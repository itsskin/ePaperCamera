import micropython


@micropython.viper
def _copy_row(dst: ptr8, dst_off: int, src: ptr8, src_off: int, width: int):
    """Быстрая построчная копия — HW-подтверждено, что обычный срез
    bytearray[a:b] = memoryview_из_камеры[a:b] в этой прошивке ОЧЕНЬ
    медленный (4.7с на VGA вместо ожидаемых миллисекунд), видимо буфер
    камеры не даёт эффективный memcpy через обычный Python-срез."""
    for x in range(width):
        dst[dst_off + x] = src[src_off + x]


@micropython.viper
def _downsample_box_row(dst: ptr8, src: ptr8, src_row0_off: int, src_width: int, out_width: int, scale: int):
    """Усредняет блок scale x scale исходных пикселей на каждый выходной —
    качественнее, чем взять через один (nearest-neighbor): сохраняет
    яркостную информацию всего блока, а не одного случайного пикселя,
    что важно перед последующим дизерингом (Floyd-Steinberg) для e-paper."""
    n = scale * scale
    for x in range(out_width):
        total = 0
        base = src_row0_off + x * scale
        for sy in range(scale):
            row_off = base + sy * src_width
            for sx in range(scale):
                total += src[row_off + sx]
        dst[x] = total // n


def grayscale_to_bmp(buf, width, height, scale=1):
    """8-бит grayscale BMP (bottom-up) с палитрой оттенков серого.
    Камера в PixelFormat.GRAYSCALE отдаёт чистые плавные значения
    (HW-подтверждено) — в отличие от PixelFormat.RGB565 у этой связки
    OV5640+библиотека, где цветовой пайплайн сенсора не настроен как
    следует (радужные артефакты на плавных градиентах). Для ч/б e-paper
    проекта цвет и не нужен.

    scale>1 усредняет блоки scale x scale при уменьшении (box-filter) —
    конечная цель всё равно 400x300 на e-paper, гонять/хранить полный
    захваченный кадр ради веб-превью смысла нет, а усреднение (не через
    один пиксель) даёт более корректный сигнал для дизеринга на входе."""
    out_width = width // scale
    out_height = height // scale
    row_size = (out_width + 3) & ~3
    palette_size = 256 * 4
    pixel_data_offset = 54 + palette_size
    pixel_data_size = row_size * out_height
    file_size = pixel_data_offset + pixel_data_size

    out = bytearray(file_size)
    out[0:2] = b"BM"
    out[2:6] = file_size.to_bytes(4, "little")
    out[10:14] = pixel_data_offset.to_bytes(4, "little")
    out[14:18] = (40).to_bytes(4, "little")
    out[18:22] = out_width.to_bytes(4, "little")
    out[22:26] = out_height.to_bytes(4, "little")
    out[26:28] = (1).to_bytes(2, "little")
    out[28:30] = (8).to_bytes(2, "little")
    out[34:38] = pixel_data_size.to_bytes(4, "little")
    out[46:50] = (256).to_bytes(4, "little")

    for i in range(256):
        off = 54 + i * 4
        out[off] = i
        out[off + 1] = i
        out[off + 2] = i

    if scale == 1:
        for y in range(out_height):
            src_off = (height - 1 - y) * width
            dst_off = pixel_data_offset + y * row_size
            _copy_row(out, dst_off, buf, src_off, out_width)
    else:
        for y in range(out_height):
            src_row0_off = (out_height - 1 - y) * scale * width
            dst_off = pixel_data_offset + y * row_size
            _downsample_box_row(memoryview(out)[dst_off:], buf, src_row0_off, width, out_width, scale)
    return out


@micropython.viper
def _pack_4bpp_row(dst: ptr8, src: ptr8, src_off: int, width: int):
    # Два пикселя в байт: старший полубайт — левый пиксель.
    for i in range(width >> 1):
        a = src[src_off + i * 2]
        b = src[src_off + i * 2 + 1]
        dst[i] = (a << 4) | b


def gray4_to_bmp(buf, width, height):
    """4-бит BMP (bottom-up) с палитрой из 4 оттенков — для показа в вебе
    того, что уйдёт на панель в 4-градационном режиме. buf содержит номера
    уровней 0..3 (как отдаёт dither.floyd_steinberg_4g). 4 бита/пиксель
    вместо 8 — вдвое меньше отдавать по Wi-Fi, а больше 16 оттенков
    формат всё равно бы не дал."""
    row_size = ((width * 4 + 31) // 32) * 4
    palette_size = 16 * 4
    pixel_data_offset = 54 + palette_size
    pixel_data_size = row_size * height
    file_size = pixel_data_offset + pixel_data_size

    out = bytearray(file_size)
    out[0:2] = b"BM"
    out[2:6] = file_size.to_bytes(4, "little")
    out[10:14] = pixel_data_offset.to_bytes(4, "little")
    out[14:18] = (40).to_bytes(4, "little")
    out[18:22] = width.to_bytes(4, "little")
    out[22:26] = height.to_bytes(4, "little")
    out[26:28] = (1).to_bytes(2, "little")
    out[28:30] = (4).to_bytes(2, "little")
    out[34:38] = pixel_data_size.to_bytes(4, "little")
    out[46:50] = (16).to_bytes(4, "little")

    for i in range(4):
        v = i * 85
        off = 54 + i * 4
        out[off] = v
        out[off + 1] = v
        out[off + 2] = v

    for y in range(height):
        src_off = (height - 1 - y) * width
        dst_off = pixel_data_offset + y * row_size
        _pack_4bpp_row(memoryview(out)[dst_off:], buf, src_off, width)
    return out


@micropython.viper
def _pack_1bpp_row(dst: ptr8, src: ptr8, src_off: int, width_bytes: int):
    for i in range(width_bytes):
        base = src_off + i * 8
        b = 0
        for bit in range(8):
            b = b << 1
            if src[base + bit] != 0:
                b = b | 1
        dst[i] = b


def binary_to_bmp(buf, width, height):
    """1-бит BMP (bottom-up, чёрно-белая палитра) для уже чисто бинарных
    (0/255) результатов дизеринга — в 8 раз меньше, чем 8-бит grayscale
    BMP, а передача по Wi-Fi этих ~120KB и была реальным тормозом веб-
    превью (HW-подтверждено: алгоритмы сами занимали 36-274мс, но полная
    загрузка картинки — 1.7-3.7с). width должен делиться на 8."""
    width_bytes = width // 8
    row_size = (width_bytes + 3) & ~3
    palette_size = 2 * 4
    pixel_data_offset = 54 + palette_size
    pixel_data_size = row_size * height
    file_size = pixel_data_offset + pixel_data_size

    out = bytearray(file_size)
    out[0:2] = b"BM"
    out[2:6] = file_size.to_bytes(4, "little")
    out[10:14] = pixel_data_offset.to_bytes(4, "little")
    out[14:18] = (40).to_bytes(4, "little")
    out[18:22] = width.to_bytes(4, "little")
    out[22:26] = height.to_bytes(4, "little")
    out[26:28] = (1).to_bytes(2, "little")
    out[28:30] = (1).to_bytes(2, "little")
    out[34:38] = pixel_data_size.to_bytes(4, "little")
    out[46:50] = (2).to_bytes(4, "little")
    # palette[0] = чёрный (уже нули по умолчанию)
    out[58] = 255
    out[59] = 255
    out[60] = 255

    for y in range(height):
        src_off = (height - 1 - y) * width
        dst_off = pixel_data_offset + y * row_size
        _pack_1bpp_row(memoryview(out)[dst_off:], buf, src_off, width_bytes)
    return out


@micropython.viper
def _unpack_1bpp_row(dst: ptr8, dst_off: int, src: ptr8, src_off: int, width_bytes: int):
    for i in range(width_bytes):
        b = int(src[src_off + i])
        base = dst_off + i * 8
        for bit in range(8):
            if (b >> (7 - bit)) & 1:
                dst[base + bit] = 255
            else:
                dst[base + bit] = 0


@micropython.viper
def _unpack_4bpp_row(dst: ptr8, dst_off: int, src: ptr8, src_off: int, width: int):
    for i in range(width >> 1):
        b = int(src[src_off + i])
        dst[dst_off + i * 2] = b >> 4
        dst[dst_off + i * 2 + 1] = b & 0x0F


def load_bmp_pixels(path):
    """Разбирает обратно BMP, который мы же и сохранили, — в тот самый
    буфер, из которого он делался: 0/255 для 1-битного, уровни 0..3 для
    4-битного. Нужно, чтобы снимок из истории можно было заново вывести
    на панель, не пересчитывая его из сырого кадра (сырых кадров мы не
    храним — они по 300КБ, а запись на flash тут дорогая).

    Возвращает (буфер, ширина, высота, режим), где режим — "bw" или "4g",
    ровно те строки, что понимают show_bw/show_4g."""
    with open(path, "rb") as f:
        hdr = f.read(54)
        if hdr[0:2] != b"BM":
            raise ValueError("не BMP: %s" % path)
        offset = int.from_bytes(hdr[10:14], "little")
        width = int.from_bytes(hdr[18:22], "little")
        height = int.from_bytes(hdr[22:26], "little")
        bpp = int.from_bytes(hdr[28:30], "little")
        f.seek(offset)
        data = f.read()

    out = bytearray(width * height)
    if bpp == 1:
        width_bytes = width // 8
        row_size = (width_bytes + 3) & ~3
        for y in range(height):
            # BMP хранится снизу вверх, буфер кадра — сверху вниз
            _unpack_1bpp_row(out, y * width, data, (height - 1 - y) * row_size, width_bytes)
        return out, width, height, "bw"
    if bpp == 4:
        row_size = ((width * 4 + 31) // 32) * 4
        for y in range(height):
            _unpack_4bpp_row(out, y * width, data, (height - 1 - y) * row_size, width)
        return out, width, height, "4g"
    raise ValueError("неподдерживаемая глубина: %d бит" % bpp)
