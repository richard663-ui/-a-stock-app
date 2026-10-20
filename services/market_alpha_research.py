"""Run daily ranking/forward-label research only after full cache coverage passes."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pandas as pd
from modules.market_alpha import (atomic_csv, daily_features, attach_forward_labels,
                                 rank_baseline, summarize_rank_ic)
from services.market_alpha_data import coverage


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=ROOT / "configs" / "market_alpha.json")
    parser.add_argument("--data-root", type=Path, default=Path.home() / "AStockData" / "market_alpha")
    args = parser.parse_args()
    config = json.loads(args.config.read_text(encoding="utf-8"))
    root = args.data_root
    output = root / "research"
    output.mkdir(parents=True, exist_ok=True)
    try:
        status = json.loads((root / "data_status.json").read_text(encoding="utf-8"))
        universe = pd.read_csv(root / "universe_latest.csv")
        as_of = status["as_of"]
        report = coverage(universe["stock_code"].tolist(), root, as_of,
                          config["minimum_history"], config["benchmark"])
        ready = bool(report["data_ready"].all())
    except (OSError, ValueError, KeyError) as exc:
        ready = False
        status = {"issue": type(exc).__name__ + ": " + str(exc)}
    validations = [{"check": "daily_universe_coverage", "status": "PASS" if ready else "FAIL",
                    "detail": "Every current-universe stock and benchmark requires fresh daily data."},
                   {"check": "research_isolation", "status": "PASS", "detail": "No live scores or signal labels are changed."},
                   {"check": "historical_universe", "status": "WARN", "detail": "Current-universe snapshot; historical survivorship/selection bias remains."},
                   {"check": "execution_and_costs", "status": "WARN", "detail": "Next-open price proxy; fills/limits/slippage not simulated, fixed cost is an assumption."},
                   {"check": "alpha_claim", "status": "WARN", "detail": "Benchmark excess return and rank IC are not proof of risk-adjusted alpha or portfolio profit."}]
    atomic_csv(pd.DataFrame(validations), output / "validation_report.csv")
    if not ready:
        print("Full-market daily data incomplete. No new research ranking generated; live signals untouched.")
        print(json.dumps(status, ensure_ascii=True))
        raise SystemExit(2)
    benchmark = pd.read_csv(root / "daily" / (config["benchmark"] + ".csv"))
    calendar = pd.to_datetime(benchmark["date"])
    frames = []
    for index, code in enumerate(universe["stock_code"], 1):
        prices = pd.read_csv(root / "daily" / (code + ".csv"))
        features = daily_features(prices, calendar)
        labeled = attach_forward_labels(features, benchmark, config["horizons"], config["round_trip_cost_bp"])
        frames.append(labeled.tail(config["validation_tail_sessions"]))
        if index % 500 == 0:
            print(f"Features/labels: {index}/{len(universe)}", flush=True)
    scored = rank_baseline(pd.concat(frames, ignore_index=True), config)
    latest = scored.loc[scored["date"] == pd.Timestamp(as_of)].sort_values("baseline_rank_score", ascending=False)
    latest["metadata_risk_status"] = "ST/delisting/industry point-in-time metadata not yet validated; research only"
    atomic_csv(latest, output / "latest_rankings.csv")
    atomic_csv(summarize_rank_ic(scored, config["horizons"]), output / "rank_ic_by_date.csv")
    # Do not mix historical recomputed ranks into a prospective signal log.
    print(json.dumps({"research_rows": len(latest), "eligible_stocks": int(latest["research_eligible"].sum()),
                      "research_only": True, "active_for_signals": False,
                      "historical_alpha_validated": False}, ensure_ascii=True))


if __name__ == "__main__":
    main()
