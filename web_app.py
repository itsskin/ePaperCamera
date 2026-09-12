import socket


def _read_request_line_and_headers(cl):
    req_line = cl.readline()
    if not req_line:
        return None, None
    headers = {}
    while True:
        line = cl.readline()
        if not line or line == b"\r\n":
            break
        if b":" in line:
            k, v = line.split(b":", 1)
            headers[k.strip().lower()] = v.strip()
    return req_line, headers


def _parse_request_line(req_line):
    parts = req_line.decode().split()
    method, path = parts[0], parts[1]
    query = {}
    if "?" in path:
        path, qs = path.split("?", 1)
        for pair in qs.split("&"):
            if "=" in pair:
                k, v = pair.split("=", 1)
                query[_unquote(k)] = _unquote(v)
    return method, path, query


def _unquote(s):
    s = s.replace("+", " ")
    out = ""
    i = 0
    while i < len(s):
        if s[i] == "%" and i + 2 < len(s):
            out += chr(int(s[i + 1:i + 3], 16))
            i += 3
        else:
            out += s[i]
            i += 1
    return out


# Вызывается между порциями при отдаче ответа. Нужно потому, что отдача
# 150КБ по Wi-Fi занимает десятки секунд, и всё это время главный поток
# занят — а физическая кнопка опрашивается как раз в нём (HW-замерено:
# без этого промежуток между опросами доходил до 55 секунд, и нажатие
# срабатывало с такой же задержкой).
#
# Колбэк должен только НАБЛЮДАТЬ состояние кнопки, но не выполнять
# действие: выполнять съёмку посреди недоотданного ответа — верный способ
# получить трудноуловимые гонки.
_tick = None


def _sendall(cl, data):
    mv = memoryview(data)
    sent = 0
    total = len(mv)
    while sent < total:
        n = cl.send(mv[sent:])
        if n is None:
            continue
        sent += n
        if _tick is not None:
            _tick()


MAX_BODY = 1024 * 1024


def _with_charset(content_type):
    """Дописывает кодировку в тип содержимого.

    Без неё браузер читает UTF-8 как однобайтовую кодировку, и русский
    текст превращается в кракозябры. На главной странице это незаметно —
    там кодировка объявлена мета-тегом внутри HTML, — а короткие ответы
    (подтверждения, сообщения об ошибках) такого тега не несут, и спасает
    их только заголовок."""
    if content_type.startswith("text/") and "charset" not in content_type:
        return content_type + "; charset=utf-8"
    return content_type


def _read_body(cl_file, headers):
    """Читает тело запроса по Content-Length. Сокет отдаёт данные кусками
    (особенно 150КБ по Wi-Fi), поэтому читаем в цикле, а не одним read."""
    try:
        length = int(headers.get(b"content-length", b"0"))
    except ValueError:
        return None
    if length <= 0 or length > MAX_BODY:
        return None
    buf = bytearray(length)
    mv = memoryview(buf)
    got = 0
    while got < length:
        chunk = cl_file.read(min(4096, length - got))
        if not chunk:
            return None
        mv[got:got + len(chunk)] = chunk
        got += len(chunk)
    return buf


def run_server(routes, port=80, on_idle=None, idle_interval_sec=1.0, on_tick=None):
    """routes: dict {(method, path): handler(query, headers, body) -> (status, content_type, body_bytes)}

    on_idle вызывается примерно раз в idle_interval_sec, когда нет входящих
    запросов — иначе цикл вечно висит в accept() и между запросами вообще
    ничего не может делать (нужно, например, чтобы гасить камеру по простою)."""
    global _tick
    _tick = on_tick
    addr = socket.getaddrinfo("0.0.0.0", port)[0][-1]
    s = socket.socket()
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(addr)
    s.listen(2)
    if on_idle is not None:
        s.settimeout(idle_interval_sec)
    print("web server listening on port", port)
    while True:
        cl = None
        try:
            try:
                cl, remote_addr = s.accept()
            except OSError:
                # Таймаут accept (входящих запросов нет) — это не ошибка,
                # а наша периодическая точка для фоновых задач.
                if on_idle is not None:
                    on_idle()
                continue
            # Клиентский сокет может унаследовать таймаут слушающего —
            # снимаем его: отдача больших картинок по медленному Wi-Fi
            # легально занимает десятки секунд (HW-замерено: 9.5-60 КБ/с).
            try:
                cl.settimeout(None)
            except Exception:
                pass
            cl.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            cl_file = cl.makefile("rwb", 0)
            req_line, headers = _read_request_line_and_headers(cl_file)
            if not req_line:
                cl.close()
                continue
            method, path, query = _parse_request_line(req_line)
            handler = routes.get((method, path))
            if handler is None:
                body = b"Not found"
                cl.send(b"HTTP/1.0 404 Not Found\r\nContent-Length: %d\r\n\r\n" % len(body))
                cl.send(body)
            else:
                req_body = _read_body(cl_file, headers) if method == "POST" else None
                status, content_type, body = handler(query, headers, req_body)
                if isinstance(body, str):
                    # body — путь к файлу на flash, стримим чанками (не
                    # грузим целиком в память — актуально для 5MP снимков)
                    import os
                    size = os.stat(body)[6]
                    header = (
                        "HTTP/1.0 %d OK\r\nContent-Type: %s\r\nContent-Length: %d\r\nConnection: close\r\n\r\n"
                        % (status, _with_charset(content_type), size)
                    )
                    _sendall(cl, header.encode())
                    with open(body, "rb") as f:
                        while True:
                            chunk = f.read(4096)
                            if not chunk:
                                break
                            _sendall(cl, chunk)
                            if on_tick is not None:
                                on_tick()
                else:
                    header = (
                        "HTTP/1.0 %d OK\r\nContent-Type: %s\r\nContent-Length: %d\r\nConnection: close\r\n\r\n"
                        % (status, _with_charset(content_type), len(body))
                    )
                    _sendall(cl, header.encode())
                    _sendall(cl, body)
        except Exception as e:
            print("request error:", repr(e))
        finally:
            if cl:
                try:
                    cl.close()
                except Exception:
                    pass
