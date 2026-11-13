import json
import sys
import unittest
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from modules.market_clock import SHANGHAI  # noqa: E402
from modules.trading_calendar import phase_of  # noqa: E402
from services import collection_monitor as cm  # noqa: E402

REGISTRY = json.loads((ROOT / "deploy" / "services.json").read_text(encoding="utf-8"))
CFG = json.loads((ROOT / "configs" / "collection.json").read_text(encoding="utf-8"))


def context(workers):
    now_dt = datetime(2026, 11, 13, 10, 0, tzinfo=SHANGHAI)
    return cm.Context(now=now_dt.timestamp(), now_dt=now_dt, trading_day=True, calendar_basis="qmt",
                      phase=phase_of(now_dt), basket=["A.SH"], training={}, raw={},
                      status={"updated_at": now_dt.isoformat()},
                      processes={"available": True, "workers": workers, "miniqmt": True},
                      registry=REGISTRY, integrity={"ok": True}, ac=True, power_risks=[], free_gb=100.0, cfg=CFG)


class ServiceRegistryTests(unittest.TestCase):
    def test_sixty_second_model_services_are_switched_off_and_collectors_are_not(self):
        services = {s["id"]: s for s in REGISTRY["services"]}
        self.assertEqual(len(services), len(REGISTRY["services"]), "service ids must be unique")
        disabled = {sid for sid, s in services.items() if s.get("enabled") is False}
        self.assertEqual(disabled, {"autotrain", "v6_shadow"})
        for sid, s in services.items():
            if s.get("collects_market_data") or sid == "collection_monitor":
                self.assertIsNot(s.get("enabled"), False, f"{sid} must stay enabled")
            if s.get("enabled") is False:
                self.assertTrue(s.get("disabled_reason"))

    def test_monitor_does_not_expect_disabled_services(self):
        running = {"bridge": 1, "training_recorder": 1, "research_recorder": 1, "autotrain": 0, "v6_shadow": 0}
        keys = {f.key for f in cm.evaluate(context(running))}
        self.assertFalse({k for k in keys if k.startswith("PROCESS_")}, keys)

    def test_monitor_flags_a_disabled_service_that_runs(self):
        running = {"bridge": 1, "training_recorder": 1, "research_recorder": 1, "autotrain": 1, "v6_shadow": 0}
        found = {f.key: f.severity for f in cm.evaluate(context(running))}
        self.assertEqual(found.get("PROCESS_DISABLED_autotrain"), "WARN")
        self.assertNotIn("PROCESS_DISABLED_v6_shadow", found)

    def test_installer_and_launcher_honour_the_flag_and_start_detached(self):
        installer = (ROOT / "deploy" / "install_runtime.ps1").read_text(encoding="utf-8")
        self.assertIn("$svc.enabled -eq $false", installer)
        self.assertIn("Win32_Process", installer)          # services are not children of the installer
        self.assertTrue(installer.isascii(), "installer must stay ASCII for Windows PowerShell 5.1")
        launcher = (ROOT / "services" / "start_monitor.ps1").read_text(encoding="utf-8-sig")
        self.assertIn("$_.enabled -ne $false", launcher)
        self.assertIn("Win32_Process", launcher)


if __name__ == "__main__":
    unittest.main()
