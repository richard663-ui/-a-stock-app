import json
import os
import random
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from modules import mobile_model_v10 as m10  # noqa: E402

START_MS = 1792548000000  # 2026-10-21 10:00:00 Shanghai (Wednesday, AM session)


def node_binary():
    found = shutil.which("node")
    if found:
        return found
    local = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs"
    for candidate in sorted(local.glob("node-v*-win-x64/node.exe"), reverse=True):
        return str(candidate)
    return None


def make_stream(seed: int):
    rng = random.Random(seed)
    price, volume, amount, t = 10.0 + rng.random() * 40, 100000, 1e8, START_MS
    drift = rng.choice([-1, 0, 1]) * 0.0004
    ticks = []
    for i in range(260):
        t += rng.choice([3000, 3000, 3000, 6000, 1000])
        if seed % 5 == 0 and i == 130:
            t += 20000                          # exchange gap -> window reset
        if rng.random() < 0.03 and ticks:
            t = ticks[-1]["time"]               # duplicate exchange timestamp
        price = max(1.0, price * (1 + drift + rng.gauss(0, 0.0012)))
        if rng.random() < 0.15:
            drift = -drift if drift else rng.choice([-1, 1]) * 0.0004
        dv = rng.choice([0, 0, rng.randint(10, 3000)])
        volume += dv
        amount += dv * 100 * price
        tick_size = 0.01
        bid = round(price - tick_size * rng.choice([0, 1]), 2)
        ask = round(bid + tick_size * rng.choice([1, 1, 2]), 2)
        ticks.append({"time": t, "lastPrice": round(price, 2), "volume": volume, "amount": round(amount, 2),
                      "bidPrice": [bid - 0.01 * k for k in range(5)], "askPrice": [ask + 0.01 * k for k in range(5)],
                      "bidVol": [rng.randint(1, 900) for _ in range(5)], "askVol": [rng.randint(1, 900) for _ in range(5)]})
    return ticks


class MobileV10ParityTests(unittest.TestCase):
    def test_python_port_matches_typescript_model(self):
        node = node_binary()
        if not node:
            self.skipTest("node not installed")
        cases = [{"ticks": make_stream(seed), "cuts": [40, 80, 120, 160, 200, 240, 260]} for seed in range(25)]
        with tempfile.TemporaryDirectory() as tmp:
            fixture = Path(tmp) / "cases.json"
            fixture.write_text(json.dumps(cases), encoding="utf-8")
            proc = subprocess.run([node, str(ROOT / "tests" / "parity" / "mobile_v10_parity.mjs"), str(fixture)],
                                  capture_output=True, text=True, cwd=str(ROOT), timeout=120)
            self.assertEqual(proc.returncode, 0, proc.stderr[-800:])
            expected = json.loads(proc.stdout)
        compared = nonzero = 0
        for case, exp_cuts in zip(cases, expected):
            for cut, exp in zip(case["cuts"], exp_cuts):
                got = m10.stable_model(case["ticks"][:cut])
                self.assertEqual((got["score60"], got["score120"], got["ready60"], got["ready120"],
                                  got["score60_core"], got["score120_core"]),
                                 (exp["score60"], exp["score120"], exp["ready60"], exp["ready120"],
                                  exp["core60"], exp["core120"]), f"cut {cut}")
                compared += 1
                nonzero += int(exp["score60"] != 0 or exp["core60"] != 0)
        self.assertGreater(nonzero, 20, "fixtures must exercise non-trivial scores")
        self.assertEqual(compared, 25 * 7)

    def test_session_key_matches_shanghai_sessions(self):
        self.assertEqual(m10.session_key(START_MS), "2026-10-21/AM")
        self.assertIsNone(m10.session_key(START_MS + 2 * 3600 * 1000))      # 12:00 lunch
        self.assertEqual(m10.js_round(2.5), 3)
        self.assertEqual(m10.js_round(-2.5), -2)


if __name__ == "__main__":
    unittest.main()
