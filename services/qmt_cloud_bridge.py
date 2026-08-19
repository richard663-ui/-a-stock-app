# -*- coding: utf-8 -*-
"""Guosheng QMT -> Supabase bridge for V18 Final.

Read-only market-data bridge. It also records the 1-minute model locally and
labels predictions after 60 seconds so displayed confidence can be audited.
"""
from __future__ import annotations

import sys
import time
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from xtquant import xtdata

from modules.cloud_bridge import CloudBridge, load_bridge_config
from modules.direction_v18 import analyze_direction_v18
from modules.prediction_journal import PredictionJournal
from modules.qmt_level2 import QMTLevel2Manager
from modules.qmt_live import normalize_tick


def _backfill(symbol: str, count: int = 300) -> List[Dict]:
    try:
        data = xtdata.get_market_data_ex(
            field_list=[], stock_list=[symbol], period="tick",
            start_time="", end_time="", count=count,
            dividend_type="none", fill_data=False,
        ) or {}
        frame = data.get(symbol)
        if not isinstance(frame, pd.DataFrame) or frame.empty:
            return []
        rows: List[Dict] = []
        seen = set()
        for idx, row in frame.tail(count).iterrows():
            item = normalize_tick(symbol, row.to_dict(), fallback_time=idx)
            key = (item.get("captured_at"), item.get("lastPrice"), item.get("volume"))
            if key in seen:
                continue
            seen.add(key)
            rows.append(item)
        rows.sort(key=lambda x: str(x.get("captured_at") or ""))
        return rows
    except Exception as exc:
        print(f"Backfill unavailable for {symbol}: {exc}")
        return []


def _append_unique(rows: deque, row: Dict) -> None:
    if not row:
        return
    if not rows:
        rows.append(row)
        return
    prev = rows[-1]
    if (row.get("captured_at"), row.get("lastPrice"), row.get("volume")) != (
        prev.get("captured_at"), prev.get("lastPrice"), prev.get("volume")
    ):
        rows.append(row)


def _price(row: Dict) -> float:
    try:
        return float(row.get("lastPrice") or 0)
    except Exception:
        return 0.0


def _feature_snapshot(direction: Dict, l2_summary: Dict) -> Dict:
    return {
        "direction_metrics": direction.get("metrics", {}),
        "direction_label": direction.get("label_60"),
        "confidence_tier": direction.get("confidence_tier"),
        "condition_agreement": direction.get("condition_agreement"),
        "l2_metrics": (l2_summary or {}).get("metrics", {}),
        "l2_direction": (l2_summary or {}).get("direction"),
        "l2_agreement": (l2_summary or {}).get("agreement"),
    }


def main() -> None:
    config = load_bridge_config()
    if not config.ok:
        raise SystemExit("Bridge configuration is incomplete. Run repair_and_start_bridge.bat once.")

    bridge = CloudBridge(config)
    l2 = QMTLevel2Manager()
    journal = PredictionJournal(PROJECT_ROOT / "runtime" / "one_minute_predictions.sqlite3")

    current_symbol = ""
    tick_sub: Optional[int] = None
    rows: deque = deque(maxlen=1200)
    last_publish = 0.0
    last_print = 0.0
    cached_validation: Dict = {}

    print("QMT cloud bridge V18 Final started. Read-only mode.")
    print(f"Bridge ID: {config.bridge_id}")

    while True:
        try:
            requested = (bridge.get_requested_symbol() or current_symbol or "000400.SZ").upper()
            if requested != current_symbol:
                if tick_sub is not None:
                    try:
                        xtdata.unsubscribe_quote(tick_sub)
                    except Exception:
                        pass
                current_symbol = requested
                rows.clear()
                print(f"Switching to {current_symbol}")

                try:
                    tick_sub = xtdata.subscribe_quote(current_symbol, period="tick", count=-1)
                except Exception as exc:
                    tick_sub = None
                    print(f"Tick subscription warning: {exc}")

                time.sleep(0.5)
                rows.extend(_backfill(current_symbol, 300))
                if rows:
                    bridge.publish_ticks(current_symbol, list(rows), status="loading")
                    print(f"Backfilled {len(rows)} ticks")

                status = l2.switch(current_symbol)
                caps = status.get("capabilities", {}) or {}
                available = [name for name, item in caps.items() if item.get("available")]
                print("Level-2 available: " + (", ".join(available) if available else "none yet"))

            tick_data = xtdata.get_full_tick([current_symbol]) or {}
            tick = tick_data.get(current_symbol) or {}
            if tick:
                _append_unique(rows, normalize_tick(current_symbol, tick))

            now = time.time()
            if now - last_publish >= 1.0:
                latest = rows[-1] if rows else {}
                current_price = _price(latest)
                l2_snapshot = l2.snapshot()
                summary = dict(l2_snapshot.get("summary", {}) or {})

                direction = analyze_direction_v18(pd.DataFrame(list(rows)), summary)
                if current_price > 0:
                    journal.mature(symbol=current_symbol, current_price=current_price, now_ts=now)
                    if direction.get("direction_60") in {"UP", "DOWN"} and direction.get("live"):
                        journal.record(
                            symbol=current_symbol,
                            price=current_price,
                            direction=direction["direction_60"],
                            agreement=int(direction.get("condition_agreement", 0) or 0),
                            high_confidence=bool(direction.get("high_confidence")),
                            true_l2=bool(direction.get("metrics", {}).get("true_l2")),
                            features=_feature_snapshot(direction, summary),
                            now_ts=now,
                            bucket_seconds=10,
                        )

                # Validation is cheap enough every five seconds and becomes part of
                # the same L2 payload read by Streamlit Cloud.
                if not cached_validation or int(now) % 5 == 0:
                    cached_validation = journal.stats(current_symbol, limit=5000)
                summary["validation"] = cached_validation
                summary["one_minute"] = {
                    "direction": direction.get("direction_60"),
                    "label": direction.get("label_60"),
                    "agreement": direction.get("condition_agreement"),
                    "high_confidence": direction.get("high_confidence"),
                }

                bridge.publish_ticks(current_symbol, list(rows), status="online")
                bridge.publish_level2(
                    current_symbol,
                    summary=summary,
                    capabilities=l2_snapshot.get("capabilities", {}),
                    recent_transactions=l2_snapshot.get("recent_transactions", []),
                    recent_orders=l2_snapshot.get("recent_orders", []),
                    quoteaux=l2_snapshot.get("quoteaux", {}),
                    orderqueue=l2_snapshot.get("orderqueue", {}),
                    status="online" if summary.get("ok") else "waiting_l2",
                )
                last_publish = now

            if now - last_print >= 5.0:
                latest = rows[-1] if rows else {}
                l2_snapshot = l2.snapshot()
                direction = analyze_direction_v18(pd.DataFrame(list(rows)), l2_snapshot.get("summary", {}))
                n = int(cached_validation.get("true_l2_high_conf_samples", 0) or 0)
                acc = cached_validation.get("true_l2_high_conf_accuracy_pct")
                acc_text = f"{float(acc):.1f}%/{n}" if acc is not None and n else "pending"
                print(
                    f"{datetime.now().strftime('%H:%M:%S')} {current_symbol} "
                    f"price={latest.get('lastPrice')} samples={len(rows)} "
                    f"1m={direction.get('label_60', 'waiting')} "
                    f"agreement={direction.get('condition_agreement', 0)}% "
                    f"validated={acc_text}"
                )
                last_print = now

        except KeyboardInterrupt:
            print("Bridge stopped")
            l2.stop()
            break
        except Exception as exc:
            print(f"bridge error: {exc}")
            try:
                if current_symbol:
                    bridge.publish_ticks(current_symbol, list(rows), status=f"error: {exc}")
            except Exception:
                pass
            time.sleep(3.0)
        time.sleep(0.25)


if __name__ == "__main__":
    main()
