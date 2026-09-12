#!/usr/bin/env python3
"""Собирает ota_manifest.json из файлов проекта. Запускается на компьютере
перед коммитом, результат коммитится вместе с кодом — плата читает его с
GitHub и по нему решает, что качать (см. ota.py).

    python3 tools/gen_ota_manifest.py          # версия +1 к текущей
    python3 tools/gen_ota_manifest.py 42       # задать версию вручную

Версия — целое число в файле VERSION. Плата сравнивает её с тем, что
записано у неё в /ota_version.txt, и обновляется, только если манифест
новее. Поэтому версию надо поднимать при каждом релизе, иначе плата
решит, что обновляться не за чем.
"""

import hashlib
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Что уезжает на плату. Перечислено списком, а не собирается обходом
# каталога: в проекте лежат ещё тестовые скрипты, прошивка и инструменты
# для компьютера, и им на плате делать нечего.
FIRMWARE_FILES = [
    "main.py",
    "web_app.py",
    "wifi_manager.py",
    "epaper.py",
    "dither.py",
    "bmp.py",
    "persist.py",
    "yuv.py",
    "ota.py",
]

MANIFEST_PATH = os.path.join(ROOT, "ota_manifest.json")
VERSION_PATH = os.path.join(ROOT, "VERSION")


def read_version():
    try:
        with open(VERSION_PATH) as f:
            return int(f.read().strip())
    except (OSError, ValueError):
        return 0


def main():
    if len(sys.argv) > 1:
        version = int(sys.argv[1])
    else:
        version = read_version() + 1

    files = {}
    for rel in sorted(FIRMWARE_FILES):
        path = os.path.join(ROOT, rel)
        if not os.path.exists(path):
            sys.exit("нет файла %s — список в FIRMWARE_FILES разошёлся с проектом" % rel)
        with open(path, "rb") as f:
            files[rel] = hashlib.sha256(f.read()).hexdigest()

    with open(VERSION_PATH, "w") as f:
        f.write("%d\n" % version)

    with open(MANIFEST_PATH, "w") as f:
        json.dump({"version": version, "files": files}, f, indent=1, sort_keys=True)
        f.write("\n")

    print("версия %d, файлов %d" % (version, len(files)))
    for rel, h in files.items():
        print("  %-16s %s" % (rel, h[:12]))


if __name__ == "__main__":
    main()
