"""Этап 3: тест матрицы — сплошные заливки на весь экран."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from .base import Step

FILLS = [("Чёрный", "#000000"), ("Белый", "#ffffff"), ("Красный", "#ff0000"),
         ("Зелёный", "#00ff00"), ("Синий", "#0000ff"), ("Серый 50%", "#808080")]


class DisplayStep(Step):
    step_id = "display"
    title = "Дисплей (матрица)"
    hint = ("Полноэкранные заливки для поиска битых пикселей, пятен, выгорания и засветов. "
            "Смена цвета — клик мыши или Пробел, выход — Esc.")

    def build(self, body):
        self.shown: list[str] = []   # какие заливки реально были показаны
        self.runs = 0
        self._win: tk.Toplevel | None = None
        ttk.Button(body, text="Запустить тест экрана", style="Big.TButton",
                   command=self.start_test).pack(anchor="w", pady=(6, 12))
        self.progress = ttk.Label(body, text="Тест ещё не запускался")
        self.progress.pack(anchor="w")
        ttk.Label(body, justify="left", style="Hint.TLabel", text=(
            "\nНа что смотреть:\n"
            "  • Чёрный — светящиеся точки, засветы по краям\n"
            "  • Белый — тёмные точки, жёлтые пятна, неравномерность подсветки\n"
            "  • Красный / Зелёный / Синий — «мёртвые» субпиксели\n"
            "  • Серый 50% — полосы, пятна, следы от клавиатуры, выгорание")).pack(anchor="w")

    def start_test(self):
        if self._win is not None:
            return
        self._index = 0
        win = tk.Toplevel(self)
        self._win = win
        win.overrideredirect(True)  # окно без рамок
        win.geometry(f"{win.winfo_screenwidth()}x{win.winfo_screenheight()}+0+0")
        win.attributes("-topmost", True)
        win.configure(cursor="none", bg=FILLS[0][1])
        win.bind("<Button-1>", lambda e: self._next())
        win.bind("<space>", lambda e: self._next())
        win.bind("<Escape>", lambda e: self._close())
        win.protocol("WM_DELETE_WINDOW", self._close)
        win.focus_force()
        win.grab_set()
        self.runs += 1
        self._mark()

    def _mark(self):
        name = FILLS[self._index][0]
        if name not in self.shown:
            self.shown.append(name)

    def _next(self):
        self._index += 1
        if self._index >= len(FILLS):
            self._close()
            return
        self._win.configure(bg=FILLS[self._index][1])
        self._mark()

    def _close(self):
        if self._win is not None:
            try:
                self._win.grab_release()
                self._win.destroy()
            except tk.TclError:
                pass
            self._win = None
        self.progress.configure(text=f"Показано заливок: {len(self.shown)} из {len(FILLS)} ({', '.join(self.shown)})")

    def on_hide(self):
        self._close() if self._win is not None else None

    def collect(self):
        return {"fills_shown": list(self.shown), "fills_total": len(FILLS), "runs": self.runs}

    def summary(self):
        if not self.shown:
            return [("Заливки", "тест не запускался")]
        return [("Показано заливок", f"{len(self.shown)} из {len(FILLS)}")]
