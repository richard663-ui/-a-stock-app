import re
import sys
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from modules import resource_guard as rg  # noqa: E402


class ResourceGuardTests(unittest.TestCase):
    def test_limits(self):
        self.assertEqual(rg.over_limit(None, 100, 100), "")
        self.assertEqual(rg.over_limit({"memory_mb": 50, "handles": 50}, 100, 100), "")
        self.assertIn("memory", rg.over_limit({"memory_mb": 150, "handles": 50}, 100, 100))
        self.assertIn("handles", rg.over_limit({"memory_mb": 50, "handles": 150}, 100, 100))

    def test_guard_triggers_callback_instead_of_running_forever(self):
        fired = threading.Event()
        reasons = []
        rg.start_guard("test", max_memory_mb=100, max_handles=100, interval=0.01,
                       sampler=lambda: {"memory_mb": 500, "handles": 1},
                       on_exceed=lambda reason: (reasons.append(reason), fired.set()))
        self.assertTrue(fired.wait(2))
        self.assertIn("memory", reasons[0])

    @unittest.skipUnless(sys.platform == "win32", "Windows counters")
    def test_real_usage_is_sane(self):
        sample = rg.usage()
        self.assertGreater(sample["memory_mb"], 1)
        self.assertGreater(sample["handles"], 1)
        self.assertLess(sample["memory_mb"], rg.DEFAULT_MAX_MEMORY_MB)

    def test_no_service_subscribes_with_full_history(self):
        # count=-1 makes QMT load the whole tick history per subscription (40 GB incidents).
        pattern = re.compile(r"subscribe_quote\([^)]*count\s*=\s*-1")
        offenders = [str(p.relative_to(ROOT)) for folder in ("services", "modules")
                     for p in (ROOT / folder).glob("*.py") if pattern.search(p.read_text(encoding="utf-8"))]
        self.assertEqual(offenders, [])

    def test_watchdog_template_has_crash_loop_brake(self):
        text = (ROOT / "deploy" / "install_runtime.ps1").read_text(encoding="utf-8")
        self.assertIn("FAILS% GEQ 3", text)
        self.assertNotIn("timeout /t", text)


if __name__ == "__main__":
    unittest.main()
