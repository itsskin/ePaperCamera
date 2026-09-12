import array
import micropython

_BAYER4 = bytes([0, 8, 2, 10, 12, 4, 14, 6, 3, 11, 1, 9, 15, 7, 13, 5])
_BAYER_THRESH = bytes(((v * 2 + 1) * 255) // 32 for v in _BAYER4)
_FS_BIAS = 8192


@micropython.viper
def _resize_nearest_row(dst: ptr8, src: ptr8, src_row: int, src_width: int, dst_width: int):
    for x in range(dst_width):
        src_x = (x * src_width) // dst_width
        dst[x] = src[src_row + src_x]


def resize_nearest(buf, sw, sh, dw, dh):
    """Ближайший сосед — sw/sh не кратны dw/dh (640/400=1.6), box-filter
    с целым шагом тут не подходит. Для дизеринга-превью этого достаточно."""
    out = bytearray(dw * dh)
    mv = memoryview(out)
    for y in range(dh):
        src_y = (y * sh) // dh
        _resize_nearest_row(mv[y * dw:], buf, src_y * sw, sw, dw)
    return out


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
def _threshold_all(dst: ptr8, src: ptr8, n: int):
    for i in range(n):
        v = src[i]
        if v > 127:
            dst[i] = 255
        else:
            dst[i] = 0


def threshold(buf, w, h):
    out = bytearray(w * h)
    _threshold_all(out, buf, w * h)
    return out


@micropython.viper
def _bayer_apply(dst: ptr8, src: ptr8, thresh: ptr8, width: int, height: int):
    for y in range(height):
        row = y * width
        trow = (y & 3) * 4
        for x in range(width):
            t = thresh[trow + (x & 3)]
            v = src[row + x]
            if v > t:
                dst[row + x] = 255
            else:
                dst[row + x] = 0


def ordered_bayer(buf, w, h):
    out = bytearray(w * h)
    _bayer_apply(out, buf, _BAYER_THRESH, w, h)
    return out


@micropython.viper
def _fs_init(work: ptr16, src: ptr8, n: int, bias: int):
    for i in range(n):
        work[i] = src[i] + bias


@micropython.viper
def _fs_pass(work: ptr16, out: ptr8, width: int, height: int, bias: int):
    for y in range(height):
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
            newv = 0
            if old > 127:
                newv = 255
            out[i] = newv
            err = old - newv
            # >>4, а не //16: для степени двойки арифметический сдвиг даёт
            # то же округление вниз, но viper компилирует // на возможно
            # отрицательных значениях в вызов функции деления, а сдвиг —
            # в одну инструкцию (HW-замерено: 351мс -> см. ниже на 400x300).
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


def floyd_steinberg(buf, w, h):
    n = w * h
    # bytes(2*n), не bytes(n): array.array с bytes трактует их как СЫРОЙ
    # буфер (frombytes), а не как последовательность значений — то есть
    # 2*n байт дают ровно n элементов uint16. С bytes(n) буфер выходил
    # вдвое короче нужного, и вторая половина кадра писалась и читалась
    # за его границей (HW-подтверждено: нижняя половина превью — мусор).
    work = array.array("H", bytes(2 * n))
    _fs_init(work, buf, n, _FS_BIAS)
    out = bytearray(n)
    _fs_pass(work, out, w, h, _FS_BIAS)
    return out


@micropython.viper
def _fs4_pass(work: ptr16, out: ptr8, width: int, height: int, bias: int):
    """То же диффузионное распространение ошибки, что и в _fs_pass, но
    квантование не в 2, а в 4 уровня (0, 85, 170, 255). На выходе — НОМЕР
    уровня 0..3, именно в таком виде его ждёт панель (два бит-плана)."""
    for y in range(height):
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


def floyd_steinberg_4g(buf, w, h):
    """Возвращает буфер уровней 0..3 (не 0/255!) — панель в 4-градационном
    режиме принимает именно номер уровня, см. epaper.py."""
    n = w * h
    work = array.array("H", bytes(2 * n))
    _fs_init(work, buf, n, _FS_BIAS)
    out = bytearray(n)
    _fs4_pass(work, out, w, h, _FS_BIAS)
    return out


ALGORITHMS = {
    "none": None,
    "threshold": threshold,
    "floyd": floyd_steinberg,
    "bayer": ordered_bayer,
}
