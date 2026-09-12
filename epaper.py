# Драйвер WeAct 4.2" (400x300, GDEY042T81 / SSD1683) — оба режима.
#
# HW-подтверждено на реальной панели: ч/б полное обновление ~2.4с,
# 4 градации ~5.6с (серая волновая форма длиннее, это нормально).
#
# Распиновка подобрана под эту плату: пины камеры, Octal-PSRAM (33-37),
# SD-карта (38-40), нативный USB (19,20) и консоль (43,44) заняты.
# BUSY специально не на strapping-пине (0, 3, 45, 46) — это единственная
# линия, которой управляет сам экран, и на strapping-пине она могла бы
# сбить режим загрузки ESP32.
#
# LUT для 4 градаций и последовательность инициализации взяты из рабочего
# драйвера именно этой панели: ZinggJM/GxEPD2_4G,
# src/gdey/GxEPD2_420_GDEY042T81.cpp (там — из референса Waveshare).

import time
import micropython
from machine import Pin, SPI

WIDTH = 400
HEIGHT = 300

PINS = {"sck": 42, "mosi": 41, "cs": 45, "dc": 48, "rst": 47, "busy": 21}

# HW-подтверждено: буфер ложится на стекло один в один, разворачивать
# ничего не надо. Пробовали зеркалить по X (казалось, что подпись из
# левого угла буфера выходит на панели справа) — на панели немедленно
# отзеркалилось всё, включая текст, читать стало нельзя. Байты и биты
# идут как в test_display.py: первый байт — левый край, бит 7 — левый
# пиксель в байте.

# 227 байт LUT + 6 байт настроек напряжений (индексы 227..232)
_LUT_4G = bytes([
    0x01, 0x0A, 0x1B, 0x0F, 0x03, 0x01, 0x01,
    0x05, 0x0A, 0x01, 0x0A, 0x01, 0x01, 0x01,
    0x05, 0x08, 0x03, 0x02, 0x04, 0x01, 0x01,
    0x01, 0x04, 0x04, 0x02, 0x00, 0x01, 0x01,
    0x01, 0x00, 0x00, 0x00, 0x00, 0x01, 0x01,
    0x01, 0x00, 0x00, 0x00, 0x00, 0x01, 0x01,
    0x01, 0x0A, 0x1B, 0x0F, 0x03, 0x01, 0x01,
    0x05, 0x4A, 0x01, 0x8A, 0x01, 0x01, 0x01,
    0x05, 0x48, 0x03, 0x82, 0x84, 0x01, 0x01,
    0x01, 0x84, 0x84, 0x82, 0x00, 0x01, 0x01,
    0x01, 0x00, 0x00, 0x00, 0x00, 0x01, 0x01,
    0x01, 0x00, 0x00, 0x00, 0x00, 0x01, 0x01,
    0x01, 0x0A, 0x1B, 0x8F, 0x03, 0x01, 0x01,
    0x05, 0x4A, 0x01, 0x8A, 0x01, 0x01, 0x01,
    0x05, 0x48, 0x83, 0x82, 0x04, 0x01, 0x01,
    0x01, 0x04, 0x04, 0x02, 0x00, 0x01, 0x01,
    0x01, 0x00, 0x00, 0x00, 0x00, 0x01, 0x01,
    0x01, 0x00, 0x00, 0x00, 0x00, 0x01, 0x01,
    0x01, 0x8A, 0x1B, 0x8F, 0x03, 0x01, 0x01,
    0x05, 0x4A, 0x01, 0x8A, 0x01, 0x01, 0x01,
    0x05, 0x48, 0x83, 0x02, 0x04, 0x01, 0x01,
    0x01, 0x04, 0x04, 0x02, 0x00, 0x01, 0x01,
    0x01, 0x00, 0x00, 0x00, 0x00, 0x01, 0x01,
    0x01, 0x00, 0x00, 0x00, 0x00, 0x01, 0x01,
    0x01, 0x8A, 0x9B, 0x8F, 0x03, 0x01, 0x01,
    0x05, 0x4A, 0x01, 0x8A, 0x01, 0x01, 0x01,
    0x05, 0x48, 0x03, 0x42, 0x04, 0x01, 0x01,
    0x01, 0x04, 0x04, 0x42, 0x00, 0x01, 0x01,
    0x01, 0x00, 0x00, 0x00, 0x00, 0x01, 0x01,
    0x01, 0x00, 0x00, 0x00, 0x00, 0x01, 0x01,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x02, 0x00, 0x00, 0x07, 0x17, 0x41, 0xA8,
    0x32, 0x30,
])

_spi = None
_cs = _dc = _rst = _busy = None

# Инициализация делается перед КАЖДЫМ выводом, а не один раз на режим.
#
# Кэширование ("режим тот же — инициализацию пропускаем") выглядело
# безобидной экономией, но на панели давало ровно то, что и должно:
# первый кадр после включения хороший, второй заметно хуже. Причина в
# том, что последовательность обновления заканчивается выключением
# аналоговой части (0x22 -> 0xCF/0xD7 гасит charge pump и осциллятор),
# а таблица волновых форм и напряжения задаются как раз в инициализации.
#
# Оба HW-проверенных теста (test_display.py, test_display_4gray.py) тоже
# делали init непосредственно перед выводом — то есть проверена именно
# такая последовательность, а не сэкономленная.
#
# Стоит это около 100мс на фоне 2.4-5.6с самого обновления.


def _pins():
    global _spi, _cs, _dc, _rst, _busy
    if _spi is not None:
        return
    # SPI(1), не SPI(2): на платах с Octal-PSRAM второй SPI валит плату
    # по watchdog (HW-подтверждено в соседнем проекте на этой же плате).
    _spi = SPI(1, baudrate=4_000_000, polarity=0, phase=0,
               sck=Pin(PINS["sck"]), mosi=Pin(PINS["mosi"]))
    _cs = Pin(PINS["cs"], Pin.OUT, value=1)
    _dc = Pin(PINS["dc"], Pin.OUT, value=0)
    _rst = Pin(PINS["rst"], Pin.OUT, value=1)
    _busy = Pin(PINS["busy"], Pin.IN)


def _wait_busy(timeout_ms=30000):
    t0 = time.ticks_ms()
    while _busy.value() == 1:
        if time.ticks_diff(time.ticks_ms(), t0) > timeout_ms:
            raise RuntimeError("EPD busy timeout")
        time.sleep_ms(10)


def _cmd(c, data=b""):
    _dc(0)
    _cs(0)
    _spi.write(bytes([c]))
    _cs(1)
    if data:
        _dc(1)
        _cs(0)
        _spi.write(data)
        _cs(1)


def _reset():
    _rst(0)
    time.sleep_ms(20)
    _rst(1)
    time.sleep_ms(20)
    _wait_busy()


def _set_window():
    # Окно RAM переустанавливаем перед КАЖДОЙ записью: указатель адреса
    # остаётся там, где его оставила предыдущая запись.
    _cmd(0x11, b"\x03")
    _cmd(0x44, bytes([0, (WIDTH - 1) // 8]))
    _cmd(0x45, bytes([0, 0, (HEIGHT - 1) & 0xFF, (HEIGHT - 1) >> 8]))
    _cmd(0x4E, bytes([0]))
    _cmd(0x4F, bytes([0, 0]))


def _init_4g():
    _reset()
    _cmd(0x12)
    _wait_busy()
    _cmd(0x0C, b"\x8B\x9C\xA4\x0F")
    _cmd(0x21, b"\x00\x00")
    _cmd(0x3C, b"\x03")
    _set_window()
    _cmd(0x32, _LUT_4G[0:227])
    _cmd(0x3F, bytes([_LUT_4G[227]]))
    _cmd(0x03, bytes([_LUT_4G[228]]))
    _cmd(0x04, bytes([_LUT_4G[229], _LUT_4G[230], _LUT_4G[231]]))
    _cmd(0x2C, bytes([_LUT_4G[232]]))


@micropython.viper
def _pack_4g(dst: ptr8, src: ptr8, n_bytes: int, mode: int):
    # src: уровни 0..3 -> бит-план, сразу инвертированный.
    #   mode 0 -> плоскость 0x26: бит = 1, если уровень >= 2
    #   mode 1 -> плоскость 0x24: бит = младший бит уровня
    for i in range(n_bytes):
        base = i * 8
        b = 0
        for k in range(8):
            b = b << 1
            v = int(src[base + k])
            if mode == 0:
                if v >= 2:
                    b = b | 1
            else:
                b = b | (v & 1)
        dst[i] = b ^ 0xFF


def overlay_text(buf, text, x=2, y=HEIGHT - 11, fg=0, bg=255):
    """Пишет строку прямо в буфер кадра 400x300 (байт на пиксель), уже
    ПОСЛЕ дизеринга — иначе текст размылся бы в точки и стал нечитаемым.

    Шрифт — встроенный в framebuf 8x8, единственный и самый мелкий, что
    есть в прошивке (своих шрифтов MicroPython не несёт).

    fg/bg — значения в той же шкале, что и сам буфер: 0/255 для ч/б,
    0..3 для 4 градаций. Подложка (bg) обязательна: без неё текст тонет
    в дизеренном фоне."""
    import framebuf

    w = 8 * len(text)
    stride = (w + 7) // 8
    tmp = bytearray(stride * 8)
    fb = framebuf.FrameBuffer(tmp, w, 8, framebuf.MONO_HLSB)
    fb.text(text, 0, 0, 1)

    if bg is not None:
        for py in range(max(0, y - 1), min(HEIGHT, y + 9)):
            row = py * WIDTH
            for px in range(max(0, x - 1), min(WIDTH, x + w + 1)):
                buf[row + px] = bg

    for ty in range(8):
        py = y + ty
        if py < 0 or py >= HEIGHT:
            continue
        row = py * WIDTH
        trow = ty * stride
        for tx in range(w):
            px = x + tx
            if px < 0 or px >= WIDTH:
                continue
            if (tmp[trow + (tx >> 3)] >> (7 - (tx & 7))) & 1:
                buf[row + px] = fg


def show_4g(levels):
    """levels: WIDTH*HEIGHT байт со значениями 0..3 (как отдаёт
    dither.floyd_steinberg_4g)."""
    _pins()
    _init_4g()
    n_bytes = WIDTH * HEIGHT // 8
    plane = bytearray(n_bytes)

    _set_window()
    _pack_4g(plane, levels, n_bytes, 0)
    _cmd(0x26, plane)

    _set_window()
    _pack_4g(plane, levels, n_bytes, 1)
    _cmd(0x24, plane)

    _cmd(0x21, b"\x88\x00")
    _cmd(0x22, b"\xCF")
    _cmd(0x20)
    _wait_busy()


def sleep():
    if _spi is not None:
        _cmd(0x10, b"\x01")
