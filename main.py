"""Точка входа LaptopCheck. Запуск из исходников: python main.py"""

from __future__ import annotations

import os
import sys
import tempfile
import traceback


def _prepare_process() -> None:
    if sys.platform != "win32":
        return
    import ctypes
    # Чёткий интерфейс и настоящие пиксели экрана на дисплеях с масштабом 125–200 %
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass
    # Уходим с флешки: тогда её можно извлекать во время теста USB-портов.
    try:
        os.chdir(tempfile.gettempdir())
    except OSError:
        pass


def main() -> int:
    _prepare_process()
    try:
        from laptopcheck.app import App
        App().mainloop()
        return 0
    except Exception:
        # В сборке --noconsole трассировка иначе никому не видна
        text = traceback.format_exc()
        try:
            from laptopcheck.common import reports_dir
            (reports_dir() / "LaptopCheck_error.log").write_text(text, encoding="utf-8")
        except Exception:
            pass
        try:
            from tkinter import messagebox
            messagebox.showerror("LaptopCheck", "Критическая ошибка:\n\n" + text[-1500:])
        except Exception:
            pass
        return 1


if __name__ == "__main__":
    sys.exit(main())
