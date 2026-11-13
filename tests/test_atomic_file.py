import json
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from modules import atomic_file as af  # noqa: E402


def refused(*_):
    raise PermissionError(5, "Access is denied")


class ReplaceRetryTests(unittest.TestCase):
    def test_retries_until_the_reader_lets_go(self):
        calls, slept = [], []

        def replace(tmp, path):
            calls.append((tmp, path))
            if len(calls) < 3:
                refused()

        self.assertEqual(af.replace_file("a.tmp", "a.json", replace=replace, sleep=slept.append), 2)
        self.assertEqual(len(calls), 3)
        self.assertEqual(slept, list(af.RETRY_DELAYS[:2]))

    def test_gives_up_after_the_last_delay(self):
        calls = []

        def replace(tmp, path):
            calls.append(1)
            refused()

        with self.assertRaises(PermissionError):
            af.replace_file("a.tmp", "a.json", delays=(0, 0, 0), replace=replace, sleep=lambda s: None)
        self.assertEqual(len(calls), 4)

    def test_other_errors_are_not_retried(self):
        calls = []

        def replace(tmp, path):
            calls.append(1)
            raise FileNotFoundError(2, "missing")

        with self.assertRaises(FileNotFoundError):
            af.replace_file("a.tmp", "a.json", replace=replace, sleep=lambda s: None)
        self.assertEqual(len(calls), 1)

    def test_total_wait_fits_a_five_second_sampling_loop(self):
        self.assertLessEqual(sum(af.RETRY_DELAYS), 1.0)


class HeartbeatTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "status" / "training_status.json"

    def test_writes_json_and_leaves_no_temp_file(self):
        log = []
        self.assertTrue(af.write_heartbeat(self.path, {"ok": True, "name": "盯盘"}, log=log.append))
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8")), {"ok": True, "name": "盯盘"})
        self.assertEqual([p.name for p in self.path.parent.iterdir()], ["training_status.json"])
        self.assertEqual(log, [])

    def _hazard_or_skip(self, handle):
        """The failure seen in production: replacing a file another handle has open."""
        probe = self.path.with_suffix(".probe")
        probe.write_text("{}", encoding="utf-8")
        try:
            os.replace(probe, self.path)
        except PermissionError:
            probe.unlink()
            return
        handle.close()
        self.skipTest("this system lets os.replace overwrite an open file")

    @unittest.skipUnless(os.name == "nt", "Windows file-sharing behaviour")
    def test_a_file_held_open_skips_the_update_and_does_not_raise(self):
        af.write_heartbeat(self.path, {"n": 1}, log=lambda m: None)
        log = []
        with open(self.path, "r", encoding="utf-8") as handle:
            self._hazard_or_skip(handle)
            self.assertFalse(af.write_heartbeat(self.path, {"n": 2}, log=log.append, delays=(0.01, 0.01)))
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8")), {"n": 1})   # old heartbeat kept
        self.assertEqual(len(log), 1)
        self.assertIn("update skipped (training_status.json)", log[0])
        self.assertTrue(af.write_heartbeat(self.path, {"n": 3}, log=log.append))        # next cycle recovers
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8")), {"n": 3})

    @unittest.skipUnless(os.name == "nt", "Windows file-sharing behaviour")
    def test_a_short_reader_only_delays_the_update(self):
        af.write_heartbeat(self.path, {"n": 1}, log=lambda m: None)
        log = []
        handle = open(self.path, "r", encoding="utf-8")
        self._hazard_or_skip(handle)
        timer = threading.Timer(0.08, handle.close)
        timer.start()
        try:
            self.assertTrue(af.write_heartbeat(self.path, {"n": 2}, log=log.append))
        finally:
            timer.join()
            handle.close()
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8")), {"n": 2})
        self.assertEqual(len(log), 1)
        self.assertIn("written after", log[0])


class ServicesUseTheHelperTests(unittest.TestCase):
    def test_status_writers_of_running_services_cannot_stop_them(self):
        for name in ("qmt_l2_training_recorder_v2.py", "qmt_research_recorder_v3.py",
                     "qmt_research_recorder.py", "collection_monitor.py"):
            text = (ROOT / "services" / name).read_text(encoding="utf-8")
            self.assertIn("write_heartbeat(", text, name)
            self.assertNotIn(".replace(STATUS_PATH)", text, name)
            self.assertNotIn("os.replace(tmp, path)", text, name)


if __name__ == "__main__":
    unittest.main()
