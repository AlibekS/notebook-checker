"""Этап 1: паспорт устройства и системная диагностика (сбор стартует при запуске программы)."""

from __future__ import annotations

import queue
from tkinter import ttk

from .. import sysinfo
from ..common import DEFECT, IS_WINDOWS, PASSED, WARNING
from .base import Step


class PassportStep(Step):
    step_id = "passport"
    title = "Паспорт устройства"
    hint = ("Данные собираются автоматически через PowerShell / WMI. Проверьте серийный номер, "
            "состояние батареи и дисков, проблемные устройства и статус BitLocker.")

    def build(self, body):
        bar = ttk.Frame(body)
        bar.pack(fill="x", pady=(0, 6))
        self.status_label = ttk.Label(bar, text="")
        self.status_label.pack(side="left")
        self.refresh_btn = ttk.Button(bar, text="Обновить данные", command=self.start_collect)
        self.refresh_btn.pack(side="right")

        wrap = ttk.Frame(body)
        wrap.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(wrap, columns=("value",), show="tree headings", selectmode="none")
        self.tree.heading("#0", text="Параметр")
        self.tree.heading("value", text="Значение")
        self.tree.column("#0", width=self.app.px(300), stretch=False)
        self.tree.column("value", width=self.app.px(540))
        scroll = ttk.Scrollbar(wrap, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.tree.pack(side="left", fill="both", expand=True)
        self.tree.tag_configure("section", background="#e5e9f0", font=self.app.font_bold)
        self.tree.tag_configure("ok", foreground="#1f7a45")
        self.tree.tag_configure("warn", foreground="#a86700")
        self.tree.tag_configure("bad", foreground="#c22f2f")

        self._queue: queue.Queue = queue.Queue()
        self._pending = 0
        self.start_collect()  # фоновый запуск при старте программы
        self._poll()

    def start_collect(self):
        if self._pending:
            return
        self.app.passport.clear()
        if not IS_WINDOWS:
            for name in sysinfo.SECTION_TITLES:
                self.app.passport[name] = {"error": "Сбор данных доступен только в Windows"}
            self._render()
            self.status_label.configure(text="Сбор данных доступен только в Windows")
            return
        self._pending = len(sysinfo.PS_SECTIONS)
        self.refresh_btn.state(["disabled"])
        self.status_label.configure(text="Идёт сбор данных…")
        self._render()
        # Поток только кладёт результат в очередь; виджеты трогает лишь главный поток.
        sysinfo.collect_async(lambda name, data: self._queue.put((name, data)))

    def _poll(self):
        """Этот опрос работает всё время жизни программы (не привязан к показу этапа)."""
        changed = False
        try:
            while True:
                name, data = self._queue.get_nowait()
                self.app.passport[name] = data
                self._pending = max(0, self._pending - 1)
                changed = True
        except queue.Empty:
            pass
        if changed:
            self._render()
            if self._pending == 0:
                self._finish()
        self.after(200, self._poll)

    def _finish(self):
        self.refresh_btn.state(["!disabled"])
        notes = sysinfo.findings(self.app.passport)
        bad = sum(1 for level, _ in notes if level == "bad")
        if not notes:
            self.status_label.configure(text="Сбор завершён, замечаний нет")
            self.app.set_auto_status(self, PASSED)
        else:
            self.status_label.configure(text=f"Сбор завершён, замечаний: {len(notes)} — проверьте выделенные строки")
            self.app.set_auto_status(self, DEFECT if bad else WARNING)

    def _render(self):
        self.tree.delete(*self.tree.get_children())
        for title, rows in sysinfo.passport_sections(self.app.passport):
            self.tree.insert("", "end", text=title, values=("",), tags=("section",))
            for label, value, level in rows:
                self.tree.insert("", "end", text="   " + label, values=(value,), tags=(level,) if level else ())

    def collect(self):
        return {"findings": [{"level": lv, "text": t} for lv, t in sysinfo.findings(self.app.passport)]}

    def summary(self):
        notes = sysinfo.findings(self.app.passport)
        return [("Автоматических замечаний", str(len(notes)))]
