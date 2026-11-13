"""Низкоуровневый перехват клавиатуры Windows (WH_KEYBOARD_LL) через ctypes.

Хук живёт в отдельном потоке с собственным циклом сообщений. Пока включена
блокировка и окно программы активно, нажатия «съедаются» (Win, Alt+Tab,
PrintScreen и т. п. не срабатывают в системе), а события передаются в callback.
Ctrl+Alt+Del перехватить невозможно — это ограничение Windows.
"""

from __future__ import annotations

import ctypes
import os
import threading
from typing import Callable

from .common import IS_WINDOWS

WH_KEYBOARD_LL = 13
WM_KEYDOWN, WM_SYSKEYDOWN = 0x0100, 0x0104
WM_QUIT = 0x0012
LLKHF_EXTENDED = 0x01

if IS_WINDOWS:
    from ctypes import wintypes

    _user32 = ctypes.WinDLL("user32", use_last_error=True)
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    class KBDLLHOOKSTRUCT(ctypes.Structure):
        _fields_ = [
            ("vkCode", wintypes.DWORD),
            ("scanCode", wintypes.DWORD),
            ("flags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", ctypes.c_void_p),
        ]

    _LRESULT = ctypes.c_ssize_t
    _HOOKPROC = ctypes.WINFUNCTYPE(_LRESULT, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)

    # Явные прототипы обязательны: на x64 дескрипторы не помещаются в int по умолчанию.
    _user32.SetWindowsHookExW.argtypes = (ctypes.c_int, _HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD)
    _user32.SetWindowsHookExW.restype = wintypes.HHOOK
    _user32.CallNextHookEx.argtypes = (wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
    _user32.CallNextHookEx.restype = _LRESULT
    _user32.UnhookWindowsHookEx.argtypes = (wintypes.HHOOK,)
    _user32.UnhookWindowsHookEx.restype = wintypes.BOOL
    _user32.GetMessageW.argtypes = (ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT)
    _user32.GetMessageW.restype = ctypes.c_int
    _user32.PostThreadMessageW.argtypes = (wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
    _user32.PostThreadMessageW.restype = wintypes.BOOL
    _user32.GetForegroundWindow.restype = wintypes.HWND
    _user32.GetWindowThreadProcessId.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
    _user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    _user32.GetAsyncKeyState.argtypes = (ctypes.c_int,)
    _user32.GetAsyncKeyState.restype = ctypes.c_short
    _kernel32.GetModuleHandleW.argtypes = (wintypes.LPCWSTR,)
    _kernel32.GetModuleHandleW.restype = wintypes.HMODULE
    _kernel32.GetCurrentThreadId.restype = wintypes.DWORD


def _our_window_is_foreground() -> bool:
    """True, если активное окно принадлежит нашему процессу."""
    hwnd = _user32.GetForegroundWindow()
    if not hwnd:
        return False
    pid = wintypes.DWORD(0)
    _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value == os.getpid()


class KeyboardHook:
    """callback(vk, scan, extended, is_down) вызывается из потока хука — он должен быть быстрым
    (Windows снимает «медленные» хуки), поэтому в нём только кладём событие в очередь."""

    def __init__(self, callback: Callable[[int, int, bool, bool], None]):
        self._callback = callback
        self._thread: threading.Thread | None = None
        self._thread_id = 0
        self._ready = threading.Event()
        self.block = True       # глушить ли нажатия для системы
        self.active = False     # установлен ли хук
        self.error = ""

    def start(self) -> bool:
        if not IS_WINDOWS:
            self.error = "Перехват клавиатуры доступен только в Windows"
            return False
        if self._thread and self._thread.is_alive():
            return True
        self._ready.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name="keyboard-hook")
        self._thread.start()
        self._ready.wait(3.0)
        return self.active

    def stop(self) -> None:
        if self._thread and self._thread.is_alive() and self._thread_id:
            _user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
            self._thread.join(2.0)
        self._thread = None
        self.active = False

    def _run(self) -> None:
        self._thread_id = _kernel32.GetCurrentThreadId()

        def proc(n_code, w_param, l_param):
            if n_code == 0:  # HC_ACTION
                try:
                    info = ctypes.cast(l_param, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
                    is_down = w_param in (WM_KEYDOWN, WM_SYSKEYDOWN)
                    foreground = _our_window_is_foreground()
                    if foreground:
                        self._callback(info.vkCode, info.scanCode,
                                       bool(info.flags & LLKHF_EXTENDED), is_down)
                    # Глушим клавиши только пока активно наше окно, чтобы
                    # не «убить» клавиатуру для остальных программ.
                    if self.block and foreground:
                        return 1
                except Exception:
                    pass  # исключение в хуке не должно блокировать клавиатуру
            return _user32.CallNextHookEx(None, n_code, w_param, l_param)

        c_proc = _HOOKPROC(proc)  # держим ссылку, иначе сборщик мусора удалит callback
        hook = _user32.SetWindowsHookExW(WH_KEYBOARD_LL, c_proc, _kernel32.GetModuleHandleW(None), 0)
        if not hook:
            self.error = f"SetWindowsHookEx: ошибка {ctypes.get_last_error()}"
            self._ready.set()
            return
        self.active = True
        self._ready.set()
        msg = wintypes.MSG()
        # Цикл сообщений обязателен: без него низкоуровневый хук не вызывается.
        while _user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            pass
        _user32.UnhookWindowsHookEx(hook)
        self.active = False


def pressed_keys() -> set[int]:
    """Виртуальные коды клавиш, физически нажатых прямо сейчас (для детектора залипания).

    Кнопки мыши (VK 1–6) и обобщённые Shift/Ctrl/Alt (0x10–0x12) исключены:
    вместо последних Windows отдельно сообщает левый/правый вариант.
    """
    if not IS_WINDOWS:
        return set()
    return {vk for vk in range(8, 256)
            if vk not in (0x10, 0x11, 0x12, 0xFF) and _user32.GetAsyncKeyState(vk) & 0x8000}
