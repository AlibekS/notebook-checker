"""Этап 7: чек-лист визуального осмотра, данные сторон и формирование акта приёмки."""

from __future__ import annotations

import json
import os
import sys
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from ..common import DEFECT, PASSED, reports_dir
from ..report import save_report
from .base import Step

CHECKLIST = [
    "Отсутствие трещин, сколов и деформаций корпуса и петель",
    "Отсутствие царапин и трещин на матрице",
    "Отсутствие следов влаги, окисления в портах и хруста/залипания клавиш",
    "Наличие оригинального блока питания в комплекте",
]


class InspectionStep(Step):
    step_id = "inspection"
    title = "Осмотр и акт приёмки"
    hint = "Отметьте галочкой пункты, которые выполняются. Неотмеченный пункт попадёт в акт как замечание."
    show_actions = False

    def build(self, body):
        self.last_report = None
        box = ttk.LabelFrame(body, text="Чек-лист физического состояния", padding=10)
        box.pack(fill="x")
        self.checks = []
        for text in CHECKLIST:
            var = tk.BooleanVar(value=False)
            ttk.Checkbutton(box, text=text, variable=var).pack(anchor="w", pady=2)
            self.checks.append((text, var))

        form = ttk.LabelFrame(body, text="Данные сторон", padding=10)
        form.pack(fill="x", pady=10)
        form.columnconfigure(1, weight=1)
        self.employee = tk.StringVar()
        self.it = tk.StringVar(value=self._load_it_name())
        ttk.Label(form, text="ФИО сдающего сотрудника:").grid(row=0, column=0, sticky="w", pady=3)
        ttk.Entry(form, textvariable=self.employee).grid(row=0, column=1, sticky="ew", padx=(8, 0))
        ttk.Label(form, text="ФИО принимающего IT-специалиста:").grid(row=1, column=0, sticky="w", pady=3)
        ttk.Entry(form, textvariable=self.it).grid(row=1, column=1, sticky="ew", padx=(8, 0))
        ttk.Label(form, text="Общий комментарий:").grid(row=2, column=0, sticky="nw", pady=3)
        self.comment = tk.Text(form, height=4, font=self.app.font_normal, wrap="word")
        self.comment.grid(row=2, column=1, sticky="ew", padx=(8, 0), pady=3)

        row = ttk.Frame(body)
        row.pack(fill="x")
        ttk.Button(row, text="Сформировать акт приёмки", style="Big.TButton",
                   command=self.generate).pack(side="left")
        self.open_btn = ttk.Button(row, text="Открыть акт в браузере", command=self.open_report)
        self.result = ttk.Label(body, text="", wraplength=self.app.px(820), justify="left")
        self.result.pack(anchor="w", pady=10)

    def inspection_data(self) -> dict:
        return {"items": [{"text": text, "checked": bool(var.get())} for text, var in self.checks]}

    def parties_data(self) -> dict:
        return {"employee": self.employee.get().strip(), "it": self.it.get().strip(),
                "comment": self.comment.get("1.0", "end").strip()}

    def generate(self):
        parties = self.parties_data()
        if not parties["employee"] or not parties["it"]:
            messagebox.showwarning("Акт приёмки", "Укажите ФИО сдающего сотрудника и принимающего IT-специалиста.")
            return
        if self.app.passport_busy() and not messagebox.askyesno(
                "Акт приёмки", "Паспорт устройства ещё собирается (этап 1) — часть данных не попадёт в акт.\n\n"
                "Лучше подождать несколько секунд. Сформировать акт прямо сейчас?"):
            return
        pending = self.app.pending_titles()
        if pending and not messagebox.askyesno(
                "Акт приёмки", "Не по всем этапам принято решение:\n\n• " + "\n• ".join(pending)
                + "\n\nВ акте они будут отмечены как «Не проводился». Сформировать акт?"):
            return
        unchecked = [i for i in self.inspection_data()["items"] if not i["checked"]]
        self.app.set_status(self, DEFECT if unchecked else PASSED,
                            f"Замечаний осмотра: {len(unchecked)}" if unchecked else "")
        record = self.app.build_record()
        try:
            html_path, json_path = save_report(record)
        except OSError as exc:
            # Типичная причина — флешка извлечена после теста USB, сменила букву или защищена от записи
            if not messagebox.askyesno(
                    "Акт приёмки", f"Не удалось сохранить отчёт в папку Reports:\n{exc}\n\n"
                    "Проверьте, что флешка подключена, и нажмите кнопку ещё раз.\n\n"
                    "Либо выбрать другую папку для сохранения прямо сейчас?"):
                return
            folder = filedialog.askdirectory(title="Папка для акта приёмки")
            if not folder:
                return
            try:
                html_path, json_path = save_report(record, Path(folder))
            except OSError as exc2:
                messagebox.showerror("Акт приёмки", f"Не удалось сохранить отчёт:\n{exc2}")
                return
        self._save_it_name(parties["it"])
        self.last_report = html_path
        self.result.configure(text=f"Акт сохранён:\n{html_path}\n{json_path}")
        self.open_btn.pack(side="left", padx=10)

    # --- ФИО IT-специалиста запоминается между запусками (файл лежит в той же папке Reports) ---
    @staticmethod
    def _settings_path() -> Path:
        return reports_dir() / "LaptopCheck_settings.json"

    def _load_it_name(self) -> str:
        try:
            return str(json.loads(self._settings_path().read_text(encoding="utf-8")).get("it_name", ""))
        except (OSError, ValueError, AttributeError):
            return ""

    def _save_it_name(self, name: str) -> None:
        try:
            self._settings_path().write_text(json.dumps({"it_name": name}, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass   # не критично: просто не запомним

    def open_report(self):
        if not self.last_report:
            return
        try:
            if sys.platform == "win32":
                os.startfile(str(self.last_report))  # noqa: S606 — открываем собственный отчёт
            else:
                webbrowser.open(self.last_report.as_uri())
        except OSError as exc:
            messagebox.showerror("Акт приёмки", f"Не удалось открыть отчёт: {exc}")

    def collect(self):
        return self.inspection_data()

    def summary(self):
        items = self.inspection_data()["items"]
        return [("Пунктов чек-листа выполнено", f"{sum(i['checked'] for i in items)} из {len(items)}")]
