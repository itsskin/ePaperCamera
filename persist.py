import os
import time
import _thread

# Запись на flash идёт кусками с паузами между ними. Причина не в скорости
# (она та же), а в том, что запись на ESP32 останавливает выполнение на
# ОБОИХ ядрах: кэш инструкций общий с флешем. Архив 150КБ плюс панельная
# копия 60КБ — это около четырёх секунд, и одним куском они превращаются
# в четыре секунды полностью замороженной платы: веб не отвечает, нажатие
# кнопки не обрабатывается. Куски по 8КБ с паузой отдают управление между
# собой, и плата остаётся живой всё это время.
# 8КБ — кратно блоку файловой системы (4КБ) и подобрано замером.
# Пробовали мельче, по 2КБ: стало ВТРОЕ хуже (отклик доходил до 15с
# против 3с). Причина в том, что littlefs всё равно работает блоками по
# 4КБ, и запись меньшими порциями заставляет её перезаписывать один блок
# по нескольку раз — суммарного времени с замороженными ядрами получается
# намного больше, хотя каждая отдельная заморозка короче.
WRITE_CHUNK = 8192
# 20мс паузы на каждые 8КБ. Пробовали 150мс, чтобы уравновесить 145мс
# заморозки ядер на самой записи, — оказалось напрасной жертвой: запись
# архива выросла с 4 до 9 секунд, а нажатия и так не теряются, их
# запоминает прерывание. Измерено: кадр целиком занимал 19с, из них 9 —
# только запись.
WRITE_PAUSE_MS = 20

PHOTOS_DIR = "/photos"
# Рядом с каждым снимком лежит вторая, маленькая копия — ровно тот кадр
# 400x300, что ушёл на панель. Без неё повторный вывод приходится ужимать
# из полного кадра, а ужимать УЖЕ дизеренную картинку нельзя: в файле не
# полутона, а готовые уровни 0-3, и любой пересчёт разрушает структуру
# дизеринга (проверено на глаз — заметная каша). Стоит она 60КБ против
# 150КБ у архива. Расширение своё, чтобы копия не попадала в галерею;
# внутри обычный BMP.
PANEL_EXT = ".pan"

# Хранить в галерее полный кадр камеры или ту же картинку, что ушла на
# экран.
#
# Полный кадр — это отдельный дизеринг на 640x480 и запись 150КБ вместо
# 60КБ, а вместе — десять секунд из шестнадцати на кадр (измерено на
# плате). Всё это время плата занята, и физический спуск ждёт очереди.
# Полный кадр нужен галерее, то есть вебу, а он вторичен — поэтому по
# умолчанию храним версию для экрана.
#
# True вернёт полный кадр на 150КБ, если галерея станет важнее скорости
# спуска.
ARCHIVE_FULL_FRAME = False
SEQ_FILE = PHOTOS_DIR + "/_seq"
MIN_FREE_RATIO = 0.20  # чистим старые снимки, если свободно меньше 20%

_lock = _thread.allocate_lock()
_saving = False
# Последняя ошибка фонового потока. Он печатает её в консоль, но у платы
# на батарее консоли нет — поэтому держим здесь и показываем в /status.
last_error = ""

# Кадр, ожидающий обработки, пока занят предыдущий. Слот один: если
# нажать ещё раз, свежий кадр вытесняет ожидающий — на экран человек
# хочет видеть последний снимок, а не тот, что был две попытки назад.
#
# Раньше занятость означала молчаливый отказ, и это выглядело так:
# светодиод загорается, кадр снят, а экран не обновляется вовсе. Фоновая
# работа после снимка идёт около десяти секунд, так что попасть в занятое
# окно проще, чем не попасть.
_pending = None

# Когда начался текущий кадр и на каком он этапе. Нужно, чтобы застрявший
# поток было видно снаружи: если он умрёт, не сняв флаг занятости, всё
# последующее будет молча вставать в очередь навсегда.
_started_ms = 0
_stage = "покой"


def state():
    busy = _saving
    return {
        "saving": busy,
        "queued": _pending is not None,
        "stage": _stage,
        "render_ms": time.ticks_diff(time.ticks_ms(), _started_ms) if busy else 0,
    }


def _mark(stage):
    global _stage
    _stage = stage


# Буферы для архива живут всё время работы платы и переиспользуются.
#
# Выделять их на каждый кадр дорого не памятью (её в PSRAM много), а
# сборщиком мусора: полный кадр требует 600КБ под буфер ошибок, столько
# же временных байт и 150КБ под сам BMP, и на таком выделении MicroPython
# запускает полную сборку по всей куче. Она идёт около двух секунд, ничему
# не уступает управление, и нажатие кнопки в это окно теряется целиком.
#
# HW-замерено: без архива худший отклик платы 0.4с, с архивом — 2.9с, и
# провалов ровно столько, сколько крупных выделений.
#
# Только для архива: превью в вебе считаются в другом потоке, и общий
# буфер они бы затёрли друг другу.
_buffers = {}


def _archive_buffers(n_pixels, bmp_size):
    import array

    bufs = _buffers
    if bufs.get("n") != n_pixels:
        bufs["work"] = array.array("H", bytes(2 * n_pixels))
        bufs["out"] = bytearray(n_pixels)
        bufs["n"] = n_pixels
    if len(bufs.get("bmp") or b"") < bmp_size:
        bufs["bmp"] = bytearray(bmp_size)
    return bufs["work"], bufs["out"], bufs["bmp"]


def _ensure_dir():
    try:
        os.mkdir(PHOTOS_DIR)
    except OSError:
        pass  # уже существует


def _next_seq():
    try:
        with open(SEQ_FILE) as f:
            n = int(f.read().strip()) + 1
    except (OSError, ValueError):
        n = 1
    with open(SEQ_FILE, "w") as f:
        f.write(str(n))
    return n


def _free_ratio():
    s = os.statvfs("/")
    total = s[2]
    free = s[3]
    return free / total if total else 1.0


def list_photos():
    """Имена снимков от новых к старым — в таком порядке их и выбирают
    в вебе. Имя = zero-padded номер, так что сортировка строк совпадает
    с сортировкой по времени."""
    try:
        names = [n for n in os.listdir(PHOTOS_DIR) if n.endswith(".bmp")]
    except OSError:
        return []
    names.sort(reverse=True)
    return names


def path_for(name):
    """Путь к снимку по имени из list_photos. Имя приходит из веба,
    поэтому пускаем только те, что реально лежат в каталоге — никаких
    "../" и абсолютных путей."""
    if name not in list_photos():
        return None
    return PHOTOS_DIR + "/" + name


def delete_photo(name):
    """Удаляет снимок из истории. Имя проверяется через path_for — из веба
    приходит произвольная строка."""
    path = path_for(name)
    if path is None:
        return False
    if not _remove_with_panel(path):
        print("delete error:", path)
        return False
    return True


def _write_chunked(path, data):
    """Пишет файл кусками с паузами — чтобы плата не замирала целиком на
    всё время записи (см. WRITE_CHUNK)."""
    mv = memoryview(data)
    total = len(mv)
    with open(path, "wb") as f:
        off = 0
        while off < total:
            f.write(mv[off:off + WRITE_CHUNK])
            off += WRITE_CHUNK
            if off < total:
                time.sleep_ms(WRITE_PAUSE_MS)


def _panel_path(bmp_path):
    return bmp_path[:-4] + PANEL_EXT


def _remove_with_panel(bmp_path):
    ok = True
    try:
        os.remove(bmp_path)
    except OSError:
        ok = False
    try:
        os.remove(_panel_path(bmp_path))
    except OSError:
        pass  # у старых снимков копии нет — это нормально
    return ok


def _oldest_photos():
    names = [n for n in os.listdir(PHOTOS_DIR) if n.endswith(".bmp")]
    names.sort()  # zero-padded seq в имени -> сортировка по числу = по возрасту
    return names


def _cleanup_if_low_space():
    while _free_ratio() < MIN_FREE_RATIO:
        oldest = _oldest_photos()
        if not oldest:
            break
        if not _remove_with_panel(PHOTOS_DIR + "/" + oldest[0]):
            break


def _worker(args):
    """Обрабатывает кадр, а потом — тот, что успел встать в очередь, не
    выходя из потока: заново поднимать поток на каждый кадр незачем."""
    global _saving, _pending, _started_ms
    while True:
        try:
            _started_ms = time.ticks_ms()
            _render_one(*args)
        except Exception as e:
            global last_error
            last_error = "render/save: %r" % e
            print("background render/save error:", repr(e))
        with _lock:
            if _pending is None:
                _saving = False
                _mark("покой")
                return
            args = _pending
            _pending = None


def _render_one(raw_y, src_w, src_h, dst_w, dst_h, label, save):
    # Всё тяжёлое — ресайз, дизеринг, вывод на панель, упаковка в BMP,
    # запись на flash, очистка — здесь, а не в вызывающем потоке:
    # пользователь должен увидеть снимок в вебе сразу после захвата,
    # а не ждать обновления e-paper (2.4с ч/б, 5.6с в 4 градациях) и
    # записи файла.
    #
    # Порядок внутри тоже не случайный: сначала панель, потом flash.
    # Панель — это, собственно, и есть камера, а запись на flash
    # физически замораживает оба ядра (общий с флешем кэш инструкций),
    # так что она должна быть последней.
    #
    # На flash кладём ровно то, что ушло на панель, а не отдельный
    # вариант с порогом, как раньше: история должна совпадать с тем,
    # что человек увидел на экране.
    try:
        import dither
        import epaper
        from bmp import gray4_to_bmp

        # 1. Панель: кадр обрезается по краям до 400x300 и получает подпись.
        _mark("дизеринг для панели")
        cropped = dither.resize_crop_nearest(raw_y, src_w, src_h, dst_w, dst_h)
        panel = dither.floyd_steinberg_4g(cropped, dst_w, dst_h)
        if label:
            epaper.overlay_text(panel, label, fg=0, bg=3)
        _mark("обновление панели")
        epaper.show_4g(panel)
        panel_img = gray4_to_bmp(panel, dst_w, dst_h) if save else None
        # gc.collect() здесь раньше стоял, и это была ошибка: с кучей в
        # несколько мегабайт PSRAM полная сборка идёт около двух секунд и
        # ничему не уступает управление. Три таких вызова в этом потоке
        # давали три окна, в которые терялось нажатие кнопки. Ссылки
        # снимаем через del, а собирать мусор MicroPython умеет сам —
        # он делает это при нехватке памяти на очередное выделение.
        del cropped, panel

        # 2. История: полный кадр камеры, без обрезки под панель и без
        # подписи — это архив снимка, а не копия того, что на стекле.
        # 640x480 в 4 градациях занимает 150КБ против 60КБ у обрезанного
        # 400x300, но обрезать архив под конкретную панель незачем.
        if save and ARCHIVE_FULL_FRAME:
            _mark("дизеринг архива")
            n = src_w * src_h
            # 4 бита на пиксель плюс заголовок с палитрой
            bmp_size = 54 + 64 + ((src_w * 4 + 31) // 32) * 4 * src_h
            work, out, bmp = _archive_buffers(n, bmp_size)
            full = dither.floyd_steinberg_4g(raw_y, src_w, src_h, work, out)
            img = gray4_to_bmp(full, src_w, src_h, bmp)
            del full
        elif save:
            # Готовая картинка для экрана уже посчитана выше — она же и
            # идёт в галерею. Отдельной панельной копии тогда не нужно:
            # архив ей и является.
            img = panel_img
            panel_img = None

            _ensure_dir()
            # Чистим и ДО записи: если места уже впритык, запись просто
            # не пройдёт, и чистить будет нечего и незачем.
            _cleanup_if_low_space()
            seq = _next_seq()
            path = "%s/%06d.bmp" % (PHOTOS_DIR, seq)
            _mark("запись архива")
            _write_chunked(path, img)
            del img
            if panel_img is not None:
                _write_chunked(_panel_path(path), panel_img)
            del panel_img
            _cleanup_if_low_space()
    finally:
        pass


def _show_worker(path):
    global _saving
    try:
        import epaper
        from bmp import load_bmp_pixels

        pan = _panel_path(path)
        try:
            os.stat(pan)
            path = pan  # точная копия того, что уже было на панели
        except OSError:
            pass
        buf, w, h, mode = load_bmp_pixels(path)
        if mode != "4g" or (w, h) != (epaper.WIDTH, epaper.HEIGHT):
            # Либо чужой размер, либо старый чёрно-белый снимок, снятый до
            # того, как остался единственный алгоритм.
            #
            # Ближайшим соседом дизеренную картинку ужимать нельзя —
            # пересчёт разрушает структуру точек. Усредняем по площади
            # (это возвращает полутона, закодированные плотностью точек) и
            # дизерим заново. Для чёрно-белых то же усреднение переводит
            # их в полутона, а дизеринг — в четыре уровня панели.
            import dither
            gray = dither.resize_crop_box(
                buf, w, h, epaper.WIDTH, epaper.HEIGHT, 85 if mode == "4g" else 1)
            buf = dither.floyd_steinberg_4g(gray, epaper.WIDTH, epaper.HEIGHT)
            del gray
        epaper.show_4g(buf)
    except Exception as e:
        global last_error
        last_error = "show: %r" % e
        print("show saved error:", repr(e))
    finally:
        with _lock:
            _saving = False


def show_saved_background(path):
    """Выводит на панель уже сохранённый снимок. Как и съёмка — в фоне:
    обновление e-paper это 2.4-5.6с, держать на них HTTP-ответ незачем."""
    global _saving
    with _lock:
        if _saving:
            global last_error
            last_error = "занято: панель обновляется"
            print("panel busy, skipping show request")
            return False
        _saving = True
    _thread.start_new_thread(_show_worker, (path,))
    return True


def render_and_save_background(raw_y, src_w, src_h, dst_w, dst_h,
                               label="", save=True):
    """Выводит кадр на e-paper и (если save) кладёт копию в историю на
    flash, не блокируя вызывающего ничем из этого. Если предыдущая такая
    задача ещё не закончилась, этот кадр просто пропускается (best-effort):
    экран всё равно физически занят предыдущим обновлением.

    save=False нужен для перерисовки уже снятого кадра в другом режиме —
    сам снимок при этом тот же, плодить в истории его копии незачем."""
    global _saving, _pending
    args = (raw_y, src_w, src_h, dst_w, dst_h, label, save)
    with _lock:
        if _saving:
            _pending = args
            print("занято, кадр поставлен в очередь")
            return "queued"
        _saving = True
    _thread.start_new_thread(_worker, (args,))
    return True
