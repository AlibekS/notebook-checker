"""Модуль 7: генерация акта приёмки — самодостаточный HTML (печать в PDF из браузера) + сырой JSON."""

from __future__ import annotations

import html
import json
import re
from datetime import datetime
from pathlib import Path

from . import APP_NAME, APP_VERSION
from .common import (DEFECT, NOT_FOUND, PASSED, PENDING, SKIPPED, STATUS_COLORS,
                     STATUS_LABELS, WARNING, reports_dir)
from .sysinfo import findings, passport_sections

# В отчёте статус «Ошибка» называется «Дефект», как требует ТЗ
REPORT_LABELS = dict(STATUS_LABELS, **{PASSED: "Пройден", DEFECT: "Дефект", PENDING: "Не проводился"})

_CSS = """
*{box-sizing:border-box}
body{font-family:"Segoe UI",Arial,sans-serif;color:#1c2430;background:#eef1f5;margin:0;padding:24px;font-size:14px;line-height:1.45}
.page{max-width:900px;margin:0 auto;background:#fff;padding:36px 44px;border-radius:8px;box-shadow:0 2px 12px rgba(0,0,0,.08)}
h1{font-size:22px;margin:0 0 4px}
h2{font-size:16px;margin:28px 0 10px;padding-bottom:6px;border-bottom:2px solid #1c2430}
h3{font-size:13px;margin:16px 0 6px;color:#4a5568;text-transform:uppercase;letter-spacing:.04em}
.meta{color:#5a6577;margin-bottom:6px}
table{width:100%;border-collapse:collapse;margin:0 0 6px}
td,th{padding:6px 10px;border:1px solid #d5dbe4;text-align:left;vertical-align:top;overflow-wrap:anywhere}
th{background:#f3f5f8;font-weight:600}
td.k{width:36%;color:#3b4656}
.badge{display:inline-block;padding:2px 10px;border-radius:10px;color:#fff;font-size:12px;font-weight:600;white-space:nowrap}
.lv-ok{color:#1f7a45}.lv-warn{color:#a86700;font-weight:600}.lv-bad{color:#c22f2f;font-weight:700}
.verdict{padding:12px 16px;border-radius:6px;margin:16px 0;font-weight:600;border:1px solid}
.verdict.good{background:#e8f6ee;border-color:#1f9d55;color:#176b3b}
.verdict.bad{background:#fdecec;border-color:#d64545;color:#a12b2b}
.verdict.warn{background:#fff6e0;border-color:#d98e04;color:#8a5a00}
ul.find{margin:6px 0;padding-left:20px}
.details{color:#4a5568;font-size:13px}
.check{font-size:16px;font-weight:700}
.sign{display:flex;gap:40px;margin-top:44px}
.sign div{flex:1}
.sign .line{border-bottom:1px solid #1c2430;height:44px}
.sign small{color:#5a6577}
.footer{margin-top:28px;color:#8a94a6;font-size:12px;text-align:center}
.print-btn{float:right;padding:8px 16px;border:0;border-radius:6px;background:#2563eb;color:#fff;font-size:14px;cursor:pointer}
tr,.sign{page-break-inside:avoid}
@media print{body{background:#fff;padding:0}.page{box-shadow:none;padding:0;max-width:none}.print-btn{display:none}
 .badge,.verdict{-webkit-print-color-adjust:exact;print-color-adjust:exact}}
"""


def _e(value) -> str:
    return html.escape("" if value is None else str(value))


def _badge(status: str) -> str:
    return (f'<span class="badge" style="background:{STATUS_COLORS.get(status, "#8a94a6")}">'
            f'{_e(REPORT_LABELS.get(status, status))}</span>')


def safe_filename_part(text: str, fallback: str) -> str:
    """Убирает из строки символы, недопустимые в именах файлов Windows."""
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f\s]+', "_", (text or "").strip()).strip("._")
    return cleaned[:60] or fallback


def report_basename(record: dict) -> str:
    """YYYY-MM-DD_<SerialNumber>_<Фамилия>"""
    date = record["generated_at"][:10]
    serial = (record.get("passport", {}).get("identity") or {}).get("serial") or ""
    surname = (record.get("parties", {}).get("employee") or "").split(" ")[0]
    return f"{date}_{safe_filename_part(serial, 'NOSERIAL')}_{safe_filename_part(surname, 'Сотрудник')}"


def overall_verdict(record: dict) -> tuple[str, str]:
    """(css-класс, текст) общего заключения."""
    statuses = [s["status"] for s in record["steps"]]
    unchecked = [i for i in record["inspection"]["items"] if not i["checked"]]
    defects = statuses.count(DEFECT)
    if defects or unchecked:
        parts = []
        if defects:
            parts.append(f"тестов с дефектами: {defects}")
        if unchecked:
            parts.append(f"замечаний визуального осмотра: {len(unchecked)}")
        return "bad", "Ноутбук принят с замечаниями — " + ", ".join(parts)
    incomplete = [s for s in statuses if s in (WARNING, SKIPPED, PENDING, NOT_FOUND)]
    if incomplete:
        return "warn", (f"Дефектов не зафиксировано, но {len(incomplete)} из {len(statuses)} "
                        "проверок с предупреждением, пропущены или недоступны")
    return "good", "Все проверки пройдены, дефектов не обнаружено"


def build_html(record: dict) -> str:
    ident = record.get("passport", {}).get("identity") or {}
    parties = record["parties"]
    dt = datetime.fromisoformat(record["generated_at"])
    out: list[str] = []
    w = out.append
    w('<!DOCTYPE html><html lang="ru"><head><meta charset="utf-8">')
    w('<meta name="viewport" content="width=device-width, initial-scale=1">')
    w(f"<title>Акт приёмки ноутбука {_e(ident.get('serial') or '')}</title><style>{_CSS}</style></head>")
    w('<body><div class="page">')
    w('<button class="print-btn" onclick="window.print()">Печать / PDF</button>')
    w("<h1>Акт приёмки корпоративного ноутбука</h1>")
    w(f'<div class="meta">Дата и время: <b>{dt:%d.%m.%Y %H:%M}</b></div>')
    device = " ".join(x for x in (ident.get("manufacturer"), ident.get("model")) if x) or "—"
    w(f'<div class="meta">Устройство: <b>{_e(device)}</b>, серийный номер: '
      f'<b>{_e(ident.get("serial") or "—")}</b></div>')
    w(f'<div class="meta">Сдал: <b>{_e(parties.get("employee") or "—")}</b> · '
      f'Принял: <b>{_e(parties.get("it") or "—")}</b></div>')
    cls, text = overall_verdict(record)
    w(f'<div class="verdict {cls}">{_e(text)}</div>')

    # --- Паспорт -------------------------------------------------------------
    w("<h2>1. Паспорт устройства</h2>")
    for title, rows in passport_sections(record.get("passport", {})):
        w(f"<h3>{_e(title)}</h3><table>")
        for label, value, level in rows:
            w(f'<tr><td class="k">{_e(label)}</td><td class="lv-{level}">{_e(value)}</td></tr>')
        w("</table>")
    notes = findings(record.get("passport", {}))
    if notes:
        w("<h3>Автоматически выявленные замечания</h3><ul class='find'>")
        for level, text in notes:
            w(f'<li class="lv-{level}">{_e(text)}</li>')
        w("</ul>")

    # --- Результаты тестов -------------------------------------------------
    w("<h2>2. Результаты тестов</h2>")
    w('<table><tr><th style="width:26%">Тест</th><th style="width:18%">Результат</th>'
      "<th>Комментарий и детали</th></tr>")
    for step in record["steps"]:
        details = "".join(f"<div class='details'>{_e(k)}: {_e(v)}</div>" for k, v in step.get("summary", []))
        comment = f"<div><b>{_e(step['comment'])}</b></div>" if step.get("comment") else ""
        w(f"<tr><td>{_e(step['title'])}</td><td>{_badge(step['status'])}</td>"
          f"<td>{comment}{details}</td></tr>")
    w("</table>")

    # --- Визуальный осмотр ---------------------------------------------------
    w("<h2>3. Визуальный осмотр</h2><table>")
    for item in record["inspection"]["items"]:
        mark = ('<span class="check lv-ok">✔</span> да' if item["checked"]
                else '<span class="check lv-bad">✖</span> НЕТ')
        w(f'<tr><td>{_e(item["text"])}</td><td style="width:16%">{mark}</td></tr>')
    w("</table>")
    if parties.get("comment"):
        w(f"<h3>Общий комментарий</h3><div>{_e(parties['comment']).replace(chr(10), '<br>')}</div>")

    # --- Подписи -------------------------------------------------------------
    w("<h2>4. Подписи сторон</h2>")
    w('<div class="sign">')
    for role, name in (("Сдал (сотрудник)", parties.get("employee")), ("Принял (IT-специалист)", parties.get("it"))):
        w(f'<div><b>{_e(role)}</b><div class="line"></div>'
          f"<small>{_e(name or '')} / подпись, дата</small></div>")
    w("</div>")
    w(f'<div class="footer">Сформировано {APP_NAME} {APP_VERSION} · {dt:%d.%m.%Y %H:%M:%S}</div>')
    w("</div></body></html>")
    return "\n".join(out)


def save_report(record: dict, directory: Path | None = None) -> tuple[Path, Path]:
    """Сохраняет HTML и JSON в ./Reports. Существующие файлы не перезаписывает (добавляет _2, _3…)."""
    directory = directory or reports_dir()
    base = report_basename(record)
    name, n = base, 1
    while (directory / f"{name}.html").exists() or (directory / f"{name}.json").exists():
        n += 1
        name = f"{base}_{n}"
    html_path, json_path = directory / f"{name}.html", directory / f"{name}.json"
    html_path.write_text(build_html(record), encoding="utf-8")
    json_path.write_text(json.dumps(record, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return html_path, json_path
