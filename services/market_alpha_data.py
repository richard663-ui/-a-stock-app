"""Resumable QMT daily-cache preparation, separate from live stock subscriptions."""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import time
from datetime import timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pandas as pd
from modules.market_alpha import atomic_csv, normalize_daily, normalize_universe
from modules.market_clock import market_now


def worker(request):
    with contextlib.redirect_stdout(io.StringIO()):
        from xtquant import xtdata
        xtdata.enable_hello = False
        if request["action"] == "inventory":
            sectors = xtdata.get_sector_list()
            desired = request["preferred_sector"]
            if desired not in sectors:
                raise RuntimeError(f"Required QMT market sector unavailable: {desired}")
            symbols = normalize_universe(xtdata.get_stock_list_in_sector(desired))
            now = market_now()
            completed = now.date() if now.hour * 60 + now.minute >= 15 * 60 + 10 else now.date() - timedelta(days=1)
            dates = xtdata.get_trading_dates("SH", end_time=completed.strftime("%Y%m%d"), count=10)
            if not symbols or not dates:
                raise RuntimeError("Market universe or exchange calendar is empty")
            as_of = pd.Timestamp(max(dates), unit="ms", tz="UTC").tz_convert("Asia/Shanghai").strftime("%Y-%m-%d")
            return {"symbols": symbols, "sector": desired, "observed_at": now.isoformat(), "as_of": as_of}
        codes = request["symbols"]
        def read_cache():
            return xtdata.get_market_data_ex([], codes, period="1d", start_time=request["start_date"],
                end_time=request["as_of"].replace("-", ""), count=-1,
                dividend_type="front_ratio", fill_data=False) or {}
        frames = read_cache()
        def save_snapshot(code, data):
            path = Path(request["data_root"]) / "daily" / (code + ".csv")
            if path.exists():
                old = pd.read_csv(path, usecols=["date"])
                if not old.empty and data["date"].min() > old["date"].min():
                    raise ValueError("Truncated replacement rejected; old adjusted snapshot preserved")
            atomic_csv(data, path)
        needed = []
        for code in codes:
            try:
                cached = normalize_daily(frames.get(code), code, request["as_of"])
                if cached["date"].max() != request["as_of"]:
                    needed.append(code)
                else:
                    # Persist available data before a slow network supplement can time out.
                    save_snapshot(code, cached)
            except ValueError:
                needed.append(code)
        error = ""
        if request["download"] and needed:
            try:
                xtdata.download_history_data2(needed, "1d", start_time=request["start_date"],
                    end_time=request["as_of"].replace("-", ""), incrementally=True)
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"
            frames = read_cache()
            for _ in range(2):
                if all(frames.get(code) is not None and not frames[code].empty for code in needed):
                    break
                # The provider can signal completion before its final cache file is visible.
                time.sleep(0.4)
                frames = read_cache()
        result = []
        for code in codes:
            try:
                data = normalize_daily(frames.get(code), code, request["as_of"])
                save_snapshot(code, data)
                result.append({"stock_code": code, "status": "UPDATED", "rows": len(data),
                               "latest_date": data["date"].max(), "error": error})
            except Exception as exc:
                result.append({"stock_code": code, "status": "FAILED_CACHE_PRESERVED", "rows": 0,
                               "latest_date": "", "error": f"{error} {type(exc).__name__}: {exc}".strip()})
        return {"rows": result}


def bounded_worker(request, timeout):
    with tempfile.TemporaryDirectory(prefix="astock-alpha-") as temp:
        source, target = Path(temp) / "request.json", Path(temp) / "response.json"
        source.write_text(json.dumps(request, ensure_ascii=True), encoding="utf-8")
        result = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--worker", str(source),
            "--response", str(target)], capture_output=True, text=True, timeout=timeout,
            env={**os.environ, "PYTHONIOENCODING": "utf-8"})
        if result.returncode or not target.exists():
            raise RuntimeError((result.stderr or result.stdout or "QMT worker failed")[-500:])
        return json.loads(target.read_text(encoding="utf-8"))


def coverage(symbols, root, as_of, minimum_history, benchmark):
    records = []
    for code in symbols + [benchmark]:
        path = root / "daily" / (code + ".csv")
        row = {"stock_code": code, "is_benchmark": code == benchmark, "history_rows": 0,
               "latest_date": "", "data_ready": False, "research_history_ready": False, "issue": "missing_daily_cache"}
        if path.exists():
            try:
                data = pd.read_csv(path)
                normalized = normalize_daily(data.set_index("date"), code, as_of)
                row.update(history_rows=len(normalized), latest_date=normalized["date"].max())
                ready = row["latest_date"] == as_of
                row.update(data_ready=ready, research_history_ready=ready and len(normalized) >= minimum_history,
                           issue="" if ready else "stale_or_suspended_requires_review")
            except Exception as exc:
                row["issue"] = type(exc).__name__ + ": " + str(exc)
        records.append(row)
    return pd.DataFrame(records)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--worker", type=Path)
    parser.add_argument("--response", type=Path)
    parser.add_argument("--config", type=Path, default=ROOT / "configs" / "market_alpha.json")
    parser.add_argument("--data-root", type=Path, default=Path.home() / "AStockData" / "market_alpha")
    parser.add_argument("--download", action="store_true")
    parser.add_argument("--max-seconds", type=int, default=300)
    parser.add_argument("--provider-timeout", type=int, default=60)
    parser.add_argument("--batch-size", type=int, default=20)
    args = parser.parse_args()
    if args.worker:
        args.response.write_text(json.dumps(worker(json.loads(args.worker.read_text(encoding="utf-8")))), encoding="utf-8")
        return
    if args.batch_size < 1 or args.max_seconds < 10 or args.provider_timeout < 1:
        parser.error("Positive batch/timeout and at least 10 seconds total runtime required")
    config = json.loads(args.config.read_text(encoding="utf-8"))
    started = time.monotonic()
    inventory = bounded_worker({"action": "inventory", "preferred_sector": config["preferred_sector"]},
                               min(30, args.max_seconds))
    root = args.data_root
    symbols, as_of = inventory["symbols"], inventory["as_of"]
    universe = pd.DataFrame({"stock_code": symbols})
    universe["exchange"] = universe["stock_code"].str.split(".", regex=False).str[-1]
    universe["universe_observed_at"] = inventory["observed_at"]
    universe["sector"] = inventory["sector"]
    universe["historical_point_in_time"] = False
    atomic_csv(universe, root / "universe_latest.csv")
    current = coverage(symbols, root, as_of, config["minimum_history"], config["benchmark"])
    missing = current.loc[~current["data_ready"], "stock_code"].tolist()
    todo = ([config["benchmark"]] if config["benchmark"] in missing else []) + [
        code for code in missing if code != config["benchmark"]]
    updates, failures = [], 0
    print(f"Universe: {len(symbols)} stocks; completed market date: {as_of}; pending daily caches: {len(todo)}", flush=True)
    for start in range(0, len(todo), args.batch_size):
        remaining = args.max_seconds - (time.monotonic() - started)
        if remaining <= 1:
            break
        codes = todo[start:start + args.batch_size]
        print(f"[{start+1}-{start+len(codes)}/{len(todo)}] QMT daily {'download' if args.download else 'cache read'}", flush=True)
        request = {"action": "daily", "symbols": codes, "download": args.download,
                   "data_root": str(root), "start_date": config["start_date"], "as_of": as_of}
        try:
            response = bounded_worker(request, min(args.provider_timeout, remaining))
            rows = response["rows"]
        except (RuntimeError, subprocess.TimeoutExpired) as exc:
            saved = coverage(codes, root, as_of, config["minimum_history"], config["benchmark"])
            saved = {row["stock_code"]: row for row in saved.to_dict("records")}
            rows = [{"stock_code": code,
                     "status": "CURRENT_CACHE_PRESERVED" if saved[code]["data_ready"] else "FAILED_CACHE_PRESERVED",
                     "rows": saved[code]["history_rows"], "latest_date": saved[code]["latest_date"],
                     "error": type(exc).__name__ + ": " + str(exc)[:300]} for code in codes]
        updates.extend(rows)
        successes = sum(row["status"] in {"UPDATED", "CURRENT_CACHE_PRESERVED"} for row in rows)
        failures = failures + 1 if successes == 0 else 0
        atomic_csv(pd.DataFrame(updates), root / "data_update_report.csv")
        print(f"  successful daily snapshots: {successes}/{len(codes)}", flush=True)
        if failures >= 2:
            print("Two empty/failed batches; stopping provider work, preserving caches.", flush=True)
            break
    report = coverage(symbols, root, as_of, config["minimum_history"], config["benchmark"])
    atomic_csv(report, root / "data_coverage_report.csv")
    stock_rows = report.loc[~report["is_benchmark"]]
    fraction = float(stock_rows["data_ready"].mean())
    ready = fraction >= config["required_data_coverage"] and bool(report.loc[report["is_benchmark"], "data_ready"].all())
    summary = {"checked_at": market_now().isoformat(), "as_of": as_of, "universe_stocks": len(symbols),
               "universe_sector": inventory["sector"], "research_version": config["version"],
               "data_source": "QMT_native_1d_front_ratio",
               "daily_data_ready": int(stock_rows["data_ready"].sum()), "coverage_fraction": fraction,
               "research_ready": ready, "new_training_model": False, "research_only": True,
               "active_for_signals": False, "historical_point_in_time_universe": False,
               "issue": "" if ready else "INCOMPLETE_DAILY_DATA: no whole-market prediction or alpha claim allowed"}
    root.mkdir(parents=True, exist_ok=True)
    path = root / "data_status.json"
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    temporary.replace(path)
    print(json.dumps(summary, ensure_ascii=True), flush=True)
    if not ready:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
