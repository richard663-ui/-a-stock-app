# -*- coding: utf-8 -*-
"""Guosheng QMT -> Supabase bridge for V18 Final.

Read-only market-data bridge. Cloud I/O is isolated from QMT sampling.
2026-08-27 reliability patch:
- never publish an empty/stale QMT feed as "online";
- retry QMT tick subscription automatically after service disconnects;
- preserve five-level order-book-only changes in the rolling tick buffer;
- recover automatically after QMT/xtquant comes back without a Windows reboot.
"""
from __future__ import annotations

import queue
import sys
import threading
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
from modules.runtime_guard import acquire_bridge_lock

CLOUD_TICK_LIMIT = 600
WATCH_POLL_SECONDS = 0.8
LIVE_BUILD_SECONDS = 1.0
L2_BUILD_SECONDS = 2.0
QMT_RETRY_SECONDS = 3.0
QMT_HEALTH_GRACE_SECONDS = 5.0
QMT_SELF_HEAL_MARKER = "QMT self-heal: ON"


def _tuple5(v) -> tuple:
    if not isinstance(v, (list, tuple)):
        return ()
    out = []
    for x in list(v)[:5]:
        try:
            out.append(float(x))
        except Exception:
            out.append(None)
    return tuple(out)


def _row_signature(row: Dict) -> tuple:
    return (
        row.get("captured_at"),
        row.get("lastPrice"),
        row.get("volume"),
        row.get("amount"),
        _tuple5(row.get("bidPrice")),
        _tuple5(row.get("bidVol")),
        _tuple5(row.get("askPrice")),
        _tuple5(row.get("askVol")),
    )


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
            key = _row_signature(item)
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
    if not rows or _row_signature(row) != _row_signature(rows[-1]):
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


def _offer_latest(q: queue.Queue, payload: Dict) -> None:
    try:
        q.put_nowait(payload)
        return
    except queue.Full:
        pass
    try:
        q.get_nowait()
    except queue.Empty:
        pass
    try:
        q.put_nowait(payload)
    except queue.Full:
        pass


def _watch_worker(config, state: Dict[str, str], stop: threading.Event) -> None:
    bridge = CloudBridge(config, timeout=6.0)
    last_error = ""
    while not stop.is_set():
        try:
            symbol = (bridge.get_requested_symbol() or "").upper()
            if symbol:
                state["symbol"] = symbol
            last_error = ""
        except Exception as exc:
            msg = str(exc)
            if msg != last_error:
                print(f"watch request warning: {msg}")
                last_error = msg
        stop.wait(WATCH_POLL_SECONDS)


def _tick_upload_worker(config, q: queue.Queue, stop: threading.Event) -> None:
    bridge = CloudBridge(config, timeout=6.0)
    while not stop.is_set():
        try:
            item = q.get(timeout=0.5)
        except queue.Empty:
            continue
        if item is None:
            break
        try:
            bridge.publish_ticks(item["symbol"], item["ticks"], status=item.get("status", "waiting_qmt"))
        except Exception as exc:
            print(f"tick upload warning: {exc}")


def _l2_upload_worker(config, q: queue.Queue, stop: threading.Event) -> None:
    bridge = CloudBridge(config, timeout=6.0)
    while not stop.is_set():
        try:
            item = q.get(timeout=0.5)
        except queue.Empty:
            continue
        if item is None:
            break
        try:
            bridge.publish_level2(
                item["symbol"],
                summary=item.get("summary", {}),
                capabilities=item.get("capabilities", {}),
                recent_transactions=item.get("recent_transactions", []),
                recent_orders=item.get("recent_orders", []),
                quoteaux=item.get("quoteaux", {}),
                orderqueue=item.get("orderqueue", {}),
                status=item.get("status", "waiting_l2"),
            )
        except Exception as exc:
            print(f"l2 upload warning: {exc}")


def _subscribe_tick(symbol: str, old_sub: Optional[int] = None) -> Optional[int]:
    if old_sub is not None:
        try:
            xtdata.unsubscribe_quote(old_sub)
        except Exception:
            pass
    try:
        sub = xtdata.subscribe_quote(symbol, period="tick", count=-1)
        if sub is None:
            raise RuntimeError("subscribe_quote returned None")
        try:
            if int(sub) < 0:
                raise RuntimeError(f"subscribe_quote returned {sub}")
        except (TypeError, ValueError):
            pass
        print(f"Tick subscription active for {symbol}: {sub}")
        return sub
    except Exception as exc:
        print(f"Tick subscription warning for {symbol}: {exc}")
        return None


def main() -> None:
    process_lock = acquire_bridge_lock()
    if process_lock is None:
        print("Another AStockQMT bridge is already running. Exiting this duplicate process.")
        return

    config = load_bridge_config()
    if not config.ok:
        raise SystemExit("Bridge configuration is incomplete. Run repair_and_start_bridge.bat once.")

    l2 = QMTLevel2Manager()
    journal = PredictionJournal(PROJECT_ROOT / "runtime" / "one_minute_predictions.sqlite3")

    stop = threading.Event()
    requested_state: Dict[str, str] = {"symbol": ""}
    tick_upload_q: queue.Queue = queue.Queue(maxsize=1)
    l2_upload_q: queue.Queue = queue.Queue(maxsize=1)
    workers = [
        threading.Thread(target=_watch_worker, args=(config, requested_state, stop), daemon=True, name="AStockWatch"),
        threading.Thread(target=_tick_upload_worker, args=(config, tick_upload_q, stop), daemon=True, name="AStockTickUpload"),
        threading.Thread(target=_l2_upload_worker, args=(config, l2_upload_q, stop), daemon=True, name="AStockL2Upload"),
    ]
    for worker in workers:
        worker.start()

    current_symbol = ""
    tick_sub: Optional[int] = None
    rows: deque = deque(maxlen=1200)
    last_build = 0.0
    last_l2_build = 0.0
    last_print = 0.0
    last_sub_attempt = 0.0
    last_qmt_success = 0.0
    last_qmt_error = ""
    cached_validation: Dict = {}

    print("QMT cloud bridge V18 Final started. Read-only mode.")
    print(f"Bridge ID: {config.bridge_id}")
    print("Cloud I/O isolation: ON")
    print(QMT_SELF_HEAL_MARKER)

    try:
        while True:
            requested = (requested_state.get("symbol") or current_symbol or "000400.SZ").upper()
            now = time.time()

            if requested != current_symbol:
                current_symbol = requested
                rows.clear()
                cached_validation = {}
                last_qmt_success = 0.0
                last_qmt_error = ""
                print(f"Switching to {current_symbol}")

                tick_sub = _subscribe_tick(current_symbol, tick_sub)
                last_sub_attempt = now
                time.sleep(0.35)
                backfilled = _backfill(current_symbol, 300)
                if backfilled:
                    rows.extend(backfilled)
                    last_qmt_success = time.time()
                    _offer_latest(tick_upload_q, {
                        "symbol": current_symbol,
                        "ticks": list(rows)[-CLOUD_TICK_LIMIT:],
                        "status": "loading",
                    })
                    print(f"Backfilled {len(rows)} ticks")

                status = l2.switch(current_symbol)
                caps = status.get("capabilities", {}) or {}
                available = [name for name, item in caps.items() if item.get("available")]
                print("Level-2 available: " + (", ".join(available) if available else "none yet"))

            qmt_poll_ok = False
            try:
                tick_data = xtdata.get_full_tick([current_symbol]) or {}
                tick = tick_data.get(current_symbol) or {}
                if tick:
                    qmt_poll_ok = True
                    last_qmt_success = now
                    last_qmt_error = ""
                    _append_unique(rows, normalize_tick(current_symbol, tick))
                else:
                    last_qmt_error = "empty get_full_tick"
            except Exception as exc:
                msg = str(exc)
                if msg != last_qmt_error:
                    print(f"QMT tick warning: {msg}")
                last_qmt_error = msg

            if not qmt_poll_ok and now - last_sub_attempt >= QMT_RETRY_SECONDS:
                tick_sub = _subscribe_tick(current_symbol, tick_sub)
                last_sub_attempt = now
                recovered = _backfill(current_symbol, 300)
                if recovered:
                    for item in recovered:
                        _append_unique(rows, item)
                    last_qmt_success = time.time()
                    print(f"QMT recovery backfill: {len(rows)} buffered ticks")

            now = time.time()
            qmt_healthy = bool(last_qmt_success and now - last_qmt_success <= QMT_HEALTH_GRACE_SECONDS)
            feed_status = "online" if qmt_healthy and rows else ("qmt_disconnected" if rows else "waiting_qmt")

            if now - last_build >= LIVE_BUILD_SECONDS:
                latest = rows[-1] if rows else {}
                current_price = _price(latest)
                l2_snapshot = l2.snapshot()
                summary = dict(l2_snapshot.get("summary", {}) or {})
                direction = analyze_direction_v18(pd.DataFrame(list(rows)), summary)

                if current_price > 0 and qmt_healthy:
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
                            bucket_seconds=60,
                        )

                if not cached_validation or int(now) % 5 == 0:
                    cached_validation = journal.stats(current_symbol, limit=5000)
                summary["validation"] = cached_validation
                summary["qmt_feed_status"] = feed_status
                summary["qmt_feed_healthy"] = qmt_healthy
                summary["buffered_ticks"] = len(rows)
                summary["one_minute"] = {
                    "direction": direction.get("direction_60") if qmt_healthy else "WATCH",
                    "label": direction.get("label_60") if qmt_healthy else "等待QMT行情",
                    "agreement": direction.get("condition_agreement") if qmt_healthy else 0,
                    "high_confidence": bool(direction.get("high_confidence")) if qmt_healthy else False,
                }
                summary["two_minute"] = {
                    "direction": direction.get("direction_120") if qmt_healthy else "WATCH",
                    "label": direction.get("label_120") if qmt_healthy else "等待QMT行情",
                    "agreement": direction.get("condition_agreement") if qmt_healthy else 0,
                    "high_confidence": False,
                }

                # Do not refresh qmt_live_cache while QMT is unhealthy. Let the
                # existing cloud row become stale so mobile cannot mistake a
                # heartbeat for fresh market data.
                if qmt_healthy:
                    _offer_latest(tick_upload_q, {
                        "symbol": current_symbol,
                        "ticks": list(rows)[-CLOUD_TICK_LIMIT:],
                        "status": "online",
                    })

                if now - last_l2_build >= L2_BUILD_SECONDS:
                    _offer_latest(l2_upload_q, {
                        "symbol": current_symbol,
                        "summary": summary,
                        "capabilities": l2_snapshot.get("capabilities", {}),
                        "recent_transactions": l2_snapshot.get("recent_transactions", []),
                        "recent_orders": l2_snapshot.get("recent_orders", []),
                        "quoteaux": l2_snapshot.get("quoteaux", {}),
                        "orderqueue": l2_snapshot.get("orderqueue", {}),
                        "status": "online" if summary.get("ok") and qmt_healthy else (
                            "waiting_l2" if qmt_healthy else "waiting_qmt"
                        ),
                    })
                    last_l2_build = now
                last_build = now

            if now - last_print >= 5.0:
                latest = rows[-1] if rows else {}
                l2_snapshot = l2.snapshot()
                direction = analyze_direction_v18(pd.DataFrame(list(rows)), l2_snapshot.get("summary", {}))
                n = int(cached_validation.get("true_l2_high_conf_samples", 0) or 0)
                acc = cached_validation.get("true_l2_high_conf_accuracy_pct")
                acc_text = f"{float(acc):.1f}%/{n}" if acc is not None and n else "pending"
                print(
                    f"{datetime.now().strftime('%H:%M:%S')} {current_symbol} "
                    f"feed={feed_status} price={latest.get('lastPrice')} samples={len(rows)} "
                    f"1m={direction.get('label_60', 'waiting') if qmt_healthy else '等待QMT行情'} "
                    f"agreement={direction.get('condition_agreement', 0) if qmt_healthy else 0}% "
                    f"validated={acc_text}"
                )
                last_print = now

            time.sleep(0.25)

    except KeyboardInterrupt:
        print("Bridge stopped")
    finally:
        stop.set()
        l2.stop()
        for q in (tick_upload_q, l2_upload_q):
            try:
                q.put_nowait(None)
            except queue.Full:
                pass


if __name__ == "__main__":
    main()
