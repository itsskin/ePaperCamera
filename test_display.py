# Тестовый вывод картинки на WeAct 4.2" (400x300, SSD1683-подобный) с
# ESP32-S3-N16R8. Пины и протокол взяты из HW-подтверждённого
# ~/ClaudeCodeProjects/SellerCounter/firmware/display/epd4in2.py (тот же тип
# платы и экрана, там 1.54"-вариант того же протокола уже проверен "живьём").

import time
from machine import Pin, SPI
import framebuf

# Распиновка подобрана под ЭТУ плату (ESP32-S3 + OV5640): пины камеры
# (4,5,6,7,8,9,10,11,12,13,15,16,17,18), Octal-PSRAM (33-37), SD-карта
# (38-40), нативный USB (19,20) и консоль (43,44) — заняты. Остаются две
# соседние группы на нижнем ряду: 21/47/48/45 и 41/42/2/1.
#
# BUSY специально не на strapping-пине (0, 3, 45, 46): это единственная
# линия, которой управляет сам экран, и на strapping-пине она могла бы
# сбить режим загрузки ESP32. Остальные пять — выходы ESP32, экран их не
# тянет, поэтому strapping-пин (45) под CS безопасен.
PINS = {"sck": 42, "mosi": 41, "cs": 45, "dc": 48, "rst": 47, "busy": 21}

WIDTH = 400
HEIGHT = 300

CMD_SW_RESET = 0x12
CMD_DRIVER_OUTPUT_CTRL = 0x01
CMD_DATA_ENTRY_MODE = 0x11
CMD_SET_RAM_X = 0x44
CMD_SET_RAM_Y = 0x45
CMD_SET_RAM_X_COUNTER = 0x4E
CMD_SET_RAM_Y_COUNTER = 0x4F
CMD_BORDER_WAVEFORM = 0x3C
CMD_TEMP_CONTROL = 0x18
CMD_TEMP_WRITE = 0x1A
CMD_WRITE_RAM_BW = 0x24
CMD_DISPLAY_UPDATE_CTRL1 = 0x21
CMD_DISPLAY_UPDATE_CTRL2 = 0x22
CMD_MASTER_ACTIVATE = 0x20

# SPI(2) валит хардварный ребут по watchdog на этой плате (Octal-PSRAM) —
# HW-подтверждено в epd1in54.py на той же плате. SPI(1) работает нормально.
spi = SPI(1, baudrate=4_000_000, polarity=0, phase=0,
          sck=Pin(PINS["sck"]), mosi=Pin(PINS["mosi"]))
cs = Pin(PINS["cs"], Pin.OUT, value=1)
dc = Pin(PINS["dc"], Pin.OUT, value=0)
rst = Pin(PINS["rst"], Pin.OUT, value=1)
busy = Pin(PINS["busy"], Pin.IN)


def reset():
    rst(0)
    time.sleep_ms(20)
    rst(1)
    time.sleep_ms(20)


def wait_busy(timeout_ms=25000):
    t0 = time.ticks_ms()
    while busy.value() == 1:
        if time.ticks_diff(time.ticks_ms(), t0) > timeout_ms:
            raise RuntimeError("EPD busy timeout")
        time.sleep_ms(10)


def cmd(c, data=b""):
    dc(0)
    cs(0)
    spi.write(bytes([c]))
    cs(1)
    if data:
        dc(1)
        cs(0)
        spi.write(data)
        cs(1)


def init():
    reset()
    wait_busy()
    cmd(CMD_SW_RESET)
    wait_busy()
    cmd(CMD_DRIVER_OUTPUT_CTRL,
        bytes([(HEIGHT - 1) & 0xFF, ((HEIGHT - 1) >> 8) & 0xFF, 0x00]))
    cmd(CMD_BORDER_WAVEFORM, bytes([0x01]))
    cmd(CMD_TEMP_CONTROL, bytes([0x80]))
    cmd(CMD_DATA_ENTRY_MODE, bytes([0x03]))


def set_window(x0, y0, x1, y1):
    cmd(CMD_SET_RAM_X, bytes([x0 // 8, x1 // 8]))
    cmd(CMD_SET_RAM_Y, bytes([y0 & 0xFF, y0 >> 8, y1 & 0xFF, y1 >> 8]))
    cmd(CMD_SET_RAM_X_COUNTER, bytes([x0 // 8]))
    cmd(CMD_SET_RAM_Y_COUNTER, bytes([y0 & 0xFF, y0 >> 8]))


def show(buf):
    set_window(0, 0, WIDTH - 1, HEIGHT - 1)
    # framebuf: 1=закрашено (чёрный текст/фигуры). Панели (по референс-
    # протоколу) нужно 0=чёрный, поэтому инвертируем перед отправкой.
    inverted = bytes(b ^ 0xFF for b in buf)
    cmd(CMD_WRITE_RAM_BW, inverted)
    cmd(CMD_DISPLAY_UPDATE_CTRL1, bytes([0x40, 0x00]))
    # Fast full update (сверено с GxEPD2 GDEY042T81)
    cmd(CMD_TEMP_WRITE, bytes([0x6E]))
    cmd(CMD_DISPLAY_UPDATE_CTRL2, bytes([0xD7]))
    cmd(CMD_MASTER_ACTIVATE)
    wait_busy()


buf = bytearray(WIDTH * HEIGHT // 8)
fb = framebuf.FrameBuffer(buf, WIDTH, HEIGHT, framebuf.MONO_HLSB)
fb.fill(0)
fb.rect(4, 4, WIDTH - 8, HEIGHT - 8, 1)
fb.rect(10, 10, WIDTH - 20, HEIGHT - 20, 1)
fb.text("ESP32-S3 + WeAct 4.2\"", 20, 30, 1)
fb.text("400x300 MicroPython test", 20, 46, 1)
fb.text("epd4in2.py driver, HW check", 20, 62, 1)
for x in range(20, WIDTH - 20, 24):
    fb.line(x, 90, WIDTH - 20 - (x - 20), HEIGHT - 20, 1)
fb.fill_rect(WIDTH - 90, HEIGHT - 90, 60, 60, 1)
fb.rect(WIDTH - 90, HEIGHT - 90, 60, 60, 0)

print("initializing panel...")
init()
print("sending frame + refreshing...")
t0 = time.ticks_ms()
show(buf)
print("done in %d ms" % time.ticks_diff(time.ticks_ms(), t0))
