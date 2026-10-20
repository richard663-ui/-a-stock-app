import unittest
from modules.collection_runtime import RecorderPowerGuard, CollectionContinuity


class Backend:
    def __init__(self):
        self.ac = True
        self.calls = []

    def ac_connected(self):
        return self.ac

    def set_required(self, required):
        self.calls.append(required)


class CollectionRuntimeTests(unittest.TestCase):
    def test_request_lives_in_recorder_and_releases_on_battery_or_shutdown(self):
        backend = Backend()
        guard = RecorderPowerGuard(backend, True)
        self.assertTrue(guard.update()["active"])
        guard.update()
        self.assertEqual(backend.calls, [True])
        backend.ac = False
        self.assertFalse(guard.update()["active"])
        backend.ac = True
        guard.update()
        guard.close()
        self.assertEqual(backend.calls, [True, False, True, False])
        self.assertFalse(guard.status()["display_required"])
        self.assertFalse(guard.status()["global_power_plan_changed"])

    def test_opt_out_and_api_failure_are_visible(self):
        backend = Backend()
        guard = RecorderPowerGuard(backend, False)
        self.assertFalse(guard.update()["active"])
        self.assertEqual(backend.calls, [])
        def fail():
            raise OSError("test failure")
        backend.ac_connected = fail
        result = RecorderPowerGuard(backend, True).update()
        self.assertEqual(result["reason"], "power_request_failed")
        self.assertTrue(result["error"])

    def test_loop_resume_and_backward_clock_are_not_continuous(self):
        monitor = CollectionContinuity()
        self.assertFalse(monitor.observe_loop(100))
        self.assertFalse(monitor.observe_loop(101))
        self.assertTrue(monitor.observe_loop(200))
        self.assertTrue(monitor.observe_loop(199))
        self.assertEqual(monitor.status()["loop_gap_count"], 2)

    def test_duplicate_tick_is_not_a_gap_but_missing_interval_is(self):
        monitor = CollectionContinuity()
        self.assertFalse(monitor.observe_tick("A", 100))
        self.assertFalse(monitor.observe_tick("A", 100))
        self.assertFalse(monitor.observe_tick("A", 103))
        self.assertTrue(monitor.observe_tick("A", 120))
        self.assertFalse(monitor.observe_tick("B", 120))
        self.assertEqual(monitor.status()["feed_gap_count"], 1)


if __name__ == "__main__":
    unittest.main()
