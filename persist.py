import os
import _thread

PHOTOS_DIR = "/photos"
# Рядом с каждым снимком лежит вторая, маленькая копия — ровно тот кадр
# 400x300, что ушёл на панель. Без неё повторный вывод приходится ужимать
# из полного кадра, а ужимать УЖЕ дизеренную картинку нельзя: в файле не
# полутона, а готовые уровни 0-3, и любой пересчёт разрушает структуру
# дизеринга (проверено на глаз — заметная каша). Стоит она 60КБ против
# 150КБ у архива. Расширение своё, чтобы копия не попадала в галерею;
# внутри обычный BMP.
PANEL_EXT = ".pan"
SEQ_FILE = PHOTOS_DIR + "/_seq"
MIN_FREE_RATIO = 0.20  # чистим старые снимки, если свободно меньше 20%

_lock = _thread.allocate_lock()
_saving = False


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


def photo_mode(name):
    """Режим снимка — по глубине BMP: 4 бита на пиксель это 4 градации,
    1 бит — ч/б. Читаем заголовок, а не судим по размеру файла: размер
    совпадает только пока разрешение панели одно и то же."""
    try:
        with open(PHOTOS_DIR + "/" + name, "rb") as f:
            hdr = f.read(30)
        return "4g" if int.from_bytes(hdr[28:30], "little") == 4 else "bw"
    except OSError:
        return "bw"


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


def _worker(raw_y, src_w, src_h, dst_w, dst_h, mode, label, save):
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
    global _saving
    try:
        import gc
        import dither
        import epaper
        from bmp import binary_to_bmp, gray4_to_bmp

        # 1. Панель: кадр обрезается по краям до 400x300 и получает подпись.
        cropped = dither.resize_crop_nearest(raw_y, src_w, src_h, dst_w, dst_h)
        if mode == "4g":
            panel = dither.floyd_steinberg_4g(cropped, dst_w, dst_h)
            if label:
                epaper.overlay_text(panel, label, fg=0, bg=3)
            epaper.show_4g(panel)
            panel_img = gray4_to_bmp(panel, dst_w, dst_h) if save else None
        else:
            panel = dither.floyd_steinberg(cropped, dst_w, dst_h)
            if label:
                epaper.overlay_text(panel, label, fg=0, bg=255)
            epaper.show_bw(panel)
            panel_img = binary_to_bmp(panel, dst_w, dst_h) if save else None
        del cropped, panel
        gc.collect()

        # 2. История: полный кадр камеры, без обрезки под панель и без
        # подписи — это архив снимка, а не копия того, что на стекле.
        # 640x480 в 4 градациях занимает 150КБ против 60КБ у обрезанного
        # 400x300, но обрезать архив под конкретную панель незачем.
        if save:
            if mode == "4g":
                full = dither.floyd_steinberg_4g(raw_y, src_w, src_h)
                img = gray4_to_bmp(full, src_w, src_h)
            else:
                full = dither.floyd_steinberg(raw_y, src_w, src_h)
                img = binary_to_bmp(full, src_w, src_h)
            del full
            gc.collect()

            _ensure_dir()
            # Чистим и ДО записи: если места уже впритык, запись просто
            # не пройдёт, и чистить будет нечего и незачем.
            _cleanup_if_low_space()
            seq = _next_seq()
            path = "%s/%06d.bmp" % (PHOTOS_DIR, seq)
            with open(path, "wb") as f:
                f.write(img)
            del img
            gc.collect()
            with open(_panel_path(path), "wb") as f:
                f.write(panel_img)
            del panel_img
            gc.collect()
            _cleanup_if_low_space()
    except Exception as e:
        print("background render/save error:", repr(e))
    finally:
        with _lock:
            _saving = False


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
        if (w, h) != (epaper.WIDTH, epaper.HEIGHT):
            # Старый снимок, сохранённый до появления панельной копии.
            # Ближайшим соседом такую картинку ужимать нельзя — она уже
            # дизеренная, и пересчёт разрушает структуру точек. Усредняем
            # по площади (это возвращает полутона, закодированные
            # плотностью точек) и дизерим заново под размер панели.
            import dither
            gray = dither.resize_crop_box(
                buf, w, h, epaper.WIDTH, epaper.HEIGHT, 85 if mode == "4g" else 1)
            if mode == "4g":
                buf = dither.floyd_steinberg_4g(gray, epaper.WIDTH, epaper.HEIGHT)
            else:
                buf = dither.floyd_steinberg(gray, epaper.WIDTH, epaper.HEIGHT)
            del gray
        if mode == "4g":
            epaper.show_4g(buf)
        else:
            epaper.show_bw(buf)
    except Exception as e:
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
            print("panel busy, skipping show request")
            return False
        _saving = True
    _thread.start_new_thread(_show_worker, (path,))
    return True


def render_and_save_background(raw_y, src_w, src_h, dst_w, dst_h, mode="bw",
                               label="", save=True):
    """Выводит кадр на e-paper и (если save) кладёт копию в историю на
    flash, не блокируя вызывающего ничем из этого. Если предыдущая такая
    задача ещё не закончилась, этот кадр просто пропускается (best-effort):
    экран всё равно физически занят предыдущим обновлением.

    save=False нужен для перерисовки уже снятого кадра в другом режиме —
    сам снимок при этом тот же, плодить в истории его копии незачем."""
    global _saving
    with _lock:
        if _saving:
            print("previous render/save still running, skipping this one")
            return False
        _saving = True
    _thread.start_new_thread(_worker, (raw_y, src_w, src_h, dst_w, dst_h, mode, label, save))
    return True
