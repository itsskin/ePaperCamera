#!/usr/bin/env python3
"""Заливка файлов проекта на плату. Запускается на маке, не на плате.

Нужен потому, что mpremote 1.28 держит скорость 115200 жёстко зашитой в
трёх местах (commands.py) и флага для неё не имеет. Транспорт при этом у
него нормальный и параметризуемый — им и пользуемся.

Скорость подбирается сама: сначала пробуем быструю, потом штатную. Это не
удобство, а страховка — плата переходит на 460800 только после того, как
boot.py на ней перенастроит консоль, а до этого (и после перепрошивки
MicroPython) она отвечает на 115200. Скрипт должен работать в обоих
состояниях, иначе после первой же неудачи до платы будет не достучаться.

    python3 deploy.py                # залить весь проект и перезагрузить
    python3 deploy.py main.py        # только указанные файлы
    python3 deploy.py --no-reset ...
    python3 deploy.py --wifi         # по сети, без USB вообще
"""

import sys
import time

from mpremote.transport_serial import SerialTransport

PORT_PREFIX = "/dev/cu.wchusbserial"
# Плата загружается на 115200 — это зашитая скорость консоли MicroPython.
# Поднимать её насовсем не стоит: mpremote 1.28 держит 115200 жёстко в
# трёх местах и флага не имеет, то есть штатный инструмент перестал бы
# подключаться вообще. Поэтому скорость поднимается только на время
# передачи и возвращается обратно перед выходом.
#
# Замерено на этом проекте (93КБ, 8 файлов, кусок 4096 Б):
#   115200 — 24.9с, 3.7 КБ/с
#   460800 — 12.7с, 7.4 КБ/с
# Ровно вдвое, а не вчетверо: половина времени уходит не на линию, а на
# круговые задержки протокола raw REPL и запись на flash (~55 КБ/с).
BASE_BAUD = 115200
FAST_BAUD = 460800

# Порядок важен: main.py последним. Он и есть точка входа, и если заливка
# оборвётся на середине, плата не стартанёт с новым main.py поверх старых
# модулей.
PROJECT_FILES = [
    "web_app.py",
    "wifi_manager.py",
    "epaper.py",
    "dither.py",
    "bmp.py",
    "persist.py",
    "yuv.py",
    "main.py",
]


BOARD_HOST = "192.168.1.105"


def deploy_over_wifi(files, host, do_reset):
    """Заливка по сети. Нужна потому, что драйвер USB-моста на маке
    регулярно залипает (перестаёт менять скорость порта), а плата при
    этом продолжает работать и отвечать по Wi-Fi."""
    import urllib.request

    total = 0
    t0 = time.time()
    for name in files:
        with open(name, "rb") as f:
            data = f.read()
        req = urllib.request.Request(
            "http://%s/push?path=%s" % (host, name), data=data, method="POST")
        with urllib.request.urlopen(req, timeout=60) as r:
            print("  %-16s %s" % (name, r.read().decode()))
        total += len(data)

    dt = time.time() - t0
    print("итого %d Б за %.1f с (%.1f КБ/с) по сети" % (total, dt, total / dt / 1024))

    if do_reset:
        try:
            urllib.request.urlopen("http://%s/reboot" % host, timeout=10).read()
        except Exception:
            pass
        print("плата перезагружена")


def find_port():
    import glob

    ports = sorted(glob.glob(PORT_PREFIX + "*"))
    if not ports:
        sys.exit("Порт не найден (%s*). Плата не подключена или завис драйвер." % PORT_PREFIX)
    return ports[0]


def connect(port, bauds=(BASE_BAUD, FAST_BAUD)):
    last = None
    for baud in bauds:
        t = None
        try:
            t = SerialTransport(port, baudrate=baud)
            t.enter_raw_repl(soft_reset=False)
            return t, baud
        except Exception as e:
            last = e
            # Закрыть обязательно: pyserial открывает порт монопольно, и
            # брошенная неудачная попытка не даёт открыть его следующей —
            # ошибка выглядит как "failed to access", будто завис драйвер.
            if t is not None:
                try:
                    t.close()
                except Exception:
                    pass
    # Ни одна скорость не подошла. Чаще всего это не плата, а залипший
    # драйвер CH34x: он перестаёт принимать смену скорости, и тогда
    # `stty -f <порт> 115200` тоже отвечает "Invalid argument". Проверить
    # можно так, лечится перезагрузкой мака — передёргивание кабеля не
    # помогает, расширение живёт в системе, а не в устройстве.
    sys.exit("Не подключиться ни на одной скорости (%s).\n"
             "Проверь драйвер:  stty -f %s 115200" % (last, port))


def set_console_baud(t, baud):
    """Переключает консоль платы на другую скорость.

    Ответа не ждём: он придёт уже на новой скорости, и t.exec() на старой
    его просто не дождётся (проверено — висит до таймаута)."""
    t.serial.write(b"from machine import UART; UART(0, %d)\x04" % baud)
    time.sleep(0.3)


def main():
    argv = sys.argv[1:]
    do_reset = "--no-reset" not in argv
    # Штатная скорость первой, быстрая — как страховка: если прошлый
    # запуск оборвался, не вернув консоль обратно, плата осталась на ней.
    bauds = (BASE_BAUD, FAST_BAUD)
    # 4096, а не штатные для mpremote 256: замерено на 115200 — 3.7 КБ/с
    # против 2.9, крупные куски экономят круговые задержки протокола.
    chunk = 4096
    if "--chunk" in argv:
        i = argv.index("--chunk")
        chunk = int(argv[i + 1])
        del argv[i:i + 2]
    wifi_host = None
    if "--wifi" in argv:
        i = argv.index("--wifi")
        wifi_host = BOARD_HOST
        if i + 1 < len(argv) and not argv[i + 1].startswith("--") \
                and not argv[i + 1].endswith(".py"):
            wifi_host = argv[i + 1]
            del argv[i + 1]
        del argv[i]
    if "--baud" in argv:
        i = argv.index("--baud")
        bauds = (int(argv[i + 1]),)
        del argv[i:i + 2]
    files = [a for a in argv if not a.startswith("--")] or PROJECT_FILES

    if wifi_host:
        deploy_over_wifi(files, wifi_host, do_reset)
        return

    port = find_port()
    t, baud = connect(port, bauds)
    print("подключено: %s @ %d бод" % (port, baud))

    turbo = "--no-turbo" not in argv and baud == BASE_BAUD
    if turbo:
        set_console_baud(t, FAST_BAUD)
        t.close()
        t, baud = connect(port, (FAST_BAUD,))
        print("разогнано до %d бод на время передачи" % baud)

    total = 0
    t0 = time.time()
    for name in files:
        with open(name, "rb") as f:
            data = f.read()
        t.fs_writefile(name, data, chunk_size=chunk)
        total += len(data)
        print("  %-16s %6d Б" % (name, len(data)))

    dt = time.time() - t0
    print("итого %d Б за %.1f с (%.1f КБ/с), кусок %d Б, %d бод"
          % (total, dt, total / dt / 1024, chunk, baud))

    if do_reset:
        # Сброс возвращает консоль на штатные 115200 сам — скорость мы
        # меняли в живой сессии, на flash она не записана.
        # Пишем в сыром виде, а не через t.exec(): плата уходит в сброс,
        # не ответив, exec это считает ошибкой и сброс не выполняется
        # вовсе (проверено — плата оставалась в raw REPL на быстрой
        # скорости, и обычный mpremote к ней уже не подключался).
        t.serial.write(b"import machine; machine.reset()\x04")
        time.sleep(0.5)
        print("плата перезагружена (консоль вернулась на %d бод)" % BASE_BAUD)
    elif turbo:
        # Без сброса плата осталась бы на быстрой скорости, и обычный
        # mpremote к ней уже не подключился бы.
        set_console_baud(t, BASE_BAUD)
        print("консоль возвращена на %d бод" % BASE_BAUD)
    t.close()


if __name__ == "__main__":
    main()
