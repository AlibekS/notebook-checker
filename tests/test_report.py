"""Проверки логики, не зависящей от Windows: анализ паспорта и генерация акта.

Запуск: python -m unittest discover tests
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from laptopcheck import report, sysinfo  # noqa: E402
from laptopcheck.steps.keyboard import build_layout, normalize_key  # noqa: E402

PASSPORT = {
    "identity": {"manufacturer": "Dell Inc.", "model": "Latitude 5420", "serial": "ABC/12:3",
                 "cpu": "Intel Core i5", "ram_total": 16 * 1024 ** 3, "ram_modules": 16 * 1024 ** 3},
    "battery": {"present": True, "design": 60000, "full": 27000, "cycles": 800, "ac_online": False},
    # одиночный диск PowerShell отдаёт объектом, а не массивом
    "disks": {"disks": {"model": "SSD", "size": 256 * 1024 ** 3, "health": "Healthy",
                        "predict_failure": True, "wear": 95, "temperature": 40}},
    "devices": {"devices": []},
    "security": {"error": "PowerShell не ответил за 90 с"},
}


def make_record(**parties):
    return {
        "generated_at": "2026-01-15T10:30:00",
        "parties": {"employee": "Иванов Иван", "it": "Петров Пётр", "comment": "<b>тест</b>", **parties},
        "passport": PASSPORT,
        "steps": [{"id": "keyboard", "title": "Клавиатура", "status": "defect", "comment": "Залипает <Enter>",
                   "summary": [("Нажато клавиш", "10 из 80")], "data": {}}],
        "inspection": {"items": [{"text": "Корпус цел", "checked": True}]},
    }


class PassportTests(unittest.TestCase):
    def test_battery_health(self):
        self.assertEqual(sysinfo.battery_health({"design": 60000, "full": 27000}), 45.0)
        self.assertIsNone(sysinfo.battery_health({"design": 0, "full": 100}))
        self.assertIsNone(sysinfo.battery_health({}))

    def test_findings_levels(self):
        notes = sysinfo.findings(PASSPORT)
        text = "\n".join(t for _, t in notes)
        self.assertIn("PredictFailure", text)
        self.assertIn("45 %", text)
        self.assertIn("PowerShell не ответил", text)
        self.assertTrue(any(level == "bad" for level, _ in notes))

    def test_missing_and_broken_sections_do_not_raise(self):
        sysinfo.passport_sections({})
        sysinfo.passport_sections({"battery": {"present": False}, "disks": {"disks": None},
                                   "identity": {"ram_modules": "мусор"}})


class KeyboardTests(unittest.TestCase):
    def test_layout_ids_unique(self):
        for iso in (False, True):
            ids = [k[1] for k in build_layout(iso, True) if k[1] is not None]
            self.assertEqual(len(ids), len(set(ids)))

    def test_normalize(self):
        self.assertEqual(normalize_key(0x0D, True), "NUMENTER")
        self.assertEqual(normalize_key(0x0D, False), 0x0D)
        self.assertEqual(normalize_key(0x24, False), 0x67)   # Home без extended = Numpad 7
        self.assertEqual(normalize_key(0x24, True), 0x24)    # настоящий Home


class ReportTests(unittest.TestCase):
    def test_filename_is_sanitized(self):
        self.assertEqual(report.report_basename(make_record()), "2026-01-15_ABC_12_3_Иванов")

    def test_save_escapes_and_does_not_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            html1, json1 = report.save_report(make_record(), Path(tmp))
            html2, _ = report.save_report(make_record(), Path(tmp))
            self.assertNotEqual(html1, html2)
            text = html1.read_text(encoding="utf-8")
            self.assertIn("&lt;b&gt;тест&lt;/b&gt;", text)
            self.assertIn("Залипает &lt;Enter&gt;", text)
            self.assertNotIn("<b>тест</b>", text)
            self.assertEqual(json.loads(json1.read_text(encoding="utf-8"))["parties"]["it"], "Петров Пётр")

    def test_verdict(self):
        record = make_record()
        self.assertEqual(report.overall_verdict(record)[0], "bad")
        record["steps"][0]["status"] = "passed"
        self.assertEqual(report.overall_verdict(record)[0], "good")
        record["steps"][0]["status"] = "skipped"
        self.assertEqual(report.overall_verdict(record)[0], "warn")


if __name__ == "__main__":
    unittest.main()
