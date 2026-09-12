# Обновление кода платы по сети с GitHub: скачивает файлы, сверяет
# sha256 и заменяет локальные. Запускается вручную, кнопкой в веб-
# интерфейсе — автоматическое обновление в фоне означало бы, что
# сломанный релиз уносит камеру в никуда без присмотра.
#
# Источников два, и они независимы: raw.githubusercontent.com и
# cdn.jsdelivr.net (зеркало того же репозитория). Российские провайдеры
# блокируют их по отдельности и непредсказуемо — если первый не ответил,
# идём ко второму, а не считаем обновление недоступным.
#
# ota_manifest.json содержит {"version": int, "files": {"путь": "sha256"}}
# и генерируется на компьютере (tools/gen_ota_manifest.py), коммитится
# вместе с кодом. Версия, которая СТОИТ на плате, лежит отдельно в
# /ota_version.txt: держать её в самом коде нельзя — тогда её было бы
# негде взять до того, как код обновился.
#
# HTTP-клиента в прошивке нет, поэтому запрос собран на сокетах вручную.
# Сертификат не проверяется намеренно: подлинность даёт sha256 из
# манифеста, а он приходит по тому же каналу, что и файлы, — то есть от
# подмены канала спасает не TLS, а то, что манифест и файлы должны
# сойтись между собой.

import os
import socket
import binascii

try:
    import ussl as ssl
except ImportError:
    import ssl

try:
    import uhashlib as hashlib
except ImportError:
    import hashlib

try:
    import ujson as json
except ImportError:
    import json

REPO = "itsskin/ePaperCamera"
BRANCH = "main"
OTA_SOURCES = (
    "https://raw.githubusercontent.com/%s/%s/" % (REPO, BRANCH),
    "https://cdn.jsdelivr.net/gh/%s@%s/" % (REPO, BRANCH),
)

VERSION_PATH = "/ota_version.txt"
MANIFEST_TIMEOUT_SEC = 10
FILE_TIMEOUT_SEC = 20


class OtaError(Exception):
    pass


def current_version():
    try:
        with open(VERSION_PATH) as f:
            return int(f.read().strip())
    except (OSError, ValueError):
        # Ни разу не обновлялись — версия 0, любой манифест будет новее.
        return 0


def _set_current_version(v):
    with open(VERSION_PATH, "w") as f:
        f.write(str(v))


def _split_url(url):
    if not url.startswith("https://"):
        raise OtaError("только https: %s" % url)
    host, _, path = url[8:].partition("/")
    return host, "/" + path


def _get(url, timeout, _redirects=2):
    """Один GET по HTTPS. HTTP/1.0 с Connection: close нарочно: так сервер
    отдаёт тело целиком и без chunked-кодирования, которое пришлось бы
    разбирать вручную."""
    host, path = _split_url(url)
    ai = socket.getaddrinfo(host, 443)[0]
    s = socket.socket(ai[0], ai[1], ai[2])
    s.settimeout(timeout)
    try:
        s.connect(ai[-1])
        s = ssl.wrap_socket(s, server_hostname=host)
        # Cache-Control против промежуточных кэшей. Сеть доставки самого
        # GitHub его не слушается: свежий коммит становится виден в
        # raw.githubusercontent через несколько минут, а в jsDelivr и
        # вовсе через часы. Так что после git push обновление появляется
        # на плате не сразу — это нормально и не признак поломки.
        s.write(b"GET %s HTTP/1.0\r\nHost: %s\r\n"
                b"User-Agent: ePaperCamera-ESP32/1.0\r\n"
                b"Cache-Control: no-cache\r\nPragma: no-cache\r\n"
                b"Connection: close\r\n\r\n" % (path.encode(), host.encode()))
        chunks = []
        while True:
            part = s.read(1024)
            if not part:
                break
            chunks.append(part)
    finally:
        try:
            s.close()
        except Exception:
            pass

    raw = b"".join(chunks)
    head, _, body = raw.partition(b"\r\n\r\n")
    if not head:
        raise OtaError("пустой ответ от %s" % host)
    status = int(head.split(None, 2)[1])
    if status in (301, 302, 303, 307, 308) and _redirects > 0:
        for line in head.split(b"\r\n"):
            if line.lower().startswith(b"location:"):
                return _get(line.split(b":", 1)[1].strip().decode(),
                            timeout, _redirects - 1)
        raise OtaError("редирект без Location от %s" % host)
    if status != 200:
        raise OtaError("HTTP %d от %s%s" % (status, host, path))
    return body


def check():
    """Опрашивает источники по очереди. Первому, кто ответил, доверяем:
    оба — зеркала одного репозитория, и переходить ко второму осмысленно
    только если первый вообще молчит, а не если сказал "обновлений нет".

    Возвращает словарь с текущей и доступной версией, манифестом и
    сработавшим источником. Бросает OtaError, если молчат все."""
    cur = current_version()
    errors = []
    for base in OTA_SOURCES:
        try:
            manifest = json.loads(_get(base + "ota_manifest.json",
                                       MANIFEST_TIMEOUT_SEC))
        except Exception as exc:
            print("ota: источник недоступен (%s): %r" % (base, exc))
            errors.append("%s: %r" % (base, exc))
            continue
        available = manifest.get("version", 0)
        return {
            "current_version": cur,
            "available_version": available,
            "update_available": available > cur,
            "manifest": manifest,
            "source": base,
        }
    raise OtaError("ни один источник не ответил: " + "; ".join(errors))


def _local_hash(path):
    """sha256 файла на плате, или None если файла нет. Нужно, чтобы не
    качать по сети то, что и так актуально: каждый файл — отдельное
    TLS-соединение, а меняются в релизе обычно единицы файлов."""
    try:
        with open(path, "rb") as f:
            h = hashlib.sha256()
            while True:
                chunk = f.read(512)
                if not chunk:
                    break
                h.update(chunk)
            return binascii.hexlify(h.digest()).decode()
    except OSError:
        return None


def apply(manifest, base_url, progress=None):
    """Качает и проверяет ВСЕ файлы во временные копии (*.ota_new) прежде
    чем тронуть хоть один настоящий. Если на середине оборвётся сеть,
    плата останется целиком на старой рабочей версии, а не в смеси
    старого и нового — для камеры, которую держат в руках вне дома, это
    единственный приемлемый вариант.

    Голый except, а не except Exception: в этой прошивке
    KeyboardInterrupt (Ctrl-C из диагностической сессии) не ловится
    except Exception, и недокачанные .ota_new остались бы на флеше
    мусором навсегда."""
    files = manifest["files"]
    total = len(files)
    downloaded = []
    try:
        for i, rel_path in enumerate(sorted(files.keys())):
            expected = files[rel_path]
            if progress:
                progress(i, total, rel_path)
            dest = "/" + rel_path
            if _local_hash(dest) == expected:
                continue
            data = _get(base_url + rel_path, FILE_TIMEOUT_SEC)
            actual = binascii.hexlify(hashlib.sha256(data).digest()).decode()
            if actual != expected:
                raise OtaError("%s: хэш не совпал (ждали %s, получили %s)"
                               % (rel_path, expected, actual))
            tmp = dest + ".ota_new"
            with open(tmp, "wb") as f:
                f.write(data)
            downloaded.append((tmp, dest))
    except:
        for tmp, _dest in downloaded:
            try:
                os.remove(tmp)
            except OSError:
                pass
        raise

    if progress:
        progress(total, total, "применяю")
    # Всё скачано и проверено. Переименование локальное и быстрое —
    # оборваться на середине от плохой сети уже не может.
    for tmp, dest in downloaded:
        os.rename(tmp, dest)
    _set_current_version(manifest["version"])
    return len(downloaded)
