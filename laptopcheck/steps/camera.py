"""Этап 4: тест веб-камеры (OpenCV). Кадры выводятся в tkinter без Pillow — через PPM."""

from __future__ import annotations

import os
import threading
import time
import tkinter as tk
from tkinter import ttk

from ..common import IS_WINDOWS, NOT_FOUND
from .base import Step

os.environ.setdefault("OPENCV_LOG_LEVEL", "ERROR")
# Без этого флага камеры на Media Foundation открываются по 10–20 секунд
os.environ.setdefault("OPENCV_VIDEOIO_MSMF_ENABLE_HW_TRANSFORMS", "0")

MAX_PROBE = 4            # сколько индексов камер опрашивать
PREVIEW_W, PREVIEW_H = 640, 400


class CameraStep(Step):
    step_id = "camera"
    title = "Веб-камера"
    hint = "Проверьте, что изображение резкое, без полос, пятен и артефактов, а индикатор камеры горит."

    def build(self, body):
        self.cameras: list[int] = []
        self.resolution = ""
        self.fps = 0.0
        self.frames_total = 0
        self.error = ""
        self._detected = False
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._frame = None       # последний кадр в виде PPM (bytes)
        self._photo = None

        bar = ttk.Frame(body)
        bar.pack(fill="x")
        ttk.Label(bar, text="Камера:").pack(side="left")
        self.combo = ttk.Combobox(bar, state="readonly", width=24)
        self.combo.pack(side="left", padx=6)
        self.combo.bind("<<ComboboxSelected>>", lambda e: self._start_stream())
        ttk.Button(bar, text="Найти камеры заново", command=self._detect).pack(side="left", padx=6)
        self.info = ttk.Label(body, text="", font=self.app.font_bold)
        self.info.pack(anchor="w", pady=6)
        self.view = tk.Label(body, bg="#11161d", fg="#c9d0db", text="Нет изображения",
                             width=PREVIEW_W, height=PREVIEW_H, image=self._blank())
        self.view.pack(anchor="w")

    def _blank(self):
        self._blank_img = tk.PhotoImage(width=PREVIEW_W, height=PREVIEW_H)
        return self._blank_img

    # --- жизненный цикл ---------------------------------------------------
    def on_show(self):
        if self._detected and self.cameras:
            self._start_stream()
        elif not self._detected:
            self._detect()
        self._tick()

    def on_hide(self):
        self.cancel_timers()
        self._stop_stream()

    # --- поиск камер ------------------------------------------------------
    def _detect(self):
        self._stop_stream()
        self.info.configure(text="Поиск камер…")
        self._found: list[int] | None = None

        def work():
            found = []
            try:
                import cv2
                for index in range(MAX_PROBE):
                    cap = cv2.VideoCapture(index, cv2.CAP_DSHOW) if IS_WINDOWS else cv2.VideoCapture(index)
                    try:
                        if cap.isOpened() and cap.read()[0]:
                            found.append(index)
                    finally:
                        cap.release()
            except Exception as exc:  # нет OpenCV, нет драйвера и т. п. — не падаем
                self.error = f"{type(exc).__name__}: {exc}"
            self._found = found

        threading.Thread(target=work, daemon=True, name="camera-detect").start()
        self._wait_detect()

    def _wait_detect(self):
        if self._found is None:
            self.later(150, self._wait_detect)
            return
        self._detected = True
        self.cameras = self._found
        if not self.cameras:
            text = "Устройство не обнаружено"
            if self.error:
                text += f" ({self.error})"
            self.info.configure(text=text)
            self.combo.configure(values=[])
            self.combo.set("")
            self.app.set_auto_status(self, NOT_FOUND)
            return
        self.combo.configure(values=[f"Камера {i}" for i in self.cameras])
        self.combo.current(0)
        if self.winfo_ismapped():
            self._start_stream()

    # --- видеопоток -------------------------------------------------------
    def _start_stream(self):
        self._stop_stream()
        if not self.cameras:
            return
        index = self.cameras[max(self.combo.current(), 0)]
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._capture_loop, args=(index, self._stop),
                                        daemon=True, name="camera-stream")
        self._thread.start()

    def _stop_stream(self):
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(2.0)
        self._thread = None

    def _capture_loop(self, index: int, stop: threading.Event):
        """Фоновый поток: читает кадры, уменьшает и готовит PPM для показа."""
        try:
            import cv2
            cap = cv2.VideoCapture(index, cv2.CAP_DSHOW) if IS_WINDOWS else cv2.VideoCapture(index)
        except Exception as exc:
            self.error = str(exc)
            return
        try:
            # Просим HD — камера сама выберет ближайший поддерживаемый режим
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
            count, started, failures = 0, time.monotonic(), 0
            while not stop.is_set():
                ok, frame = cap.read()
                if not ok or frame is None:
                    failures += 1
                    if failures > 30:
                        self.error = "камера перестала отдавать кадры"
                        break
                    time.sleep(0.03)
                    continue
                failures = 0
                h, w = frame.shape[:2]
                self.resolution = f"{w}×{h}"
                scale = min(PREVIEW_W / w, PREVIEW_H / h, 1.0)
                if scale < 1.0:
                    frame = cv2.resize(frame, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                ppm = b"P6 %d %d 255\n" % (rgb.shape[1], rgb.shape[0]) + rgb.tobytes()
                with self._lock:
                    self._frame = ppm
                count += 1
                self.frames_total += 1
                elapsed = time.monotonic() - started
                if elapsed >= 1.0:
                    self.fps = count / elapsed
                    count, started = 0, time.monotonic()
        except Exception as exc:
            self.error = f"{type(exc).__name__}: {exc}"
        finally:
            cap.release()

    def _tick(self):
        """Главный поток: показывает последний готовый кадр."""
        with self._lock:
            ppm, self._frame = self._frame, None
        if ppm is not None:
            try:
                self._photo = tk.PhotoImage(data=ppm, format="ppm")
                self.view.configure(image=self._photo, text="")
            except tk.TclError:
                pass
        if self._thread and self._thread.is_alive():
            self.info.configure(text=f"Разрешение: {self.resolution or '—'}   ·   FPS: {self.fps:.0f}")
        elif self._detected and self.cameras and self.error:
            self.info.configure(text="Ошибка камеры: " + self.error)
        self.later(33, self._tick)

    def collect(self):
        return {"cameras_found": len(self.cameras), "resolution": self.resolution,
                "fps": round(self.fps, 1), "frames_received": self.frames_total, "error": self.error}

    def summary(self):
        if self._detected and not self.cameras:
            return [("Камера", "устройство не обнаружено")]
        if not self.frames_total:
            return [("Камера", "видеопоток не запускался")]
        return [("Найдено камер", str(len(self.cameras))), ("Разрешение", self.resolution),
                ("FPS", f"{self.fps:.0f}")]
