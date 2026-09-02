# -*- coding: utf-8 -*-
"""V6 research recorder: expiry-first evaluation + staggered async scoring.

The predictive model itself is unchanged (V5B). This file only fixes recorder
integrity so 60s/120s expiry evaluation is not blocked by expensive score_rows()
work across the background basket.
"""
from __future__ import annotations

import time
from collections import deque
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime
from typing import Any, Deque, Dict, Optional, Set, Tuple

from xtquant import xtdata

import services.qmt_research_recorder_v5 as v5

base = v5.base
RECORDER_VERSION = "research-recorder-v6-expiry-first-20260902"
POLL_SECONDS = 0.20
TRACE_SECONDS = 5.0
MAX_SCORE_READY_DELAY_SECONDS = 1.5
MAX_SCORE_WORKERS = 2

base.RECORDER_VERSION = RECORDER_VERSION


def _score_job(rows, macd_context, submitted_ts: float) -> Dict[str, Any]:
    metrics = base.score_rows(rows, macd_context)
    metrics["coverage_seconds"] = base._coverage_seconds(rows)
    metrics["score_input_ts"] = submitted_ts
    metrics["execution_recorder_version"] = RECORDER_VERSION
    metrics["production_model_reference"] = base.PRODUCTION_MODEL
    return metrics


def _phase_bucket(now_ts: float, phase: float) -> int:
    return int((now_ts - phase) // TRACE_SECONDS)


def main() -> None:
    lock = base._acquire_single_instance()
    if lock is None:
        print("AStock Research Recorder already running")
        raise SystemExit(17)

    print("AStock Research Recorder V6 started")
    print(f"Recorder: {RECORDER_VERSION}")
    print(f"Research model: {base.MODEL_VERSION}")
    print("Integrity mode: expiry-first + async staggered scoring + durable cloud sync")
    print("Predictive weights/gates are unchanged from V5B.")

    store = base.DailyStore()
    cloud = base.CloudState()
    uploader = base.DurableUploader(cloud.bridge)
    executor = ThreadPoolExecutor(max_workers=MAX_SCORE_WORKERS, thread_name_prefix="research-score")

    subscriptions: Dict[str, Optional[int]] = {}
    last_sub_retry: Dict[str, float] = {}
    last_snapshot: Dict[str, str] = {}
    latest_rows: Dict[str, Dict[str, Any]] = {}
    buffers: Dict[str, Deque[Dict[str, Any]]] = {}
    pending: Dict[Tuple[str, int], Dict[str, Any]] = {}
    counts: Dict[str, int] = {}
    eval_counts = {"60": 0, "120": 0, "invalid": 0}
    fixed: Set[str] = set()

    score_jobs: Dict[str, Tuple[Future, float, str]] = {}
    last_phase_bucket: Dict[str, int] = {}
    score_slow_skips = 0
    score_errors = 0

    last_watchlist = 0.0
    last_status = 0.0
    active_session = base._session_key()
    current_day = datetime.now().strftime("%Y-%m-%d")
    last_error = ""

    now0 = time.time()
    try:
        for sample in store.load_unfinished():
            key = (sample["symbol"], sample["horizon_seconds"])
            same_session = base._session_key(datetime.fromtimestamp(sample["generated_ts"])) == base._session_key()
            if sample["expires_ts"] > now0 and same_session:
                pending[key] = sample
            else:
                store.finalize_eval(sample, None, now0, False, "recorder_restart_or_gap")
                eval_counts["invalid"] += 1
    except Exception as exc:
        print(f"[WARN] recovery: {exc}")

    try:
        while True:
            loop_start = time.time()
            now_dt = datetime.now()
            day = now_dt.strftime("%Y-%m-%d")
            session = base._session_key(now_dt)

            if day != current_day:
                current_day = day
                counts = {s: 0 for s in counts}
                eval_counts = {"60": 0, "120": 0, "invalid": 0}
                last_snapshot.clear()
                buffers.clear()
                last_phase_bucket.clear()

            if session != active_session:
                for key, sample in list(pending.items()):
                    store.finalize_eval(sample, None, loop_start, False, "session_boundary")
                    pending.pop(key, None)
                    eval_counts["invalid"] += 1
                buffers.clear()
                last_phase_bucket.clear()
                active_session = session

            if loop_start - last_watchlist >= base.WATCHLIST_RELOAD_SECONDS:
                fixed = set(base._load_watchlist())
                last_watchlist = loop_start

            symbols = set(fixed)
            mobile_symbol = cloud.mobile_symbol()
            if mobile_symbol:
                symbols.add(mobile_symbol)
            symbols = set(sorted(s for s in symbols if s)[:base.MAX_SYMBOLS])

            for symbol in sorted(symbols):
                if symbol not in subscriptions:
                    subscriptions[symbol] = base._subscribe(symbol)
                    last_sub_retry[symbol] = loop_start
                    counts.setdefault(symbol, 0)
                    buffers.setdefault(symbol, deque(maxlen=base.BUFFER_ROWS))
                    print(f"[ADD] {symbol}")
                elif subscriptions[symbol] is None and loop_start - last_sub_retry.get(symbol, 0.0) >= base.SUB_RETRY_SECONDS:
                    subscriptions[symbol] = base._subscribe(symbol)
                    last_sub_retry[symbol] = loop_start

            for symbol in list(subscriptions):
                if symbol in symbols:
                    continue
                sid = subscriptions.pop(symbol)
                last_sub_retry.pop(symbol, None)
                last_snapshot.pop(symbol, None)
                latest_rows.pop(symbol, None)
                buffers.pop(symbol, None)
                job = score_jobs.pop(symbol, None)
                if job:
                    job[0].cancel()
                if sid is not None:
                    try:
                        xtdata.unsubscribe_quote(sid)
                    except Exception:
                        pass
                for key in [k for k in pending if k[0] == symbol]:
                    sample = pending.pop(key)
                    store.finalize_eval(sample, None, loop_start, False, "symbol_removed")
                    eval_counts["invalid"] += 1
                print(f"[DROP] {symbol}")

            # 1) Fast market-data refresh. No scoring is allowed in this section.
            if symbols:
                try:
                    data = xtdata.get_full_tick(sorted(symbols)) or {}
                    for symbol in symbols:
                        tick = data.get(symbol) or {}
                        if not tick:
                            continue
                        row = base.normalize_tick(symbol, tick)
                        latest_rows[symbol] = row
                        snap = base._snapshot_hash(row)
                        if snap != last_snapshot.get(symbol):
                            last_snapshot[symbol] = snap
                            if store.write_tick(row):
                                counts[symbol] = counts.get(symbol, 0) + 1
                            if session != "CLOSED":
                                buffers.setdefault(symbol, deque(maxlen=base.BUFFER_ROWS)).append(row)
                    last_error = ""
                except Exception as exc:
                    last_error = str(exc)
                    print(f"[WARN] QMT read error: {exc}")

            now_eval = time.time()

            # 2) EXPIRY-FIRST: finalize every due sample before any heavy scoring.
            for key, sample in list(pending.items()):
                if now_eval < float(sample["expires_ts"]):
                    continue
                row = latest_rows.get(sample["symbol"]) or {}
                price = base._f(row.get("lastPrice"))
                delay = now_eval - float(sample["expires_ts"])
                valid = bool(price and price > 0 and delay <= base.MAX_SCORE_DELAY_SECONDS and session != "CLOSED")
                reason = None if valid else ("late_scoring" if delay > base.MAX_SCORE_DELAY_SECONDS else "missing_price_or_session")
                store.finalize_eval(sample, float(price) if price else None, now_eval, valid, reason)
                pending.pop(key, None)
                if valid:
                    eval_counts[str(sample["horizon_seconds"])] += 1
                else:
                    eval_counts["invalid"] += 1

            # 3) Consume finished score jobs. Creation time is when the result is
            # actually available, so forward labels remain executable/fair.
            for symbol, item in list(score_jobs.items()):
                future, submitted_ts, submitted_session = item
                if not future.done():
                    continue
                score_jobs.pop(symbol, None)
                ready_ts = time.time()
                try:
                    metrics = future.result()
                except Exception as exc:
                    score_errors += 1
                    print(f"[WARN] score worker {symbol}: {exc}")
                    continue
                compute_seconds = max(0.0, ready_ts - submitted_ts)
                metrics["score_compute_seconds"] = round(compute_seconds, 6)
                metrics["score_ready_ts"] = ready_ts
                store.write_trace(symbol, metrics, ready_ts)
                if submitted_session != session or session == "CLOSED":
                    continue
                if compute_seconds > MAX_SCORE_READY_DELAY_SECONDS:
                    score_slow_skips += 1
                    continue
                row_now = latest_rows.get(symbol) or {}
                entry_price = base._f(row_now.get("lastPrice"))
                if not entry_price or entry_price <= 0:
                    continue
                for horizon in (60, 120):
                    key = (symbol, horizon)
                    if key in pending or base._seconds_to_close(now_dt) <= horizon + 3:
                        continue
                    coverage = float(metrics.get("coverage_seconds") or 0.0)
                    buf_len = len(buffers.get(symbol, ()))
                    if horizon == 60 and (coverage < 55 or buf_len < 20):
                        continue
                    if horizon == 120 and (coverage < 125 or buf_len < 40):
                        continue
                    score = int(metrics["score60"] if horizon == 60 else metrics["score120"])
                    label = base.score_label(score)
                    generated_ts = ready_ts
                    bucket = int(generated_ts // horizon) * horizon
                    sample = {
                        "symbol": symbol,
                        "horizon_seconds": horizon,
                        "bucket_start": bucket,
                        "generated_ts": generated_ts,
                        "expires_ts": generated_ts + horizon,
                        "generated_at": datetime.fromtimestamp(generated_ts).astimezone().isoformat(),
                        "expires_at": datetime.fromtimestamp(generated_ts + horizon).astimezone().isoformat(),
                        "entry_price": float(entry_price),
                        "direction": label["direction"],
                        "score": score,
                        "tier": label["tier"],
                        "high_confidence": base.high_confidence(horizon, metrics),
                        "macd_bias": metrics.get("macd_bias"),
                        "features": dict(metrics),
                    }
                    if store.create_eval(sample):
                        pending[key] = sample

            # 4) Stagger score submission across the 5-second cycle instead of
            # scoring all eight symbols in the same burst.
            ordered = sorted(symbols)
            n_symbols = max(1, len(ordered))
            for idx, symbol in enumerate(ordered):
                if symbol in score_jobs:
                    continue
                buf = buffers.setdefault(symbol, deque(maxlen=base.BUFFER_ROWS))
                if len(buf) < 20 or session == "CLOSED":
                    continue
                phase = TRACE_SECONDS * idx / n_symbols
                p_bucket = _phase_bucket(loop_start, phase)
                if p_bucket == last_phase_bucket.get(symbol):
                    continue
                rows = list(buf)
                macd_context = cloud.macd_bias(symbol)
                submitted_ts = time.time()
                score_jobs[symbol] = (
                    executor.submit(_score_job, rows, macd_context, submitted_ts),
                    submitted_ts,
                    session,
                )
                last_phase_bucket[symbol] = p_bucket

            if loop_start - last_status >= base.STATUS_SECONDS:
                try:
                    store.heartbeat({
                        "symbols": sorted(symbols),
                        "counts": counts,
                        "eval_counts": eval_counts,
                        "mobile_symbol": mobile_symbol,
                        "pending_eval": len(pending),
                        "score_jobs_inflight": len(score_jobs),
                        "score_slow_skips": score_slow_skips,
                        "score_errors": score_errors,
                        "session": session,
                        "last_error": last_error,
                        "recorder_version": RECORDER_VERSION,
                    })
                    base._write_status(symbols, counts, eval_counts, pending, last_error)
                except Exception as exc:
                    print(f"[WARN] status write error: {exc}")
                print(
                    f"{datetime.now().strftime('%H:%M:%S')} symbols={len(symbols)} "
                    f"rows={sum(counts.values())} eval60={eval_counts['60']} "
                    f"eval120={eval_counts['120']} invalid={eval_counts['invalid']} "
                    f"jobs={len(score_jobs)} slow_skip={score_slow_skips}"
                )
                last_status = loop_start

            elapsed = time.time() - loop_start
            time.sleep(max(0.02, POLL_SECONDS - elapsed))

    except KeyboardInterrupt:
        print("Research recorder V6 stopped")
    finally:
        try:
            uploader.stop()
        except Exception:
            pass
        try:
            cloud.stop()
        except Exception:
            pass
        executor.shutdown(wait=False, cancel_futures=True)
        for sid in subscriptions.values():
            if sid is not None:
                try:
                    xtdata.unsubscribe_quote(sid)
                except Exception:
                    pass
        if store.conn is not None:
            try:
                store.conn.commit()
                store.conn.close()
            except Exception:
                pass


if __name__ == "__main__":
    main()
