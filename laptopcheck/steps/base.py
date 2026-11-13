"""Базовый класс этапа мастера."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk


class Step(ttk.Frame):
    """Один этап мастера. Наследники переопределяют on_show/on_hide/collect/summary."""

    step_id = ""
    title = ""
    hint = ""             # подсказка под заголовком
    show_actions = True   # показывать ли кнопки «ОК / Дефект / Пропустить»

    def __init__(self, parent: tk.Misc, app):
        super().__init__(parent, padding=(18, 14))
        self.app = app
        self._after_ids: set[str] = set()
        ttk.Label(self, text=self.title, style="Title.TLabel").pack(anchor="w")
        if self.hint:
            ttk.Label(self, text=self.hint, style="Hint.TLabel", wraplength=app.px(820),
                      justify="left").pack(anchor="w", pady=(2, 10))
        self.body = ttk.Frame(self)
        self.body.pack(fill="both", expand=True)
        self.build(self.body)

    # --- для переопределения ---------------------------------------------
    def build(self, body: ttk.Frame) -> None:
        """Создание виджетов этапа."""

    def on_show(self) -> None:
        """Этап стал видимым: можно занимать устройства (камера, звук, хук)."""

    def on_hide(self) -> None:
        """Этап скрыт: обязательно освободить устройства."""

    def collect(self) -> dict:
        """Сырые данные этапа для JSON."""
        return {}

    def summary(self) -> list[tuple[str, str]]:
        """Короткие строки «параметр: значение» для HTML-акта."""
        return []

    # --- таймеры, которые гарантированно снимаются при уходе с этапа -----------
    def later(self, ms: int, func) -> str:
        holder = {}

        def run():
            self._after_ids.discard(holder["id"])
            func()

        holder["id"] = self.after(ms, run)
        self._after_ids.add(holder["id"])
        return holder["id"]

    def cancel_timers(self) -> None:
        for after_id in list(self._after_ids):
            try:
                self.after_cancel(after_id)
            except tk.TclError:
                pass
        self._after_ids.clear()
