import array
import time
import micropython

# Дизеринг считается полосами по столько строк, с уступкой управления
# между ними. Один сплошной проход по кадру 640x480 — это полторы секунды
# машинного кода, который ничем не прерывается: пока он идёт, веб-сервер
# не отвечает и нажатие кнопки теряется целиком (не откладывается — его
# просто некому заметить). Полосы режут это окно на куски ~0.1с.
#
# Границы полос проходят строго по строкам, а распространение ошибки у
# Флойда-Стейнберга идёт строка за строкой — поэтому разбиение точное,
# результат побитово тот же, что у одного прохода. Никакого шва на
# границах полос не появляется.
# 10 строк, а не 40: полоса из 40 строк считается около 120мс, и за это
# время главный поток не получает ничего. Опрос кнопки идёт как раз в
# главном потоке, поэтому полоса должна быть короче человеческого
# нажатия с большим запасом. 10 строк — это ~30мс.
BAND_ROWS = 20

# Пауза между полосами. Секунда дизеринга превращается в полторы, зато
# главный поток получает около четверти времени и успевает опросить
# кнопку. Физический спуск важнее скорости фоновой обработки.
# 2мс. Десять были перестраховкой: дизеринг архива раздувался с 1.5 до 4
# секунд, а нажатия и так не теряются — их запоминает прерывание.
BAND_PAUSE_MS = 2

# Крючок, который зовётся между полосами. Через него приложение опрашивает
# физическую кнопку: дизеринг полного кадра идёт около двух секунд, и если
# он выполняется в главном потоке (превью для веба), нажатие всё это время
# некому заметить. Модуль про кнопку ничего не знает — только зовёт, если
# дали. Колбэк обязан быть дешёвым и без побочных действий.
on_yield = None


def _yield():
    if on_yield is not None:
        on_yield()
    time.sleep_ms(BAND_PAUSE_MS)

_FS_BIAS = 8192


@micropython.viper
def _resize_nearest_row(dst: ptr8, src: ptr8, src_row: int, src_width: int, dst_width: int):
    for x in range(dst_width):
        src_x = (x * src_width) // dst_width
        dst[x] = src[src_row + src_x]


def resize_crop_nearest(buf, sw, sh, dw, dh):
    """Как resize_nearest, но обрезает по краям до нужного соотношения
    сторон вместо сжатия картинки (user: для e-paper — "с обрезанием по
    краям", веб-превью при этом остаётся несжатым/некадрированным)."""
    target_aspect = dw / dh
    src_aspect = sw / sh
    if src_aspect > target_aspect:
        crop_w = int(sh * target_aspect)
        crop_h = sh
        x_off = (sw - crop_w) // 2
        y_off = 0
    else:
        crop_w = sw
        crop_h = int(sw / target_aspect)
        x_off = 0
        y_off = (sh - crop_h) // 2

    out = bytearray(dw * dh)
    mv = memoryview(out)
    for y in range(dh):
        src_y = y_off + (y * crop_h) // dh
        src_row = src_y * sw + x_off
        _resize_nearest_row(mv[y * dw:], buf, src_row, crop_w, dw)
    return out


@micropython.viper
def _box_row(dst: ptr8, dst_off: int, src: ptr8, sw: int, x_off: int, crop_w: int,
             dw: int, y0: int, y1: int, mul: int):
    """Усредняет прямоугольник исходных пикселей на каждый выходной."""
    for x in range(dw):
        sx0 = x_off + (x * crop_w) // dw
        sx1 = x_off + ((x + 1) * crop_w) // dw
        if sx1 <= sx0:
            sx1 = sx0 + 1
        total = 0
        n = 0
        for yy in range(y0, y1):
            row = yy * sw
            for xx in range(sx0, sx1):
                total = total + int(src[row + xx])
                n = n + 1
        v = (total * mul) // n
        if v > 255:
            v = 255
        dst[dst_off + x] = v


def resize_crop_box(buf, sw, sh, dw, dh, mul=1):
    """Как resize_crop_nearest, но с усреднением, и mul поднимает шкалу.

    Нужно, чтобы вернуть полутона из УЖЕ дизеренной картинки: в ней на
    пиксель приходится не яркость, а уровень (0-3 или 0/255), и яркость
    закодирована плотностью точек. Ближайший сосед такую картинку просто
    ломает, а усреднение по площади восстанавливает исходную яркость
    настолько, насколько она вообще там осталась — после чего её можно
    честно передизерить под новый размер.

    mul=85 переводит уровни 0-3 обратно в 0-255, mul=1 оставляет как есть
    (для чёрно-белых, там уже 0/255)."""
    target_aspect = dw / dh
    src_aspect = sw / sh
    if src_aspect > target_aspect:
        crop_w = int(sh * target_aspect)
        crop_h = sh
        x_off = (sw - crop_w) // 2
        y_off = 0
    else:
        crop_w = sw
        crop_h = int(sw / target_aspect)
        x_off = 0
        y_off = (sh - crop_h) // 2

    out = bytearray(dw * dh)
    for y in range(dh):
        y0 = y_off + (y * crop_h) // dh
        y1 = y_off + ((y + 1) * crop_h) // dh
        if y1 <= y0:
            y1 = y0 + 1
        _box_row(out, y * dw, buf, sw, x_off, crop_w, dw, y0, y1, mul)
    return out


@micropython.viper
def _fs_init(work: ptr16, src: ptr8, i_from: int, i_to: int, bias: int):
    for i in range(i_from, i_to):
        work[i] = src[i] + bias


def _fs_init_banded(work, buf, w, h):
    """Та же подготовка буфера ошибок, но полосами с уступкой управления:
    сплошной проход по кадру 640x480 — ещё треть секунды, в которую
    нажатие кнопки теряется."""
    step = BAND_ROWS * w
    n = w * h
    i = 0
    while i < n:
        i2 = i + step
        if i2 > n:
            i2 = n
        _fs_init(work, buf, i, i2, _FS_BIAS)
        i = i2
        if i < n:
            _yield()


@micropython.viper
def _fs4_pass(work: ptr16, out: ptr8, width: int, height: int, y_from: int, y_to: int, bias: int):
    """То же диффузионное распространение ошибки, что и в _fs_pass, но
    квантование не в 2, а в 4 уровня (0, 85, 170, 255). На выходе — НОМЕР
    уровня 0..3, именно в таком виде его ждёт панель (два бит-плана)."""
    for y in range(y_from, y_to):
        row = y * width
        next_row = row + width
        has_next = 0
        if y + 1 < height:
            has_next = 1
        for x in range(width):
            i = row + x
            old = int(work[i]) - bias
            if old < 0:
                old = 0
            if old > 255:
                old = 255
            lvl = 0
            if old >= 213:
                lvl = 3
            elif old >= 128:
                lvl = 2
            elif old >= 43:
                lvl = 1
            out[i] = lvl
            err = old - lvl * 85
            e7 = (err * 7) >> 4
            e5 = (err * 5) >> 4
            e3 = (err * 3) >> 4
            e1 = err >> 4
            if x + 1 < width:
                work[i + 1] = work[i + 1] + e7
            if has_next:
                if x > 0:
                    work[next_row + x - 1] = work[next_row + x - 1] + e3
                work[next_row + x] = work[next_row + x] + e5
                if x + 1 < width:
                    work[next_row + x + 1] = work[next_row + x + 1] + e1


def floyd_steinberg_4g(buf, w, h, work=None, out=None):
    """Возвращает буфер уровней 0..3 (не 0/255!) — панель в 4-градационном
    режиме принимает именно номер уровня, см. epaper.py."""
    n = w * h
    # work/out можно передать снаружи и переиспользовать между кадрами.
    # Выделять их заново на каждый кадр дорого не памятью, а сборщиком:
    # array("H") на полный кадр — это 600КБ плюс столько же временных
    # bytes, и на таком выделении MicroPython запускает полную сборку
    # мусора по многомегабайтной куче PSRAM. Она идёт около двух секунд и
    # ничему не уступает управление — именно в эти окна терялось нажатие
    # кнопки (HW-замерено: без архива худший отклик 0.4с, с архивом 2.9с).
    if work is None:
        work = array.array("H", bytes(2 * n))
    _fs_init_banded(work, buf, w, h)
    if out is None:
        out = bytearray(n)
    y = 0
    while y < h:
        y2 = y + BAND_ROWS
        if y2 > h:
            y2 = h
        _fs4_pass(work, out, w, h, y, y2, _FS_BIAS)
        y = y2
        if y < h:
            _yield()
    return out
