"""Regression tests for clock, cache freshness and automatic-learning controls."""

import ast
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3
import tempfile
import time
from typing import Any, Dict, Optional
import unittest

from modules.market_clock import market_now, continuous_market_open, SHANGHAI
from modules.feed_health import tick_is_fresh
from modules.qmt_live import normalize_tick
from services.learning_progress import inspect_learning_data, learning_progress


ROOT = Path(__file__).resolve().parents[1]


class MonitoringIntegrityTests(unittest.TestCase):
    def test_same_instant_has_same_session_in_london_and_shanghai(self):
        for offset in (0, 1):
            london = timezone(timedelta(hours=offset))
            opened = datetime(2026, 10, 9, 9, 45, tzinfo=SHANGHAI)
            closed = datetime(2026, 10, 9, 16, 45, tzinfo=SHANGHAI)
            self.assertTrue(continuous_market_open(opened.astimezone(london)))
            self.assertFalse(continuous_market_open(closed.astimezone(london)))

    def test_lunch_weekend_and_shanghai_day_boundary(self):
        self.assertFalse(continuous_market_open(datetime(2026, 10, 9, 12, 0, tzinfo=SHANGHAI)))
        self.assertFalse(continuous_market_open(datetime(2026, 10, 10, 10, 0, tzinfo=SHANGHAI)))
        utc = datetime(2026, 10, 9, 18, 0, tzinfo=timezone.utc)
        self.assertEqual(market_now(utc).date().isoformat(), "2026-10-10")

    def test_capture_timestamp_cannot_refresh_old_exchange_tick(self):
        now = 1791500000.0
        self.assertTrue(tick_is_fresh({"time": (now-2)*1000}, now))
        self.assertFalse(tick_is_fresh({"time": (now-60)*1000, "captured_at": datetime.now().isoformat()}, now))
        self.assertFalse(tick_is_fresh({"captured_at": datetime.now().isoformat()}, now))
        self.assertFalse(tick_is_fresh({"time": (now+30)*1000}, now))

    def test_normalized_exchange_time_is_aware_shanghai_time(self):
        timestamp=datetime(2026,10,9,9,45,tzinfo=SHANGHAI).timestamp()
        row=normalize_tick("600522.SH",{"time":timestamp*1000,"lastPrice":10})
        self.assertEqual(row["captured_at"],"2026-10-09T09:45:00.000+08:00")

    def test_l1_manager_subscribes_tick_only_and_retries(self):
        class FakeQMT:
            def __init__(self): self.calls=[]; self.fail=True
            def subscribe_quote(self, symbol, **kwargs):
                self.calls.append((symbol,kwargs))
                if self.fail: raise RuntimeError("disconnected")
                return 17
            def unsubscribe_quote(self, identifier): self.calls.append(identifier)
        tree=ast.parse((ROOT/"services/qmt_l1_training_recorder_v1.py").read_text(encoding="utf-8-sig"))
        klass=next(node for node in tree.body if isinstance(node,ast.ClassDef) and node.name=="L1OnlyManager")
        qmt=FakeQMT()
        ns={"xtdata":qmt,"time":time,"Dict":Dict,"Any":Any,"_PERIODS":["l2quote","l2transaction"]}
        exec(compile(ast.Module(body=[klass],type_ignores=[]),"<manager test>","exec"),ns)
        manager=ns["L1OnlyManager"]()
        manager.switch("600522.SH")
        self.assertTrue(manager.subscription_error)
        qmt.fail=False; manager.last_attempt=0; manager.refresh()
        self.assertEqual(manager.subscription_id,17)
        self.assertTrue(all(kwargs["period"]=="tick" for _,kwargs in qmt.calls))
        self.assertFalse(any(c["available"] for c in manager.status()["capabilities"].values()))
        manager.stop(); self.assertEqual(qmt.calls[-1],17)

    def test_dataset_count_deduplicates_symbol_bucket(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); path=root/"training/2026-10-09/l2_training.sqlite3"
            path.parent.mkdir(parents=True)
            ts=datetime(2026,10,9,10,0,tzinfo=SHANGHAI).timestamp()
            with closing(sqlite3.connect(path)) as db:
                db.execute("create table training_samples_v2(symbol, sample_bucket, generated_ts, label_smoothed_mid_60, valid, labeled_at, session)")
                db.executemany("insert into training_samples_v2 values(?,?,?,?,?,?,?)",[
                    ("300308.SZ",1,ts,1,1,"done","AM"),
                    ("300308.SZ",1,ts,1,1,"done","AM"),
                    ("300308.SZ",2,ts,0,0,"done","AM"),
                    ("300308.SZ",3,ts,0,1,"done","OPEN_AUCTION"),
                ])
                db.commit()
            result=inspect_learning_data(root,"300308.SZ")
            self.assertEqual(result["pooled_samples"],1)
            self.assertEqual(result["priority_samples"],1)
            self.assertEqual(result["data_latest_date"],"2026-10-09")

    def test_old_data_is_not_new_learning(self):
        snapshot={"latest_sample_at":"2026-09-07T15:00:00+08:00","pooled_samples":1000,"dataset_fingerprint":"same","scan_errors":[]}
        report={"samples_total":1000,"generated_at":"2026-10-09T17:00:00+01:00"}
        result=learning_progress(snapshot,{},report,datetime(2026,10,10))
        self.assertEqual(result["learning_state"],"WAITING_FRESH_DATA")
        self.assertFalse(result["can_train"])

    def test_only_new_data_allows_training(self):
        snapshot={"latest_sample_at":"2026-10-09T15:00:00+08:00","pooled_samples":1200,"dataset_fingerprint":"new","scan_errors":[]}
        now=datetime(2026,10,9,16,0)
        result=learning_progress(snapshot,{"last_trained_samples":1000},now=now)
        self.assertTrue(result["can_train"])
        result=learning_progress(snapshot,{"last_trained_samples":1200,"trained_dataset_fingerprint":"new"},now=now)
        self.assertEqual(result["learning_state"],"WAITING_NEW_SAMPLES")
        snapshot["scan_errors"]=["database busy"]
        self.assertFalse(learning_progress(snapshot,{},now=now)["can_train"])


if __name__ == "__main__":
    unittest.main()
