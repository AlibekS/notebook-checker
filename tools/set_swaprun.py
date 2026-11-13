"""Пост-обработка LaptopCheck.exe: включает флаги PE «запускать из файла подкачки».

Зачем: exe запускается с флешки, а в тесте USB-портов эту флешку вынимают.
Без флагов Windows подгружает страницы кода прямо с носителя, и после извлечения
процесс может упасть (STATUS_IN_PAGE_ERROR). С флагами IMAGE_FILE_REMOVABLE_RUN_FROM_SWAP
и IMAGE_FILE_NET_RUN_FROM_SWAP загрузчик целиком копирует образ в память при старте.

Использование: python tools/set_swaprun.py dist/LaptopCheck.exe
"""

import struct
import sys

REMOVABLE_RUN_FROM_SWAP = 0x0400
NET_RUN_FROM_SWAP = 0x0800


def patch(path: str) -> None:
    with open(path, "r+b") as f:
        if f.read(2) != b"MZ":
            raise SystemExit(f"{path}: это не исполняемый файл Windows")
        f.seek(0x3C)
        pe_offset = struct.unpack("<I", f.read(4))[0]
        f.seek(pe_offset)
        if f.read(4) != b"PE\0\0":
            raise SystemExit(f"{path}: не найдена сигнатура PE")
        flags_offset = pe_offset + 4 + 18      # поле Characteristics заголовка COFF
        f.seek(flags_offset)
        flags = struct.unpack("<H", f.read(2))[0]
        new_flags = flags | REMOVABLE_RUN_FROM_SWAP | NET_RUN_FROM_SWAP
        f.seek(flags_offset)
        f.write(struct.pack("<H", new_flags))
    print(f"{path}: Characteristics 0x{flags:04X} -> 0x{new_flags:04X}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Использование: python tools/set_swaprun.py <путь к exe>")
    patch(sys.argv[1])
