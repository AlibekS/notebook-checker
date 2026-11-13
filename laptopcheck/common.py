"""Общие константы и вспомогательные функции: статусы шагов, пути, запуск PowerShell."""

from __future__ import annotations

import base64
import json
import subprocess
import sys
from pathlib import Path

IS_WINDOWS = sys.platform == "win32"

# --- Статусы этапов мастера -------------------------------------------------
PENDING = "pending"
PASSED = "passed"
DEFECT = "defect"
WARNING = "warning"
SKIPPED = "skipped"
NOT_FOUND = "not_found"

STATUS_LABELS = {
    PENDING: "Ожидает",
    PASSED: "Пройдено",
    DEFECT: "Ошибка",
    WARNING: "Предупреждение",
    SKIPPED: "Пропущено",
    NOT_FOUND: "Устройство не обнаружено",
}
STATUS_ICONS = {
    PENDING: "○",
    PASSED: "✔",
    DEFECT: "✖",
    WARNING: "⚠",
    SKIPPED: "–",
    NOT_FOUND: "∅",
}
STATUS_COLORS = {
    PENDING: "#8a94a6",
    PASSED: "#1f9d55",
    DEFECT: "#d64545",
    WARNING: "#d98e04",
    SKIPPED: "#8a94a6",
    NOT_FOUND: "#7a5cc7",
}


# --- Пути ---------------------------------------------------------------------
def app_dir() -> Path:
    """Папка, где лежит LaptopCheck.exe (или корень проекта при запуске из исходников)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def reports_dir() -> Path:
    """./Reports рядом с исполняемым файлом. Все отчёты пишутся только сюда."""
    path = app_dir() / "Reports"
    path.mkdir(parents=True, exist_ok=True)
    return path


# --- PowerShell -----------------------------------------------------------------
class PowerShellError(RuntimeError):
    pass


_PS_PRELUDE = (
    "$ErrorActionPreference='SilentlyContinue';"
    "$ProgressPreference='SilentlyContinue';"
    "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8;\n"
)


def run_powershell(script: str, timeout: float = 90.0) -> str:
    """Выполняет PowerShell-скрипт без всплывающего консольного окна и возвращает stdout.

    Скрипт передаётся через -EncodedCommand (Base64 от UTF-16LE), поэтому
    не нужно экранировать кавычки, а на диск ноутбука не пишется ни одного файла.
    """
    if not IS_WINDOWS:
        raise PowerShellError("PowerShell доступен только в Windows")
    encoded = base64.b64encode((_PS_PRELUDE + script).encode("utf-16-le")).decode("ascii")
    try:
        proc = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
             "-EncodedCommand", encoded],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            creationflags=0x08000000,  # CREATE_NO_WINDOW
        )
    except subprocess.TimeoutExpired as exc:
        raise PowerShellError(f"PowerShell не ответил за {int(timeout)} с") from exc
    except OSError as exc:
        raise PowerShellError(f"Не удалось запустить PowerShell: {exc}") from exc
    return proc.stdout.decode("utf-8-sig", errors="replace")


def run_powershell_json(script: str, timeout: float = 90.0):
    """Выполняет скрипт, который печатает JSON (ConvertTo-Json), и разбирает результат."""
    out = run_powershell(script, timeout).strip()
    # Перед JSON теоретически может оказаться посторонний вывод — ищем начало объекта.
    starts = [i for i in (out.find("{"), out.find("[")) if i >= 0]
    if not starts:
        raise PowerShellError("PowerShell вернул пустой ответ")
    try:
        return json.loads(out[min(starts):])
    except json.JSONDecodeError as exc:
        raise PowerShellError(f"Не удалось разобрать ответ PowerShell: {exc}") from exc


def as_list(value) -> list:
    """PowerShell сворачивает массив из одного элемента в объект — нормализуем обратно."""
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def fmt_bytes(num) -> str:
    """Человекочитаемый объём (ГБ/ТБ)."""
    try:
        num = float(num)
    except (TypeError, ValueError):
        return "—"
    if num <= 0:
        return "—"
    gb = num / 1024 ** 3
    if gb >= 1000:
        return f"{gb / 1024:.2f} ТБ"
    return f"{gb:.0f} ГБ" if gb >= 10 else f"{gb:.1f} ГБ"
