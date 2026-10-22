"""Offline evaluation of the phone model (mobile-v10) on recorded raw L1 ticks.

Read-only research. Uses the parity-tested port in modules/mobile_model_v10.py
and the phone ledger's settlement rule (first valid quote within 10 s after
+60 s, same session). Reports, separately:

* descriptive direction hit rate vs. the base rate of the same outcome,
* mean forward return after UP/DOWN calls vs. all evaluated moments,
* executable result: UP = buy at ask, sell at bid 60 s later; DOWN = sell at
  bid, buy back at ask (requires shares already held: T+1), both after fees,
* a non-overlapping (one call per symbol-minute) subset.

    python research/tools/evaluate_mobile_v10.py --sources raw backfill
"""
from __future__ import annotations

import argparse
import json
import math
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from modules import mobile_model_v10 as m10  # noqa: E402
from modules.costs import load_cost_model  # noqa: E402
from modules.market_clock import SHANGHAI  # noqa: E402
from modules.paths import data_root  # noqa: E402

COSTS = load_cost_model()
FEES_BP = COSTS.round_trip_fees_bp()
HISTORY_MS = 240_000


def load_ticks(db: Path) -> Dict[str, List[Dict[str, Any]]]:
    conn = sqlite3.connect(db.as_uri() + "?mode=ro", uri=True)
    out: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    try:
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='raw_ticks_v3'").fetchone():
            return out
        for row in conn.execute("SELECT upper(symbol), qmt_time, last_price, volume, amount, bid_price_json, "
                                "ask_price_json, bid_vol_json, ask_vol_json FROM raw_ticks_v3 "
                                "WHERE qmt_time IS NOT NULL ORDER BY symbol, qmt_time"):
            t = float(row[1])
            out[row[0]].append({"time": t if t > 1e12 else t * 1000, "lastPrice": row[2], "volume": row[3],
                                "amount": row[4], "bidPrice": json.loads(row[5]), "askPrice": json.loads(row[6]),
                                "bidVol": json.loads(row[7]), "askVol": json.loads(row[8])})
    finally:
        conn.close()
    return out


def evaluate_symbol(ticks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    ticks = [t for t in ticks if m10.session_key(t["time"]) and m10.valid_quote(t)]
    if len(ticks) < 50:
        return []
    times = [t["time"] for t in ticks]
    results = []
    start = int(times[0] // 10_000 * 10_000) + 10_000
    lo = 0
    for bucket in range(start, int(times[-1]), 10_000):
        while lo < len(times) and times[lo] < bucket - HISTORY_MS:
            lo += 1
        hi = lo
        while hi < len(times) and times[hi] <= bucket:
            hi += 1
        window = ticks[lo:hi]
        if len(window) < 20 or bucket - window[-1]["time"] > 12_000:
            continue
        model = m10.stable_model(window)
        if not model["ready60"]:
            continue
        entry = window[-1]
        session = m10.session_key(entry["time"])
        target = bucket + 60_000
        exit_idx = next((i for i in range(hi, len(ticks)) if times[i] >= target), None)
        if exit_idx is None or times[exit_idx] > target + 10_000 or m10.session_key(times[exit_idx]) != session:
            continue
        exit_tick = ticks[exit_idx]
        e_bid, e_ask = entry["bidPrice"][0], entry["askPrice"][0]
        x_bid, x_ask = exit_tick["bidPrice"][0], exit_tick["askPrice"][0]
        price, exit_price = float(entry["lastPrice"]), float(exit_tick["lastPrice"])
        spread_bp = (e_ask - e_bid) / ((e_ask + e_bid) / 2) * 1e4
        ret_bp = (exit_price / price - 1) * 1e4
        band = max(2.0, spread_bp)
        actual = "UP" if ret_bp > band else "DOWN" if ret_bp < -band else "FLAT"
        results.append({
            "bucket": bucket, "score": model["score60"], "direction": m10.score_view(model["score60"]),
            "actual": actual, "ret_bp": ret_bp,
            "mid_ret_bp": ((x_bid + x_ask) / (e_bid + e_ask) - 1) * 1e4,
            "up_exec_bp": (x_bid / e_ask - 1) * 1e4 - FEES_BP,
            "down_exec_bp": (e_bid / x_ask - 1) * 1e4 - FEES_BP,
            "spread_bp": spread_bp,
        })
    return results


def summarize(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    def mean(values):
        values = [v for v in values if math.isfinite(v)]
        return round(sum(values) / len(values), 2) if values else None

    out: Dict[str, Any] = {"evaluated_moments": len(rows), "mean_mid_ret_bp_all": mean([r["mid_ret_bp"] for r in rows])}
    for side in ("UP", "DOWN"):
        calls = [r for r in rows if r["direction"] == side]
        base = sum(1 for r in rows if r["actual"] == side) / len(rows) if rows else None
        hit = sum(1 for r in calls if r["actual"] == side) / len(calls) if calls else None
        key = "up_exec_bp" if side == "UP" else "down_exec_bp"
        out[side] = {
            "calls": len(calls),
            "coverage_pct": round(100 * len(calls) / len(rows), 2) if rows else None,
            "hit_rate_pct": round(100 * hit, 2) if hit is not None else None,
            "base_rate_pct": round(100 * base, 2) if base is not None else None,
            "mean_mid_ret_bp": mean([r["mid_ret_bp"] for r in calls]),
            "mean_executable_net_bp_after_fees": mean([r[key] for r in calls]),
            "share_profitable_after_costs_pct": round(100 * sum(1 for r in calls if r[key] > 0) / len(calls), 2) if calls else None,
            "by_strength": {
                band: {"calls": len(sub), "mean_mid_ret_bp": mean([r["mid_ret_bp"] for r in sub]),
                       "mean_executable_net_bp": mean([r[key] for r in sub])}
                for band, sub in (
                    ("15-39", [r for r in calls if 15 <= abs(r["score"]) < 40]),
                    ("40-69", [r for r in calls if 40 <= abs(r["score"]) < 70]),
                    ("70+", [r for r in calls if abs(r["score"]) >= 70]))
            },
        }
    out["median_spread_bp"] = sorted(r["spread_bp"] for r in rows)[len(rows) // 2] if rows else None
    out["round_trip_fees_bp"] = round(FEES_BP, 2)
    return out


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-root", type=Path, default=data_root())
    parser.add_argument("--sources", nargs="+", default=["raw"], choices=["raw", "backfill"])
    parser.add_argument("--out", type=Path, default=ROOT / "research" / "results")
    args = parser.parse_args(argv)
    folders = {"raw": args.data_root / "raw", "backfill": args.data_root / "backfill" / "qmt_history"}
    per_day: Dict[str, Any] = {}
    all_rows: List[Dict[str, Any]] = []
    for source in args.sources:
        for db in sorted(folders[source].glob("*/ticks.sqlite3")):
            day = db.parent.name
            if day in per_day:          # live archive wins; backfill only fills missing days
                continue
            rows = []
            for symbol, ticks in load_ticks(db).items():
                for r in evaluate_symbol(ticks):
                    r["symbol"], r["day"], r["source"] = symbol, day, source
                    rows.append(r)
            if len(rows) < 200:
                continue
            per_day[day] = {"source": source, **summarize(rows)}
            all_rows.extend(rows)
            print(f"{day} [{source}] moments={len(rows)} UP calls={per_day[day]['UP']['calls']} "
                  f"hit={per_day[day]['UP']['hit_rate_pct']}% (base {per_day[day]['UP']['base_rate_pct']}%) "
                  f"UP exec net={per_day[day]['UP']['mean_executable_net_bp_after_fees']}bp")
    minute_rows = {}
    for r in all_rows:
        minute_rows.setdefault((r["symbol"], r["bucket"] // 60_000), r)
    report = {
        "model": "mobile-v10-exchange-window-prospective (Python parity port)",
        "generated_at": datetime.now(SHANGHAI).isoformat(timespec="seconds"),
        "days": len(per_day), "symbols": sorted({r["symbol"] for r in all_rows}),
        "overall_overlapping_10s": summarize(all_rows),
        "overall_non_overlapping_60s": summarize(list(minute_rows.values())),
        "per_day": per_day,
        "caveats": ["Historical replay, not prospective; overlapping 10 s samples are correlated.",
                    "Executable results assume fills at the quoted best bid/ask with no queueing.",
                    "DOWN execution requires shares bought on a previous day (T+1).",
                    f"Fees from configs/costs.json ({COSTS.version}); spread paid via ask/bid prices."],
    }
    args.out.mkdir(parents=True, exist_ok=True)
    path = args.out / f"mobile_v10_offline_eval_{datetime.now(SHANGHAI):%Y%m%d}.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: report[k] for k in ("days", "overall_non_overlapping_60s")}, ensure_ascii=False, indent=1))
    print(f"Report: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
