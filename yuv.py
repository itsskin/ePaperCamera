import micropython


@micropython.viper
def _extract_y(dst: ptr8, src: ptr8, n: int):
    for i in range(n):
        dst[i] = src[i * 2]


def extract_y(buf, width, height):
    """YUYV -> чистая яркость (Y), тот же плоский grayscale-буфер, что
    ожидают dither.py/bmp.py — Y лежит в чётных байтах YUYV-потока."""
    n = width * height
    out = bytearray(n)
    _extract_y(out, buf, n)
    return out


@micropython.viper
def _yuv422_row_to_bgr(dst: ptr8, src: ptr8, src_off: int, width: int):
    """YUYV -> 24-бит BGR (для BMP). Стандартная BT.601 формула через
    целочисленные коэффициенты (x256), без плавающей точки."""
    pairs = width // 2
    for p in range(pairs):
        so = src_off + p * 4
        y0 = src[so]
        u = src[so + 1] - 128
        y1 = src[so + 2]
        v = src[so + 3] - 128

        rd = (359 * v) >> 8
        gd = (88 * u + 183 * v) >> 8
        bd = (454 * u) >> 8

        do = p * 6

        r = y0 + rd
        g = y0 - gd
        b = y0 + bd
        if r < 0:
            r = 0
        elif r > 255:
            r = 255
        if g < 0:
            g = 0
        elif g > 255:
            g = 255
        if b < 0:
            b = 0
        elif b > 255:
            b = 255
        dst[do] = b
        dst[do + 1] = g
        dst[do + 2] = r

        r = y1 + rd
        g = y1 - gd
        b = y1 + bd
        if r < 0:
            r = 0
        elif r > 255:
            r = 255
        if g < 0:
            g = 0
        elif g > 255:
            g = 255
        if b < 0:
            b = 0
        elif b > 255:
            b = 255
        dst[do + 3] = b
        dst[do + 4] = g
        dst[do + 5] = r


def yuv422_to_bmp(buf, width, height):
    """YUYV (esp32-camera PixelFormat.YUV422) -> 24-бит BMP (bottom-up).
    Обходит сломанный RGB565-конвертер этой библиотеки для OV5640
    (HW-подтверждено: радужные артефакты на плавных градиентах при
    PixelFormat.RGB565, хотя биты извлекались математически верно —
    сам RGB565-конвертер сенсора/библиотеки даёт некорректные значения).
    YUV422 же даёт чистые, плавные Y/U/V (HW-подтверждено) — конвертируем
    в RGB сами."""
    row_size = (width * 3 + 3) & ~3
    pixel_data_size = row_size * height
    file_size = 54 + pixel_data_size

    out = bytearray(file_size)
    out[0:2] = b"BM"
    out[2:6] = file_size.to_bytes(4, "little")
    out[10:14] = (54).to_bytes(4, "little")
    out[14:18] = (40).to_bytes(4, "little")
    out[18:22] = width.to_bytes(4, "little")
    out[22:26] = height.to_bytes(4, "little")
    out[26:28] = (1).to_bytes(2, "little")
    out[28:30] = (24).to_bytes(2, "little")
    out[34:38] = pixel_data_size.to_bytes(4, "little")

    for y in range(height):
        src_off = (height - 1 - y) * width * 2
        dst_off = 54 + y * row_size
        _yuv422_row_to_bgr(memoryview(out)[dst_off:], buf, src_off, width)
    return out
