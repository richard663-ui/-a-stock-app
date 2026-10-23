import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from modules import power_events as pe  # noqa: E402
from modules.alerts import Alert, AlertDispatcher, build_webhook_request  # noqa: E402
from modules.paths import runtime_dir  # noqa: E402

NS = "xmlns='http://schemas.microsoft.com/win/2004/08/events/event'"
XML = (
    f"<Event {NS}><System><Provider Name='Microsoft-Windows-Kernel-Power'/><EventID>506</EventID>"
    "<TimeCreated SystemTime='2026-10-21T19:34:51.2229732Z'/></System><EventData>"
    "<Data Name='Reason'>1</Data><Data Name='LidOpenState'>true</Data></EventData></Event>"
    f"<Event {NS}><System><Provider Name='Microsoft-Windows-Kernel-Power'/><EventID>507</EventID>"
    "<TimeCreated SystemTime='2026-10-22T20:41:26.1Z'/></System><EventData>"
    "<Data Name='Reason'>16777220</Data><Data Name='ActiveResidencyInUs'>60000000</Data>"
    "<Data Name='DripsResidencyInUs'>90000000000</Data></EventData></Event>"
)
POWERCFG_ZH = """电源设置 GUID: 5ca83367-6e45-459f-a27b-476b1d01c936  (合上盖子操作)
      GUID 别名: LIDACTION
    当前交流电源设置索引: 0x00000001
    当前直流电源设置索引: 0x00000001
    电源设置 GUID: 7648efa3-dd9c-4e3e-b566-50f929386280  (电源按钮操作)
    当前交流电源设置索引: 0x00000000
    当前直流电源设置索引: 0x00000001
"""
POWERCFG_EN = """    Power Setting GUID: 7648efa3-dd9c-4e3e-b566-50f929386280  (Power button action)
    Current AC Power Setting Index: 0x00000004
    Current DC Power Setting Index: 0x00000001
"""


class PowerEventTests(unittest.TestCase):
    def test_parse_and_pair_standby(self):
        events = pe.parse_standby_events(XML)
        self.assertEqual([e["kind"] for e in events], ["enter", "exit"])
        self.assertEqual(events[0]["reason"], "Power Button")
        intervals = pe.pair_intervals(events, datetime(2026, 10, 21, tzinfo=timezone.utc),
                                      datetime(2026, 10, 23, tzinfo=timezone.utc))
        self.assertEqual(len(intervals), 1)
        self.assertTrue(intervals[0]["user_initiated"])
        self.assertAlmostEqual(intervals[0]["seconds"], 90395.0, delta=2)
        self.assertEqual(intervals[0]["drips_seconds"], 90000.0)

    def test_powercfg_parsing_in_two_languages(self):
        zh = pe.parse_powercfg_buttons(POWERCFG_ZH)
        self.assertEqual(zh["lid_close"], {"ac": "sleep", "dc": "sleep"})
        self.assertEqual(zh["power_button"], {"ac": "do_nothing", "dc": "sleep"})
        en = pe.parse_powercfg_buttons(POWERCFG_EN)
        self.assertEqual(en["power_button"]["ac"], "turn_off_display")

    def test_idle_sleep_and_scheme_parsing(self):
        text = ("电源方案 GUID: 825585a6-6528-44a3-8084-5183791a29bf  (Razer Cortex Power Plan)\n"
                "      最小可能的设置: 0x00000000\n      最大可能的设置: 0xffffffff\n"
                "      可能的设置增量: 0x00000001\n      可能的设置单位: 秒\n"
                "    当前交流电源设置索引: 0x00000258\n    当前直流电源设置索引: 0x00000000\n")
        self.assertEqual(pe.parse_powercfg_seconds(text), {"ac": 600, "dc": 0})
        self.assertEqual(pe.parse_powercfg_seconds("no values"), {"ac": None, "dc": None})
        scheme = pe.parse_active_scheme("Power Scheme GUID: 381b4222-f694-41f0-9685-ff5bb260df2e  (Balanced)")
        self.assertEqual(scheme, {"guid": "381b4222-f694-41f0-9685-ff5bb260df2e", "name": "Balanced"})

    def test_boot_events(self):
        xml = (f"<Event {NS}><System><Provider Name='Microsoft-Windows-Kernel-Power'/><EventID>41</EventID>"
               "<TimeCreated SystemTime='2026-09-12T00:41:00.0Z'/></System></Event>")
        self.assertEqual(pe.parse_boot_events(xml)[0]["kind"], "unexpected_restart")


class AlertTests(unittest.TestCase):
    def alert(self, severity="CRITICAL"):
        return Alert(key="INGEST_ALL", severity=severity, title="无数据", message="no samples")

    def test_webhook_formats(self):
        url, body, headers = build_webhook_request({"url": "https://ntfy.sh/t", "format": "ntfy"}, self.alert())
        self.assertEqual(headers["Priority"], "urgent")
        self.assertTrue(headers["Title"].isascii())
        self.assertIn("无数据", body.decode("utf-8"))
        _, body, _ = build_webhook_request({"url": "https://api.day.app/k", "format": "bark"}, self.alert())
        self.assertEqual(json.loads(body)["level"], "timeSensitive")
        _, body, headers = build_webhook_request({"url": "https://sctapi.ftqq.com/k.send", "format": "serverchan"}, self.alert())
        self.assertIn("application/x-www-form-urlencoded", headers["Content-Type"])
        self.assertIsNone(build_webhook_request({"url": "http://insecure", "format": "json"}, self.alert()))

    def test_dispatch_respects_threshold_and_survives_failures(self):
        calls = []

        def poster(url, data, headers):
            calls.append(url)
            raise OSError("offline")

        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "alerts.jsonl"
            d = AlertDispatcher(log, {"webhook": {"url": "https://x.example/hook", "min_severity": "CRITICAL"}},
                                poster=poster, toaster=lambda a: None)
            d.dispatch(self.alert("WARN"))
            self.assertEqual(calls, [])
            record = d.dispatch(self.alert("CRITICAL"))
            self.assertEqual(record["delivered"], ["webhook_failed:OSError"])
            self.assertEqual(len(log.read_text(encoding="utf-8").splitlines()), 2)


class PathTests(unittest.TestCase):
    def test_runtime_dir_prefers_environment(self):
        import os
        with tempfile.TemporaryDirectory() as tmp:
            old = os.environ.get("ASTOCK_RUNTIME_DIR")
            os.environ["ASTOCK_RUNTIME_DIR"] = str(Path(tmp) / "rt")
            try:
                self.assertEqual(runtime_dir(), Path(tmp) / "rt")
                self.assertTrue((Path(tmp) / "rt").is_dir())
            finally:
                if old is None:
                    os.environ.pop("ASTOCK_RUNTIME_DIR")
                else:
                    os.environ["ASTOCK_RUNTIME_DIR"] = old


if __name__ == "__main__":
    unittest.main()
