import json
import sqlite3
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from modules.market_clock import SHANGHAI  # noqa: E402
from modules.trading_calendar import TradingCalendar, phase_bounds, phase_of, _normalize_days  # noqa: E402
from services import collection_report as cr  # noqa: E402
from services import collection_monitor as cm  # noqa: E402

DAY = date(2026, 10, 21)
CFG = json.loads((ROOT / "configs" / "collection.json").read_text(encoding="utf-8"))


def make_training_db(root: Path, day: date, rows):
    folder = root / "training" / day.isoformat()
    folder.mkdir(parents=True)
    conn = sqlite3.connect(folder / "l2_training.sqlite3")
    conn.execute("""CREATE TABLE training_samples_v2 (id INTEGER PRIMARY KEY, symbol TEXT, sample_bucket INTEGER,
        generated_ts REAL, valid INTEGER, labeled_at TEXT, mid_price REAL, features_json TEXT, recorder_version TEXT)""")
    conn.executemany("INSERT INTO training_samples_v2 (symbol,sample_bucket,generated_ts,valid,labeled_at,mid_price,"
                     "features_json,recorder_version) VALUES (?,?,?,?,?,?,?,?)", rows)
    conn.commit()
    conn.close()


def make_raw_db(root: Path, day: date, rows):
    folder = root / "raw" / day.isoformat()
    folder.mkdir(parents=True)
    conn = sqlite3.connect(folder / "ticks.sqlite3")
    conn.execute("CREATE TABLE raw_ticks_v3 (id INTEGER PRIMARY KEY, symbol TEXT, qmt_time INTEGER, recorded_at TEXT)")
    conn.executemany("INSERT INTO raw_ticks_v3 (symbol, qmt_time, recorded_at) VALUES (?,?,?)", rows)
    conn.commit()
    conn.close()


def training_rows(symbol, start, end, step=5, price=10.0, moving=True):
    out, t, i = [], start, 0
    while t < end:
        mid = price + (0.01 * (i % 7) if moving else 0.0)
        volume = 1000 + (i if moving else 0)
        out.append((symbol, int(t // 5) * 5, t, 1, "x", mid, json.dumps({"volume": volume}), "v-test"))
        t += step
        i += 1
    return out


class CalendarTests(unittest.TestCase):
    def test_phase_boundaries(self):
        at = lambda h, m: datetime(2026, 10, 21, h, m, tzinfo=SHANGHAI)
        self.assertEqual(phase_of(at(9, 15)), "OPEN_AUCTION")
        self.assertIsNone(phase_of(at(9, 27)))
        self.assertEqual(phase_of(at(9, 30)), "OPEN_CORE")
        self.assertEqual(phase_of(at(11, 29)), "AM_LATE")
        self.assertIsNone(phase_of(at(12, 0)))
        self.assertEqual(phase_of(at(14, 59)), "PM")
        self.assertIsNone(phase_of(at(15, 0)))

    def test_unknown_days_fall_back_to_labelled_weekday_rule(self):
        cal = TradingCalendar(days=["2026-10-21"], first="2026-10-01", last="2026-10-22", source="qmt")
        self.assertEqual(cal.resolve(date(2026, 10, 21)), (True, "qmt"))
        self.assertEqual(cal.resolve(date(2026, 10, 20)), (False, "qmt"))   # known non-trading
        self.assertEqual(cal.resolve(date(2026, 10, 23)), (True, "weekday_fallback_unverified"))
        self.assertEqual(cal.resolve(date(2026, 10, 24))[0], False)

    def test_normalize_calendar_formats(self):
        ms = int(datetime(2026, 10, 21, tzinfo=SHANGHAI).timestamp() * 1000)
        self.assertEqual(_normalize_days(["20261020", str(ms), "2026-10-22"]),
                         ["2026-10-20", "2026-10-21", "2026-10-22"])


class ReportTests(unittest.TestCase):
    def test_coverage_gaps_duplicates_and_stale_runs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            lo, hi = (x.timestamp() for x in phase_bounds(DAY, "OPEN_CORE"))
            rows = training_rows("600522.SH", lo, hi)
            # 000400.SZ: first 30 minutes only, then a flat (stale-looking) 2 minutes
            rows += training_rows("000400.SZ", lo, lo + 1800)
            rows += training_rows("000400.SZ", lo + 1800, lo + 1920, moving=False)
            rows.append(rows[0][:7] + ("v-other",))   # duplicate bucket from a second recorder version
            make_training_db(root, DAY, rows)
            make_raw_db(root, DAY, [("600522.SH", int((lo + 30) * 1000), "")])
            cal = TradingCalendar(days=[DAY.isoformat()], first="2026-10-01", last="2026-10-31", source="qmt")
            report = cr.build_report(root, DAY, DAY, cal, ["600522.SH", "000400.SZ"], CFG, include_power=False)
            day = report["days"][0]
            core = {r["symbol"]: r for r in day["rows"] if r["phase"] == "OPEN_CORE"}
            self.assertEqual(core["600522.SH"]["coverage_pct"], 100.0)
            self.assertEqual(core["600522.SH"]["duplicate_bucket_rows"], 1)
            self.assertAlmostEqual(core["000400.SZ"]["coverage_pct"], 53.33, places=1)
            self.assertGreaterEqual(core["000400.SZ"]["suspected_stale_runs"], 1)
            self.assertEqual(day["phases"]["AM_LATE"]["status"], "MISSING")
            self.assertTrue(any(g["phase"] == "AM_LATE" and g["seconds"] >= 3599 for g in day["gaps"]))
            paths = cr.write_outputs(root / "out", report, "t")
            self.assertTrue(paths["markdown"].read_text(encoding="utf-8").startswith("# Collection coverage"))

    def test_missing_database_is_reported_not_filled(self):
        with tempfile.TemporaryDirectory() as tmp:
            cal = TradingCalendar(days=[DAY.isoformat()], first="2026-10-01", last="2026-10-31", source="qmt")
            day = cr.build_report(Path(tmp), DAY, DAY, cal, ["600522.SH"], CFG, include_power=False)["days"][0]
            self.assertFalse(day["has_training_db"])
            self.assertEqual(day["status"], "MISSING")
            self.assertEqual(day["overall_coverage_pct"], 0.0)

    def test_gap_attribution_ignores_screen_off_idle_standby(self):
        t0 = datetime(2026, 10, 21, 1, 0, tzinfo=timezone.utc)
        idle = {"entered": t0, "exited": t0 + timedelta(hours=8), "reason": "Idle Timeout",
                "user_initiated": False, "seconds": 28800, "active_seconds": 28700, "drips_seconds": 0}
        button = {"entered": t0, "exited": t0 + timedelta(hours=8), "reason": "Power Button",
                  "user_initiated": True, "seconds": 28800}
        died = dict(idle, contains_restart=True, reason="code 55 then power loss/restart")
        gap = (t0 + timedelta(hours=1)).timestamp(), (t0 + timedelta(hours=2)).timestamp()
        self.assertEqual(cr.attribute_gap(*gap, [idle])["cause"], "pc_awake_but_not_recording_or_no_power_record")
        self.assertEqual(cr.attribute_gap(*gap, [button])["standby_reason"], "Power Button")
        self.assertEqual(cr.attribute_gap(*gap, [died])["cause"], "windows_standby")


def context(**kw):
    now_dt = kw.pop("now_dt", datetime(2026, 10, 21, 10, 0, tzinfo=SHANGHAI))
    base = dict(now=now_dt.timestamp(), now_dt=now_dt, trading_day=True, calendar_basis="qmt",
                phase=phase_of(now_dt), basket=["A.SH", "B.SZ"], training={}, raw={},
                status={"updated_at": now_dt.isoformat()},
                processes={"available": True, "workers": {"bridge": 1, "training_recorder": 1,
                           "research_recorder": 1, "autotrain": 1, "v6_shadow": 1}, "miniqmt": True},
                registry=json.loads((ROOT / "deploy" / "services.json").read_text(encoding="utf-8")),
                integrity={"ok": True}, ac=True, power_risks=[], free_gb=100.0, cfg=CFG)
    base.update(kw)
    return cm.Context(**base)


class MonitorTests(unittest.TestCase):
    def test_all_symbols_stale_is_flagged_and_fresh_data_is_not(self):
        keys = {f.key for f in cm.evaluate(context())}
        self.assertIn("INGEST_ALL", keys)
        ctx = context()
        fresh = {s: (ctx.now - 3, 50) for s in ctx.basket}
        ok = cm.evaluate(context(training=fresh, raw={s: ctx.now - 2 for s in ctx.basket}))
        self.assertEqual([f.key for f in ok], [])

    def test_no_ingestion_alerts_during_warmup_lunch_or_holiday(self):
        warm = datetime(2026, 10, 21, 9, 30, 30, tzinfo=SHANGHAI)
        self.assertNotIn("INGEST_ALL", {f.key for f in cm.evaluate(context(now_dt=warm))})
        lunch = datetime(2026, 10, 21, 12, 0, tzinfo=SHANGHAI)
        self.assertEqual(cm.evaluate(context(now_dt=lunch)), [])
        self.assertEqual(cm.evaluate(context(trading_day=False)), [])

    def test_missing_collector_in_session_is_critical(self):
        procs = {"available": True, "workers": {"bridge": 1, "training_recorder": 0}, "miniqmt": False}
        found = {f.key: f.severity for f in cm.evaluate(context(processes=procs, training={}, raw={}))}
        self.assertEqual(found["PROCESS_COLLECTOR_training_recorder"], "CRITICAL")
        self.assertEqual(found["MINIQMT"], "CRITICAL")

    def test_integrity_failure_and_battery(self):
        found = {f.key: f for f in cm.evaluate(context(integrity={"ok": False, "problems": ["1 modified"]}, ac=False))}
        self.assertTrue(found["INTEGRITY"].immediate)
        self.assertEqual(found["AC_POWER"].severity, "WARN")

    def test_alert_state_debounce_escalation_repeat_and_recovery(self):
        state = cm.AlertState(warn_after_checks=2, critical_after_seconds=180, repeat_seconds=1800)
        f = cm.Finding("INGEST_ALL", "WARN", "t", "m")
        self.assertEqual(state.update([f], 0), [])                     # debounce
        self.assertEqual([a.severity for a in state.update([f], 15)], ["WARN"])
        self.assertEqual(state.update([f], 30), [])                    # no repeat yet
        self.assertEqual([a.severity for a in state.update([f], 200)], ["CRITICAL"])
        recovered = state.update([], 215)
        self.assertTrue(recovered[0].resolved)

    def test_one_shot_events_are_sent_once_and_never_recover(self):
        state = cm.AlertState()
        incident = cm.Finding("SUSPENDED_1", "CRITICAL", "t", "m", immediate=True)
        self.assertEqual(len(state.update([incident], 0)), 1)
        self.assertEqual(state.update([incident], 15), [])
        self.assertEqual(state.update([], 30), [])

    def test_lost_session_seconds(self):
        start = datetime(2026, 10, 22, 3, 34, tzinfo=SHANGHAI).timestamp()
        end = datetime(2026, 10, 22, 21, 41, tzinfo=SHANGHAI).timestamp()
        lost = cm.lost_session_seconds(start, end)
        self.assertEqual(lost["2026-10-22 OPEN_CORE"], 3600.0)
        self.assertEqual(lost["2026-10-22 PM"], 7200.0)
        self.assertEqual(sum(lost.values()), 600 + 3600 + 3600 + 7200)


class BasketTests(unittest.TestCase):
    def test_collection_basket_matches_training_recorder(self):
        import ast
        tree = ast.parse((ROOT / "services" / "qmt_l2_training_recorder_v3.py").read_text(encoding="utf-8"))
        basket = next(ast.literal_eval(n.value) for n in ast.walk(tree)
                      if isinstance(n, ast.Assign) and getattr(n.targets[0], "id", "") == "BACKGROUND_TRAINING_SYMBOLS")
        self.assertEqual(list(basket), CFG["basket"])


if __name__ == "__main__":
    unittest.main()
