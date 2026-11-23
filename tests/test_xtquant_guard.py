import importlib.util
import json
import socket
import sys
import tempfile
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from modules import xtquant_guard as g  # noqa: E402

TICK = ["600105.SH"]


class FakeClient:
    def __init__(self):
        self.connected = True

    def is_connected(self):
        return self.connected


class Clock:
    def __init__(self):
        self.now = 1000.0
        self.slept = []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.slept.append(seconds)
        self.now += seconds


def fake_xtdata(up=False):
    """Shaped like xtquant.xtdata: its functions look up get_client when they are called."""
    mod = types.ModuleType("fake_xtdata")
    mod.up = up
    mod.connects = 0

    def get_client():
        client = vars(mod).get("__client")
        if client is not None and client.is_connected():
            return client
        mod.connects += 1                       # a real xtquant connect (leaks when it fails)
        if not mod.up:
            raise Exception(g.DOWN_MESSAGE)
        vars(mod)["__client"] = FakeClient()
        return vars(mod)["__client"]

    mod.get_client = get_client
    exec("def get_full_tick(codes):\n    return {code: get_client() for code in codes}\n", vars(mod))
    return mod


def guard_for(mod, clock, port=lambda: False, log=None):
    guard = g.ConnectGuard(get_client=mod.get_client,
                           current_client=lambda: vars(mod).get("__client"),
                           probe=port, clock=clock, sleep=clock.sleep,
                           log=log.append if log is not None else (lambda message: None))
    mod.get_client = guard
    return guard


def call(mod):
    try:
        mod.get_full_tick(TICK)
        return True
    except Exception as exc:
        assert "无法连接xtquant服务" in str(exc), exc
        return False


def run_for(mod, clock, minutes, poll=0.2):
    """Call like a recorder for a while; return the clock times of real connects."""
    attempts, end = [], clock.now + minutes * 60
    while clock.now < end:
        before = mod.connects
        call(mod)
        if mod.connects > before:
            attempts.append(clock.now)
        clock.now += poll
    return attempts


class GuardTests(unittest.TestCase):
    def test_a_connected_client_is_returned_as_before(self):
        mod, clock = fake_xtdata(up=True), Clock()
        guard_for(mod, clock, port=lambda: True)
        for _ in range(5):
            self.assertTrue(call(mod))
        self.assertEqual(mod.connects, 1)
        self.assertEqual(clock.slept, [])

    def test_while_nothing_listens_calls_wait_and_fail_without_connecting(self):
        mod, clock, log = fake_xtdata(), Clock(), []
        guard = guard_for(mod, clock, log=log)
        for _ in range(3):
            start = clock.now
            self.assertFalse(call(mod))
            self.assertAlmostEqual(clock.now - start, g.SKIP_DELAY)   # the old 2 s pace
        self.assertEqual(mod.connects, 0)
        self.assertEqual(guard.skipped, 3)
        self.assertEqual(len(log), 1)
        self.assertIn("QMT unreachable", log[0])

    def test_a_long_outage_makes_no_real_connect(self):
        mod, clock, log = fake_xtdata(), Clock(), []
        guard_for(mod, clock, log=log)
        self.assertEqual(run_for(mod, clock, minutes=24 * 60, poll=0.2), [])   # a whole day
        self.assertEqual(len(log), 1)

    def test_failing_connects_on_an_open_port_back_off_to_one_a_minute(self):
        mod, clock = fake_xtdata(), Clock()
        guard_for(mod, clock, port=lambda: True)  # something listens but xtquant cannot connect
        attempts = run_for(mod, clock, minutes=15)
        gaps = [b - a for a, b in zip(attempts, attempts[1:])]
        self.assertEqual(gaps, sorted(gaps))
        self.assertLessEqual(max(gaps), g.MAX_WAIT + g.SKIP_DELAY + 0.2)
        self.assertGreaterEqual(gaps[-1], g.MAX_WAIT)
        self.assertLess(len(attempts), 20)        # was one leaking connect per call: ~400

    def test_reconnects_as_soon_as_the_port_opens(self):
        mod, clock, log = fake_xtdata(), Clock(), []
        port = {"open": False}
        guard_for(mod, clock, port=lambda: port["open"], log=log)
        self.assertFalse(call(mod))
        mod.up = port["open"] = True              # QMT logged in again
        start = clock.now
        self.assertTrue(call(mod))
        self.assertEqual(clock.now, start)        # no wait once the port is open
        self.assertEqual(mod.connects, 1)
        self.assertIn("reachable again after 0 failed connect(s); 1 call(s)", log[-1])
        self.assertTrue(call(mod))                # back on the fast path
        self.assertEqual(mod.connects, 1)

    def test_port_opening_during_a_wait_ends_the_wait(self):
        mod, clock = fake_xtdata(), Clock()
        start = {}

        def probe():
            return clock.now - start.get("t", clock.now) >= 1.0

        guard_for(mod, clock, port=probe)
        self.assertFalse(call(mod))
        mod.up = True
        start["t"] = clock.now
        self.assertTrue(call(mod))
        self.assertAlmostEqual(clock.now - start["t"], 1.0)

    def test_a_lost_connection_is_noticed_without_connecting(self):
        mod, clock = fake_xtdata(up=True), Clock()
        port = {"open": True}
        guard = guard_for(mod, clock, port=lambda: port["open"])
        self.assertTrue(call(mod))
        vars(mod)["__client"].connected = False   # QMT went away mid-session
        mod.up = port["open"] = False
        self.assertFalse(call(mod))
        self.assertTrue(guard.down)
        self.assertEqual(mod.connects, 1)

    def test_port_is_checked_every_half_second_while_waiting(self):
        mod, clock = fake_xtdata(), Clock()
        checks = []

        def probe():
            checks.append(clock.now)
            return False

        guard_for(mod, clock, port=probe)
        call(mod)
        self.assertEqual(len(checks), int(g.SKIP_DELAY / g.PROBE_EVERY) + 1)

    def test_back_off_never_overflows(self):
        mod, clock = fake_xtdata(), Clock()
        guard = guard_for(mod, clock, port=lambda: True)
        guard.failures, guard.next_attempt = 5000, 0.0
        self.assertFalse(call(mod))
        self.assertLessEqual(guard.next_attempt - clock.now, g.MAX_WAIT)


class InstallTests(unittest.TestCase):
    def test_install_is_idempotent_and_reaches_module_functions(self):
        mod = fake_xtdata(up=True)
        first = g.install(mod, log=lambda m: None, probe=lambda: True)
        self.assertIs(g.install(mod, log=lambda m: None, probe=lambda: True), first)
        self.assertIs(mod.get_full_tick.__globals__["get_client"], first)
        self.assertTrue(call(mod))
        self.assertEqual(mod.connects, 1)

    @unittest.skipUnless(importlib.util.find_spec("xtquant"), "xtquant not installed")
    def test_real_xtdata_functions_go_through_the_guard(self):
        from xtquant import xtdata
        g.install(xtdata)
        self.assertTrue(getattr(xtdata.get_client, "is_qmt_connect_guard", False))
        for name in ("get_full_tick", "subscribe_quote", "get_market_data_ex"):
            self.assertIs(getattr(xtdata, name).__globals__["get_client"], xtdata.get_client, name)


class AddressTests(unittest.TestCase):
    def test_open_and_closed_port(self):
        server = socket.socket()
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        port = server.getsockname()[1]
        try:
            self.assertTrue(g.address_open([("127.0.0.1", port)]))
        finally:
            server.close()
        self.assertFalse(g.address_open([("127.0.0.1", port)]))

    def test_candidate_addresses_read_xtquant_configs(self):
        with tempfile.TemporaryDirectory() as home:
            base = Path(home) / ".xtquant"
            configs = {"a": json.dumps({"ip": "127.0.0.1", "port": 58611}),
                       "b": json.dumps({"ip": "0.0.0.0", "port": 58612}),
                       "c": json.dumps({"ip": "10.0.0.5", "port": 58613}),
                       "d": json.dumps({"port": 58610}),
                       "e": "not json"}
            for name, text in configs.items():
                (base / name).mkdir(parents=True)
                (base / name / "xtdata.cfg").write_text(text, encoding="utf-8")
            (base / "f").mkdir()
            (base / "f" / "running_status").write_text("", encoding="utf-8")
            self.assertEqual(g.candidate_addresses(Path(home)),
                             [("127.0.0.1", 58610), ("127.0.0.1", 58611),
                              ("127.0.0.1", 58612), ("10.0.0.5", 58613)])
        with tempfile.TemporaryDirectory() as home:
            self.assertEqual(g.candidate_addresses(Path(home)), [("127.0.0.1", 58610)])


class ServicesInstallTheGuardTests(unittest.TestCase):
    def test_every_base_recorder_installs_it_right_after_importing_xtdata(self):
        expected = ("from xtquant import xtdata\n"
                    "from modules.xtquant_guard import install as install_qmt_guard\n\n"
                    "install_qmt_guard(xtdata)")
        for name in ("qmt_cloud_bridge.py", "qmt_l2_training_recorder_v2.py", "qmt_research_recorder_v3.py"):
            self.assertIn(expected, (ROOT / "services" / name).read_text(encoding="utf-8"), name)


if __name__ == "__main__":
    unittest.main()
