"""Этап 2: тест клавиатуры (низкоуровневый хук, детектор залипания) и тачпада."""

from __future__ import annotations

import queue
import tkinter as tk
from tkinter import ttk

from ..common import IS_WINDOWS, WARNING
from ..keyhook import KeyboardHook, pressed_keys
from .base import Step

# --- Раскладка -----------------------------------------------------------------
# Идентификатор клавиши — виртуальный код (VK) либо "NUMENTER" для Enter на цифровом блоке.
# Клавиша: (подпись, id, x, y, ширина, высота) в «юнитах» (1 юнит = обычная клавиша).

def _row(y: float, x: float, keys: list[tuple]) -> list[tuple]:
    out = []
    for label, key_id, *rest in keys:
        width = rest[0] if rest else 1.0
        out.append((label, key_id, x, y, width, 1.0))
        x += width
    return out


def build_layout(iso: bool, numpad: bool) -> list[tuple]:
    keys: list[tuple] = []
    # Функциональный ряд
    keys += _row(0, 0, [("Esc", 0x1B)])
    keys += _row(0, 2, [(f"F{i}", 0x6F + i) for i in range(1, 5)])
    keys += _row(0, 6.5, [(f"F{i}", 0x6F + i) for i in range(5, 9)])
    keys += _row(0, 11, [(f"F{i}", 0x6F + i) for i in range(9, 13)])
    keys += _row(0, 15.5, [("PrtSc", 0x2C), ("ScrLk", 0x91), ("Pause", 0x13)])
    # Основной блок
    digits = [("`", 0xC0)] + [(str(i), 0x30 + i) for i in range(1, 10)] + [("0", 0x30), ("-", 0xBD), ("=", 0xBB)]
    keys += _row(1.5, 0, digits + [("Backspace", 0x08, 2)])
    keys += _row(2.5, 0, [("Tab", 0x09, 1.5)] + [(c, ord(c)) for c in "QWERTYUIOP"]
                 + [("[", 0xDB), ("]", 0xDD), ("\\", 0xDC, 1.5)])
    keys += _row(3.5, 0, [("Caps", 0x14, 1.75)] + [(c, ord(c)) for c in "ASDFGHJKL"]
                 + [(";", 0xBA), ("'", 0xDE), ("Enter", 0x0D, 2.25)])
    letters = [(c, ord(c)) for c in "ZXCVBNM"] + [(",", 0xBC), (".", 0xBE), ("/", 0xBF)]
    if iso:  # ISO: короткий левый Shift и дополнительная клавиша <> рядом с ним
        keys += _row(4.5, 0, [("Shift", 0xA0, 1.25), ("<>", 0xE2)] + letters + [("Shift", 0xA1, 2.75)])
    else:
        keys += _row(4.5, 0, [("Shift", 0xA0, 2.25)] + letters + [("Shift", 0xA1, 2.75)])
    keys += _row(5.5, 0, [("Ctrl", 0xA2, 1.25), ("Fn", None, 1), ("Win", 0x5B, 1.25), ("Alt", 0xA4, 1.25),
                          ("Space", 0x20, 6.5), ("Alt", 0xA5, 1.25), ("Menu", 0x5D, 1.25), ("Ctrl", 0xA3, 1.25)])
    # Блок навигации
    keys += _row(1.5, 15.5, [("Ins", 0x2D), ("Home", 0x24), ("PgUp", 0x21)])
    keys += _row(2.5, 15.5, [("Del", 0x2E), ("End", 0x23), ("PgDn", 0x22)])
    keys += _row(4.5, 16.5, [("↑", 0x26)])
    keys += _row(5.5, 15.5, [("←", 0x25), ("↓", 0x28), ("→", 0x27)])
    if numpad:
        x = 19
        keys += _row(1.5, x, [("Num", 0x90), ("/", 0x6F), ("*", 0x6A), ("-", 0x6D)])
        keys += _row(2.5, x, [("7", 0x67), ("8", 0x68), ("9", 0x69)])
        keys += _row(3.5, x, [("4", 0x64), ("5", 0x65), ("6", 0x66)])
        keys += _row(4.5, x, [("1", 0x61), ("2", 0x62), ("3", 0x63)])
        keys += _row(5.5, x, [("0", 0x60, 2), (".", 0x6E)])
        keys.append(("+", 0x6B, x + 3, 2.5, 1.0, 2.0))
        keys.append(("Ent", "NUMENTER", x + 3, 4.5, 1.0, 2.0))
    return keys


# При выключенном NumLock цифровой блок присылает коды навигации БЕЗ флага extended.
_NUMPAD_NAV = {0x2D: 0x60, 0x23: 0x61, 0x28: 0x62, 0x22: 0x63, 0x25: 0x64, 0x0C: 0x65,
               0x27: 0x66, 0x24: 0x67, 0x26: 0x68, 0x21: 0x69, 0x2E: 0x6E}

_VK_NAMES = {0x5C: "Правый Win", 0xAD: "Mute", 0xAE: "Volume−", 0xAF: "Volume+", 0xB0: "Next", 0xB1: "Prev",
             0xB2: "Stop", 0xB3: "Play/Pause", 0x5F: "Sleep", 0xA6: "Browser Back", 0xA7: "Browser Forward"}


# Понятные названия для клавиш с одинаковыми подписями (в акте: «Не нажаты: Правый Ctrl, Num 5»)
_SIDE_NAMES = {0xA0: "Левый Shift", 0xA1: "Правый Shift", 0xA2: "Левый Ctrl", 0xA3: "Правый Ctrl",
               0xA4: "Левый Alt", 0xA5: "Правый Alt", "NUMENTER": "Num Enter"}


def key_name(key_id, label: str) -> str:
    if key_id in _SIDE_NAMES:
        return _SIDE_NAMES[key_id]
    if isinstance(key_id, int) and (0x60 <= key_id <= 0x6F or key_id == 0x90) and label != "Num":
        return f"Num {label}"
    return label


def normalize_key(vk: int, extended: bool):
    """Приводит событие хука к идентификатору клавиши раскладки."""
    if vk == 0x0D and extended:
        return "NUMENTER"
    if not extended and vk in _NUMPAD_NAV:
        return _NUMPAD_NAV[vk]
    return vk


class KeyboardStep(Step):
    step_id = "keyboard"
    title = "Клавиатура и тачпад"
    hint = ("Нажмите по очереди каждую клавишу — она станет зелёной. Системные сочетания (Win, Alt+Tab, "
            "PrintScreen) на время теста перехватываются. Клавиши, которых на этом ноутбуке нет, отметьте "
            "правым кликом по схеме. Клавиша Fn не посылает код в ОС — её проверяют в сочетаниях "
            "(Fn+F-клавиши). Чтобы ввести комментарий, кликните в поле «Комментарий» — перехват приостановится.")

    COLOR_IDLE, COLOR_DONE, COLOR_DOWN, COLOR_STUCK, COLOR_NA = "#f4f6f9", "#4cc27a", "#f2c94c", "#e06464", "#d7dbe2"

    def build(self, body):
        self.pressed: set = set()        # клавиши, нажатые хотя бы раз
        self.absent: set = set()         # клавиши, которых физически нет на этом ноутбуке (ПКМ по схеме)
        self.down_now: set = set()       # удерживаемые в данный момент
        self.stuck: list[str] = []       # подписи залипших клавиш
        self.other_keys: set[str] = set()  # нажатые клавиши вне схемы (мультимедиа и т. п.)
        self.touch = {"left": False, "right": False, "double": False, "scroll": False}
        self._events: queue.Queue = queue.Queue()
        self._hook = KeyboardHook(lambda vk, scan, ext, down: self._events.put((vk, ext, down)))
        self._stuck_samples: list[set[int]] = []
        self._items: dict = {}           # id клавиши -> id прямоугольника на canvas
        self._texts: dict = {}           # id клавиши -> id подписи на canvas
        self._stuck_ids: set[int] = set()
        self._last = None                # последняя нажатая клавиша

        opts = ttk.Frame(body)
        opts.pack(fill="x")
        self.iso_var = tk.BooleanVar(value=False)
        self.numpad_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(opts, text="ISO (доп. клавиша у левого Shift)", variable=self.iso_var,
                        command=self._redraw, takefocus=False).pack(side="left")
        ttk.Checkbutton(opts, text="Есть Numpad", variable=self.numpad_var,
                        command=self._redraw, takefocus=False).pack(side="left", padx=14)
        ttk.Button(opts, text="Сбросить отметки", command=self._reset, takefocus=False).pack(side="right")
        self.block_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(opts, text="Перехват системных клавиш", variable=self.block_var,
                        command=self._toggle_block, takefocus=False).pack(side="right", padx=14)

        self.info = ttk.Label(body, text="", font=self.app.font_bold)
        self.info.pack(anchor="w", pady=(8, 2))
        self.stuck_label = tk.Label(body, text="", anchor="w", justify="left", bg="#fdecec", fg="#a12b2b",
                                    font=self.app.font_bold, padx=8, pady=4)
        self.canvas = tk.Canvas(body, height=self.app.px(270), bg="#ffffff", highlightthickness=1,
                                highlightbackground="#c9d0db")
        self.canvas.pack(fill="x", pady=(4, 8))
        self.canvas.bind("<Configure>", lambda e: self._redraw())
        # Клик по схеме или зоне тачпада забирает фокус из поля комментария -> перехват возобновляется
        self.canvas.bind("<Button-1>", lambda e: self.canvas.focus_set())

        # --- Тачпад -----------------------------------------------------
        tp = ttk.Frame(body)
        tp.pack(fill="both", expand=True)
        self.pad = tk.Canvas(tp, bg="#eef2f8", highlightthickness=1, highlightbackground="#c9d0db",
                             height=self.app.px(120))
        self.pad.pack(side="left", fill="both", expand=True)
        self.pad_text = self.pad.create_text(12, 12, anchor="nw", fill="#4a5568", font=self.app.font_normal,
                                             text="Зона проверки тачпада:\nлевый клик, правый клик, двойной клик, "
                                                  "прокрутка двумя пальцами")
        self.pad.bind("<Button-1>", lambda e: (self.pad.focus_set(), self._touch("left")))
        self.pad.bind("<Button-3>", lambda e: self._touch("right"))
        self.pad.bind("<Double-Button-1>", lambda e: self._touch("double"))
        self.pad.bind("<MouseWheel>", lambda e: self._touch("scroll"))
        self.pad.bind("<Button-4>", lambda e: self._touch("scroll"))  # колесо в Linux (для отладки)
        self.pad.bind("<Button-5>", lambda e: self._touch("scroll"))
        side = ttk.Frame(tp)
        side.pack(side="left", fill="y", padx=(12, 0))
        self.touch_labels = {}
        for key, text in (("left", "Левый клик"), ("right", "Правый клик"),
                          ("double", "Двойной клик"), ("scroll", "Прокрутка")):
            lbl = ttk.Label(side, text="○ " + text, foreground="#8a94a6")
            lbl.pack(anchor="w", pady=2)
            self.touch_labels[key] = (lbl, text)


    # --- жизненный цикл ---------------------------------------------------
    def on_show(self):
        self._redraw()
        if not IS_WINDOWS:
            self.info.configure(text="Перехват клавиатуры доступен только в Windows")
            return
        # Пока курсор в поле комментария, хук ничего не перехватывает — иначе дефект было бы не описать
        entry = self.app.comment_entry
        self._bindings = [(entry, "<FocusIn>", entry.bind("<FocusIn>", lambda e: self._set_paused(True), add="+")),
                          (entry, "<FocusOut>", entry.bind("<FocusOut>", lambda e: self._set_paused(False), add="+"))]
        self._hook.paused = False
        # Сначала 2 секунды слушаем залипшие клавиши. Хук включаем ПОСЛЕ проверки:
        # заблокированные хуком нажатия не видны через GetAsyncKeyState.
        self._stuck_samples = []
        self.info.configure(text="Проверка залипания: 2 секунды НЕ нажимайте клавиши…")
        self._sample_stuck()

    def on_hide(self):
        self.cancel_timers()
        self._hook.stop()
        self.down_now.clear()
        for widget, sequence, func_id in getattr(self, "_bindings", ()):
            try:
                widget.unbind(sequence, func_id)
            except tk.TclError:
                pass
        self._bindings = []

    def _set_paused(self, paused: bool):
        self._hook.paused = paused
        self.down_now.clear()
        self._redraw()

    def _sample_stuck(self):
        self._stuck_samples.append(pressed_keys())
        if len(self._stuck_samples) < 20:      # 20 замеров × 100 мс = 2 с
            self.later(100, self._sample_stuck)
            return
        # Залипшей считаем клавишу, нажатую минимум в 80 % замеров
        counts: dict[int, int] = {}
        for sample in self._stuck_samples:
            for vk in sample:
                counts[vk] = counts.get(vk, 0) + 1
        stuck_vks = [vk for vk, n in counts.items() if n >= 16]
        labels = {key_id: key_name(key_id, label) for label, key_id, *_ in build_layout(True, True)}
        self.stuck = [str(labels.get(vk) or _VK_NAMES.get(vk) or f"VK 0x{vk:02X}") for vk in stuck_vks]
        self._stuck_ids = set(stuck_vks)
        if self.stuck:
            self.stuck_label.configure(text="⚠ Обнаружены клавиши, нажатые без участия пользователя (возможное "
                                            "залитие): " + ", ".join(self.stuck))
            self.stuck_label.pack(fill="x", before=self.canvas)
            self.app.set_auto_status(self, WARNING)
        else:
            self.stuck_label.pack_forget()
        self._redraw()
        self._hook.block = self.block_var.get()
        if not self._hook.start():
            self.info.configure(text="Не удалось включить перехват: " + (self._hook.error or "неизвестная ошибка"))
            return
        self._update_info()
        self._pump()

    def _pump(self):
        """Переносит события из потока хука в GUI."""
        changed = False
        try:
            while True:
                vk, ext, down = self._events.get_nowait()
                if vk == 0xFF:
                    continue
                key = normalize_key(vk, ext)
                # Отмечаем и по нажатию, и по отпусканию: некоторые клавиши (PrintScreen)
                # на части систем присылают только отпускание.
                self.pressed.add(key)
                self.absent.discard(key)   # клавиша сработала — значит, она всё-таки есть
                self._last = key
                if key not in self._items:
                    self.other_keys.add(_VK_NAMES.get(vk, f"VK 0x{vk:02X}"))
                if down:
                    self.down_now.add(key)
                else:
                    self.down_now.discard(key)
                self._paint(key)
                changed = True
        except queue.Empty:
            pass
        if changed:
            self._update_info()
        self.later(30, self._pump)

    # --- отрисовка --------------------------------------------------------
    def _layout(self):
        return build_layout(self.iso_var.get(), self.numpad_var.get())

    def _redraw(self):
        c = self.canvas
        c.delete("all")
        self._items.clear()
        layout = self._layout()
        total_w = max(x + w for _, _, x, _, w, _ in layout)
        width = max(c.winfo_width(), 200)
        unit = min((width - 16) / total_w, self.app.px(44))
        c.configure(height=int(unit * 6.5 + 16))
        ox = (width - unit * total_w) / 2
        font = (self.app.font_family, max(6, int(unit * 0.24)))
        for label, key_id, x, y, w, h in layout:
            x0, y0 = ox + x * unit + 2, 8 + y * unit + 2
            rect = c.create_rectangle(x0, y0, x0 + w * unit - 4, y0 + h * unit - 4,
                                      fill=self.COLOR_NA if key_id is None else self.COLOR_IDLE, outline="#9aa5b5")
            text = c.create_text(x0 + (w * unit - 4) / 2, y0 + (h * unit - 4) / 2, text=label, font=font,
                                 fill="#1c2430")
            if key_id is not None:
                self._items[key_id] = rect
                self._texts[key_id] = text
                for item in (rect, text):
                    c.tag_bind(item, "<Button-3>", lambda e, k=key_id: self._toggle_absent(k))
                self._paint(key_id)
        self._update_info()

    def _paint(self, key_id):
        rect = self._items.get(key_id)
        if rect is None:
            return
        if key_id in self.down_now:
            color = self.COLOR_DOWN
        elif key_id in self._stuck_ids:
            color = self.COLOR_STUCK      # залипшая остаётся красной, даже если «нажималась»
        elif key_id in self.pressed:
            color = self.COLOR_DONE
        elif key_id in self.absent:
            color = self.COLOR_NA
        else:
            color = self.COLOR_IDLE
        self.canvas.itemconfigure(rect, fill=color)
        self.canvas.itemconfigure(self._texts[key_id],
                                  fill="#9aa5b5" if color == self.COLOR_NA else "#1c2430")

    def _toggle_absent(self, key_id):
        if key_id in self.pressed:
            return
        self.absent.symmetric_difference_update({key_id})
        self._paint(key_id)
        self._update_info()

    def _counts(self) -> tuple[int, int]:
        """(нажато, всего) без учёта клавиш, отмеченных как отсутствующие."""
        keys = [k for k in self._items if k not in self.absent]
        return sum(1 for k in keys if k in self.pressed), len(keys)

    def _update_info(self):
        if not self._hook.active:
            return
        if self._hook.paused:
            self.info.configure(text="Перехват приостановлен: ввод комментария. Чтобы продолжить тест, "
                                     "кликните по схеме клавиатуры.")
            return
        done, total = self._counts()
        text = f"Проверено клавиш: {done} из {total}"
        if self._last is not None:
            labels = {key_id: key_name(key_id, label) for label, key_id, *_ in self._layout()}
            name = labels.get(self._last) or _VK_NAMES.get(self._last) or (
                f"VK 0x{self._last:02X}" if isinstance(self._last, int) else self._last)
            text += f"   ·   последняя: {name}"
        if not self.block_var.get():
            text += "   ·   перехват системных клавиш выключен"
        self.info.configure(text=text)

    # --- действия ---------------------------------------------------------
    def _toggle_block(self):
        self._hook.block = self.block_var.get()
        self._update_info()

    def _reset(self):
        self.pressed.clear()
        self.absent.clear()
        self.other_keys.clear()
        self._last = None
        for key in self.touch:
            self.touch[key] = False
            lbl, text = self.touch_labels[key]
            lbl.configure(text="○ " + text, foreground="#8a94a6")
        self._redraw()

    def _touch(self, kind: str):
        self.touch[kind] = True
        lbl, text = self.touch_labels[kind]
        lbl.configure(text="✔ " + text, foreground="#1f9d55")

    # --- результаты -------------------------------------------------------
    def _missing(self) -> list[str]:
        return [key_name(key_id, label) for label, key_id, *_ in self._layout()
                if key_id is not None and key_id not in self.pressed and key_id not in self.absent]

    def collect(self):
        layout = self._layout()
        keys = [k[1] for k in layout if k[1] is not None and k[1] not in self.absent]
        return {
            "layout": "ISO" if self.iso_var.get() else "ANSI",
            "numpad": self.numpad_var.get(),
            "keys_total": len(keys),
            "keys_pressed": sum(1 for k in keys if k in self.pressed),
            "keys_not_pressed": self._missing(),
            "keys_marked_absent": [key_name(k[1], k[0]) for k in layout if k[1] in self.absent],
            "other_keys_pressed": sorted(self.other_keys),
            "stuck_keys": self.stuck,
            "touchpad": dict(self.touch),
        }

    def summary(self):
        data = self.collect()
        missing = data["keys_not_pressed"]
        names = {"left": "левый клик", "right": "правый клик", "double": "двойной клик", "scroll": "прокрутка"}
        rows = [("Нажато клавиш", f"{data['keys_pressed']} из {data['keys_total']}")]
        if missing and data["keys_pressed"]:
            rows.append(("Не нажаты", ", ".join(missing)))
        if self.stuck:
            rows.append(("Залипшие клавиши", ", ".join(self.stuck)))
        ok = [names[k] for k, v in self.touch.items() if v]
        bad = [names[k] for k, v in self.touch.items() if not v]
        rows.append(("Тачпад", ("проверено: " + ", ".join(ok) if ok else "не проверялся")
                     + ("; не проверено: " + ", ".join(bad) if ok and bad else "")))
        return rows
