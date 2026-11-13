"""Этап 5: тест динамиков (левый/правый канал) и микрофона (VU-метр, запись и воспроизведение)."""

from __future__ import annotations

import math
import time
import tkinter as tk
from tkinter import ttk

from ..common import NOT_FOUND
from .base import Step

RATE = 44100
TONE_HZ = 440
RECORD_SECONDS = 3


class AudioStep(Step):
    step_id = "audio"
    title = "Звук: динамики и микрофон"
    hint = ("Звук должен идти строго из указанного динамика, без хрипов и дребезга. "
            "Затем скажите что-нибудь в микрофон: шкала должна реагировать, а запись — звучать чисто.")

    def build(self, body):
        self.sd = None
        self.np = None
        self.error = ""
        self.has_output = False
        self.has_input = False
        self.played = {"left": False, "right": False}
        self.mic_peak = 0.0          # максимальный уровень за время теста (0..1)
        self.recorded = False
        self._level = 0.0            # текущий уровень для VU-метра
        self._stream = None
        self._rec_chunks: list | None = None
        self._rec_until = 0.0
        self._inited = False

        spk = ttk.LabelFrame(body, text="Динамики (стерео-тест, 440 Гц)", padding=12)
        spk.pack(fill="x", pady=(0, 12))
        self.btn_left = ttk.Button(spk, text="◀  Тест левого канала (Left)", style="Big.TButton",
                                   command=lambda: self._play_tone("left"))
        self.btn_left.pack(side="left")
        self.btn_right = ttk.Button(spk, text="Тест правого канала (Right)  ▶", style="Big.TButton",
                                    command=lambda: self._play_tone("right"))
        self.btn_right.pack(side="left", padx=12)
        self.spk_info = ttk.Label(spk, text="", wraplength=self.app.px(330), justify="left")
        self.spk_info.pack(side="left", padx=8)

        mic = ttk.LabelFrame(body, text="Микрофон", padding=12)
        mic.pack(fill="x")
        ttk.Label(mic, text="Уровень входного сигнала:").pack(anchor="w")
        self.vu = tk.Canvas(mic, height=self.app.px(26), bg="#11161d", highlightthickness=0)
        self.vu.pack(fill="x", pady=6)
        self.vu_bar = self.vu.create_rectangle(0, 0, 0, 100, fill="#4cc27a", width=0)
        row = ttk.Frame(mic)
        row.pack(fill="x", pady=(6, 0))
        self.btn_rec = ttk.Button(row, text=f"●  Записать {RECORD_SECONDS} сек и воспроизвести",
                                  style="Big.TButton", command=self._record)
        self.btn_rec.pack(side="left")
        self.mic_info = ttk.Label(row, text="")
        self.mic_info.pack(side="left", padx=12)

    # --- инициализация звуковой подсистемы ------------------------------------
    def _init_audio(self):
        self._inited = True
        try:
            import numpy as np
            import sounddevice as sd
            self.np, self.sd = np, sd
        except Exception as exc:  # нет библиотек или PortAudio
            self.error = f"Звуковая подсистема недоступна: {exc}"
            return
        try:
            self.has_output = self.sd.query_devices(kind="output")["max_output_channels"] > 0
        except Exception:
            self.has_output = False
        try:
            self.has_input = self.sd.query_devices(kind="input")["max_input_channels"] > 0
        except Exception:
            self.has_input = False

    def on_show(self):
        if not self._inited:
            self._init_audio()
        if self.error:
            self.spk_info.configure(text=self.error)
        if not self.has_output:
            self.btn_left.state(["disabled"])
            self.btn_right.state(["disabled"])
            if not self.error:
                self.spk_info.configure(text="Устройство вывода не обнаружено")
        if not self.has_input:
            self.btn_rec.state(["disabled"])
            self.mic_info.configure(text="Микрофон: устройство не обнаружено")
        if not self.has_output and not self.has_input:
            self.app.set_auto_status(self, NOT_FOUND)
        if self.has_input:
            self._open_input()
        self._tick()

    def on_hide(self):
        self.cancel_timers()
        self._close_input()
        if self.sd is not None:
            try:
                self.sd.stop()
            except Exception:
                pass

    # --- динамики ---------------------------------------------------------
    def _play_tone(self, side: str):
        np = self.np
        try:
            t = np.arange(int(RATE * 1.5)) / RATE
            tone = 0.35 * np.sin(2 * math.pi * TONE_HZ * t)
            fade = int(RATE * 0.02)   # плавное нарастание/затухание, чтобы не было щелчков
            tone[:fade] *= np.linspace(0, 1, fade)
            tone[-fade:] *= np.linspace(1, 0, fade)
            stereo = np.zeros((len(tone), 2), dtype="float32")
            stereo[:, 0 if side == "left" else 1] = tone
            self.sd.play(stereo, RATE)
            self.played[side] = True
            self.spk_info.configure(text="▶ Играет ЛЕВЫЙ канал" if side == "left" else "▶ Играет ПРАВЫЙ канал")
            self._tone_id = getattr(self, "_tone_id", 0) + 1
            tone_id = self._tone_id
            # Через 1,6 с убираем надпись (если за это время не запустили другой канал)
            self.later(1600, lambda: self.spk_info.configure(text="") if tone_id == self._tone_id else None)
        except Exception as exc:
            self.spk_info.configure(text=f"Ошибка воспроизведения: {exc}")

    # --- микрофон ---------------------------------------------------------
    def _open_input(self):
        if self._stream is not None:
            return

        def callback(indata, frames, time_info, status):
            # Вызывается из звукового потока PortAudio — только быстрые операции
            peak = float(abs(indata).max()) if frames else 0.0
            self._level = max(peak, self._level * 0.85)
            self.mic_peak = max(self.mic_peak, peak)
            chunks = self._rec_chunks
            if chunks is not None:
                chunks.append(indata.copy())

        try:
            self._stream = self.sd.InputStream(samplerate=RATE, channels=1, dtype="float32", callback=callback)
            self._stream.start()
        except Exception as exc:
            self._stream = None
            self.has_input = False
            self.btn_rec.state(["disabled"])
            self.mic_info.configure(text=f"Микрофон недоступен: {exc}")

    def _close_input(self):
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None
        self._rec_chunks = None
        self._level = 0.0

    def _record(self):
        if self._stream is None or self._rec_chunks is not None:
            return
        self._rec_chunks = []
        self._rec_until = time.monotonic() + RECORD_SECONDS
        self.btn_rec.state(["disabled"])
        self._record_wait()

    def _record_wait(self):
        left = self._rec_until - time.monotonic()
        if left > 0:
            self.mic_info.configure(text=f"Идёт запись… {left:.1f} с — говорите в микрофон")
            self.later(100, self._record_wait)
            return
        chunks, self._rec_chunks = self._rec_chunks, None
        self.btn_rec.state(["!disabled"])
        if not chunks:
            self.mic_info.configure(text="Микрофон не передал данные")
            return
        try:
            data = self.np.concatenate(chunks)
            self.recorded = True
            peak = float(abs(data).max())
            if self.has_output:
                self.sd.play(data, RATE)
                self.mic_info.configure(text=f"Воспроизведение записи (пик сигнала {peak * 100:.0f} %)")
            else:
                self.mic_info.configure(text="Запись сделана, но устройство вывода не обнаружено")
            if peak < 0.005:
                self.mic_info.configure(text="Запись пустая: микрофон не улавливает звук (или отключён в системе)")
        except Exception as exc:
            self.mic_info.configure(text=f"Ошибка: {exc}")

    def _tick(self):
        # Логарифмическая шкала −60…0 дБ
        level = self._level
        db = 20 * math.log10(level) if level > 1e-6 else -120.0
        frac = min(max((db + 60) / 60, 0.0), 1.0)
        width = self.vu.winfo_width()
        self.vu.coords(self.vu_bar, 0, 0, width * frac, self.vu.winfo_height())
        self.vu.itemconfigure(self.vu_bar, fill="#e06464" if frac > 0.95 else ("#f2c94c" if frac > 0.8 else "#4cc27a"))
        self.later(50, self._tick)

    def collect(self):
        return {"output_found": self.has_output, "input_found": self.has_input,
                "left_played": self.played["left"], "right_played": self.played["right"],
                "mic_peak_percent": round(self.mic_peak * 100, 1), "loopback_recorded": self.recorded,
                "error": self.error}

    def summary(self):
        if self._inited and self.error:
            return [("Звук", self.error)]
        yes = lambda flag: "да" if flag else "нет"  # noqa: E731
        return [
            ("Динамики", ("левый канал воспроизведён: %s, правый: %s" % (yes(self.played["left"]), yes(self.played["right"])))
             if self.has_output or not self._inited else "устройство не обнаружено"),
            ("Микрофон", (f"пик сигнала {self.mic_peak * 100:.0f} %, запись с воспроизведением: {yes(self.recorded)}")
             if self.has_input or not self._inited else "устройство не обнаружено"),
        ]
