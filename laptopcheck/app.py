"""Главное окно: пошаговый мастер со списком этапов слева и рабочей областью справа."""

from __future__ import annotations

import tkinter as tk
from datetime import datetime
from tkinter import font as tkfont
from tkinter import messagebox, ttk

from . import APP_NAME, APP_VERSION
from .common import (DEFECT, PASSED, PENDING, SKIPPED, STATUS_COLORS, STATUS_ICONS,
                     STATUS_LABELS, app_dir)
from .steps.audio import AudioStep
from .steps.camera import CameraStep
from .steps.display import DisplayStep
from .steps.inspection import InspectionStep
from .steps.keyboard import KeyboardStep
from .steps.passport import PassportStep
from .steps.ports import PortsStep

STEP_CLASSES = [PassportStep, KeyboardStep, DisplayStep, CameraStep, AudioStep, PortsStep, InspectionStep]

SIDEBAR_BG, SIDEBAR_ACTIVE, SIDEBAR_FG = "#1f2937", "#374151", "#e5e7eb"


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"{APP_NAME} {APP_VERSION} — приёмка ноутбука")
        self._scale = self.winfo_fpixels("1i") / 96.0   # коэффициент масштабирования Windows (DPI)
        width = min(self.px(1200), self.winfo_screenwidth() - 40)
        height = min(self.px(780), self.winfo_screenheight() - 90)
        self.geometry(f"{width}x{height}+20+10")
        self.minsize(min(self.px(980), width), min(self.px(640), height))

        self.passport: dict = {}         # результаты модуля 1, общие для этапов и отчёта
        self.results: dict[str, dict] = {}
        self.current = -1
        self._init_style()

        # --- левая панель со списком этапов ---------------------------------
        sidebar = tk.Frame(self, bg=SIDEBAR_BG, width=self.px(270))
        sidebar.pack(side="left", fill="y")
        sidebar.pack_propagate(False)
        tk.Label(sidebar, text=APP_NAME, bg=SIDEBAR_BG, fg="#ffffff",
                 font=(self.font_family, 16, "bold"), anchor="w").pack(fill="x", padx=16, pady=(16, 0))
        tk.Label(sidebar, text="Приёмка корпоративного ноутбука", bg=SIDEBAR_BG, fg="#9ca3af",
                 font=self.font_small, anchor="w").pack(fill="x", padx=16, pady=(0, 14))
        self._rows: list[dict] = []

        # --- правая часть: этап + панель действий ---------------------------
        right = ttk.Frame(self)
        right.pack(side="left", fill="both", expand=True)
        self.actions = ttk.Frame(right, padding=(18, 10))
        self.actions.pack(side="bottom", fill="x")
        self._actions_sep = ttk.Separator(right)
        self._actions_sep.pack(side="bottom", fill="x")
        self.container = ttk.Frame(right)
        self.container.pack(side="top", fill="both", expand=True)
        self._build_actions()

        self.steps = []
        for index, cls in enumerate(STEP_CLASSES):
            self.results[cls.step_id] = {"status": PENDING, "comment": "", "manual": False}
            step = cls(self.container, self)
            self.steps.append(step)
            self._add_sidebar_row(sidebar, index, step)
        tk.Label(sidebar, text=f"Отчёты: {app_dir() / 'Reports'}", bg=SIDEBAR_BG, fg="#9ca3af",
                 font=self.font_small, anchor="w", justify="left",
                 wraplength=self.px(240)).pack(side="bottom", fill="x", padx=16, pady=12)

        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.show_step(0)

    # --- оформление ---------------------------------------------------------
    def px(self, value: float) -> int:
        """Пиксели с учётом масштаба экрана."""
        return int(value * self._scale)

    def _init_style(self):
        base = tkfont.nametofont("TkDefaultFont")
        self.font_family = base.actual("family")
        size = max(base.actual("size"), 10)
        base.configure(size=size)
        tkfont.nametofont("TkTextFont").configure(size=size)
        self.font_normal = (self.font_family, size)
        self.font_bold = (self.font_family, size, "bold")
        self.font_small = (self.font_family, size - 1)
        style = ttk.Style(self)
        self.bg = style.lookup("TFrame", "background") or self.cget("bg")
        style.configure("Title.TLabel", font=(self.font_family, size + 6, "bold"))
        style.configure("Hint.TLabel", foreground="#5a6577")
        style.configure("Big.TButton", font=self.font_bold, padding=(14, 8))
        style.configure("Treeview", rowheight=self.px(24))
        style.configure("Treeview.Heading", font=self.font_bold)

    def _add_sidebar_row(self, sidebar, index: int, step):
        frame = tk.Frame(sidebar, bg=SIDEBAR_BG, cursor="hand2")
        frame.pack(fill="x")
        icon = tk.Label(frame, text=STATUS_ICONS[PENDING], bg=SIDEBAR_BG, fg=STATUS_COLORS[PENDING],
                        font=(self.font_family, 13, "bold"), width=2)
        icon.pack(side="left", padx=(12, 4), pady=7)
        text = tk.Frame(frame, bg=SIDEBAR_BG)
        text.pack(side="left", fill="x", expand=True)
        title = tk.Label(text, text=f"{index + 1}. {step.title}", bg=SIDEBAR_BG, fg=SIDEBAR_FG,
                         font=self.font_bold, anchor="w")
        title.pack(fill="x")
        status = tk.Label(text, text=STATUS_LABELS[PENDING], bg=SIDEBAR_BG, fg=STATUS_COLORS[PENDING],
                          font=self.font_small, anchor="w")
        status.pack(fill="x")
        widgets = [frame, icon, text, title, status]
        for widget in widgets:
            widget.bind("<Button-1>", lambda e, i=index: self.show_step(i))
        self._rows.append({"widgets": widgets, "icon": icon, "status": status})

    def _build_actions(self):
        bar = self.actions
        ttk.Label(bar, text="Комментарий:").pack(side="left")
        self.comment_var = tk.StringVar()
        self.comment_entry = ttk.Entry(bar, textvariable=self.comment_var)
        self.comment_entry.pack(side="left", fill="x", expand=True, padx=8)
        # takefocus=False: во время теста клавиатуры Пробел/Enter не должны «нажимать» кнопки
        self.btn_ok = tk.Button(bar, text="✔ Подтвердить (ОК)", bg="#1f9d55", fg="#ffffff", relief="flat",
                                activebackground="#178a49", activeforeground="#ffffff", font=self.font_bold,
                                padx=14, pady=6, takefocus=False, command=lambda: self._decide(PASSED))
        self.btn_defect = tk.Button(bar, text="✖ Зафиксировать дефект", bg="#d64545", fg="#ffffff", relief="flat",
                                    activebackground="#b93636", activeforeground="#ffffff", font=self.font_bold,
                                    padx=14, pady=6, takefocus=False, command=lambda: self._decide(DEFECT))
        self.btn_skip = tk.Button(bar, text="Пропустить", bg="#e5e7eb", fg="#1c2430", relief="flat",
                                  font=self.font_normal, padx=14, pady=6, takefocus=False,
                                  command=lambda: self._decide(SKIPPED))
        self.btn_ok.pack(side="left", padx=4)
        self.btn_defect.pack(side="left", padx=4)
        self.btn_skip.pack(side="left", padx=4)

    # --- навигация ----------------------------------------------------------
    def show_step(self, index: int):
        if index == self.current or not 0 <= index < len(self.steps):
            return
        if self.current >= 0:
            old = self.steps[self.current]
            self.results[old.step_id]["comment"] = self.comment_var.get().strip()
            self._safe(old.on_hide)
            old.pack_forget()
        self.current = index
        step = self.steps[index]
        step.pack(fill="both", expand=True)
        self.update_idletasks()
        self.comment_var.set(self.results[step.step_id]["comment"])
        if step.show_actions:
            # before=…: панель должна остаться первой в порядке упаковки, иначе при возврате
            # с последнего этапа она окажется под разделителем и может не поместиться в окно
            self.actions.pack(side="bottom", fill="x", before=self._actions_sep)
        else:
            self.actions.pack_forget()
        self._refresh_sidebar()
        self.focus_set()
        self._safe(step.on_show)

    def _safe(self, func):
        """Сбой одного этапа (например, драйвера камеры) не должен ронять всю программу."""
        try:
            func()
        except Exception as exc:
            messagebox.showerror(APP_NAME, f"Ошибка этапа: {type(exc).__name__}: {exc}")

    def _decide(self, status: str):
        step = self.steps[self.current]
        comment = self.comment_var.get().strip()
        if status == DEFECT and not comment:
            messagebox.showwarning(APP_NAME, "Опишите дефект в поле «Комментарий».")
            return
        self.set_status(step, status, comment)
        self.show_step(self.current + 1)

    # --- статусы ------------------------------------------------------------
    def set_status(self, step, status: str, comment: str | None = None):
        """Решение инженера (кнопки внизу)."""
        result = self.results[step.step_id]
        result["status"], result["manual"] = status, True
        if comment is not None:
            result["comment"] = comment
        self._refresh_sidebar()

    def set_auto_status(self, step, status: str):
        """Автоматический статус от самого этапа; не перезаписывает решение инженера."""
        result = self.results[step.step_id]
        if not result["manual"]:
            result["status"] = status
            self._refresh_sidebar()

    def _refresh_sidebar(self):
        for index, row in enumerate(self._rows):
            status = self.results[self.steps[index].step_id]["status"]
            bg = SIDEBAR_ACTIVE if index == self.current else SIDEBAR_BG
            for widget in row["widgets"]:
                widget.configure(bg=bg)
            row["icon"].configure(text=STATUS_ICONS[status], fg=STATUS_COLORS[status])
            row["status"].configure(text=STATUS_LABELS[status], fg=STATUS_COLORS[status])

    def passport_busy(self) -> bool:
        """True, пока PowerShell ещё собирает паспорт устройства."""
        return any(getattr(s, "_pending", 0) for s in self.steps)

    def pending_titles(self) -> list[str]:
        return [s.title for s in self.steps
                if s.show_actions and self.results[s.step_id]["status"] == PENDING]

    # --- данные для отчёта --------------------------------------------------
    def build_record(self) -> dict:
        inspection = next(s for s in self.steps if isinstance(s, InspectionStep))
        steps = []
        for step in self.steps:
            if step is inspection:
                continue  # осмотр выводится в акте отдельным разделом
            result = self.results[step.step_id]
            try:
                data, summary = step.collect(), step.summary()
            except Exception as exc:
                data, summary = {"error": str(exc)}, []
            steps.append({"id": step.step_id, "title": step.title, "status": result["status"],
                          "status_label": STATUS_LABELS[result["status"]], "comment": result["comment"],
                          "summary": summary, "data": data})
        return {
            "app": f"{APP_NAME} {APP_VERSION}",
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "parties": inspection.parties_data(),
            "passport": dict(self.passport),
            "steps": steps,
            "inspection": inspection.inspection_data(),
        }

    def _on_close(self):
        for step in self.steps:
            try:
                step.on_hide()   # освобождаем камеру, звук и хук клавиатуры
            except Exception:
                pass
        self.destroy()
