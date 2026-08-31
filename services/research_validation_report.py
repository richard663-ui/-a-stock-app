# -*- coding: utf-8 -*-
"""Read-only 60s/120s forward-validation report for the QMT research recorder.

This does not change prediction weights or the live/mobile model. It reads the
durable local SQLite evaluation samples produced by qmt_research_recorder_v3+
and reports whether directional accuracy/edge persists over time.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

DEFAULT_DATA_ROOT = Path.home() / "AStockData"
ROLLING_WINDOWS = (50, 100, 250, 500)


def _f(value: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        x = float(value)
        return x if x == x else default
    except Exception:
        return default


def _i(value: Any, default: Optional[int] = None) -> Optional[int]:
    try:
        return int(value)
    except Exception:
        return default


def _pct(value: Optional[float], digits: int = 1) -> str:
    return "--" if value is None else f"{value:.{digits}f}%"


def _bp(value_pct: Optional[float]) -> str:
    return "--" if value_pct is None else f"{value_pct * 100.0:+.2f} bp"


def _mean(values: Iterable[Optional[float]]) -> Optional[float]:
    xs = [float(x) for x in values if x is not None]
    return sum(xs) / len(xs) if xs else None


def _generated_dt(row: Dict[str, Any]) -> Optional[datetime]:
    ts = _f(row.get("generated_ts"))
    if ts:
        try:
            return datetime.fromtimestamp(ts).astimezone()
        except Exception:
            pass
    text = str(row.get("generated_at") or "")
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone()
    except Exception:
        return None


def _time_bucket(row: Dict[str, Any]) -> str:
    dt = _generated_dt(row)
    if dt is None:
        return "UNKNOWN"
    m = dt.hour * 60 + dt.minute
    if 570 <= m < 600:
        return "09:30-10:00"
    if 600 <= m < 660:
        return "10:00-11:00"
    if 660 <= m < 690:
        return "11:00-11:30"
    if 780 <= m < 840:
        return "13:00-14:00"
    if 840 <= m < 870:
        return "14:00-14:30"
    if 870 <= m < 900:
        return "14:30-15:00"
    return "OTHER"


def _confidence(row: Dict[str, Any], horizon: int) -> Optional[float]:
    features = row.get("features") or {}
    c = _f(features.get(f"confidence_{horizon}"))
    if c is not None:
        return c
    comp = features.get(f"confidence_components_{horizon}") or {}
    return _f(comp.get("confidence_score"))


def _confidence_bucket(value: Optional[float]) -> str:
    if value is None:
        return "NO_CONFIDENCE"
    if value < 50:
        return "<50"
    if value < 55:
        return "50-55"
    if value < 60:
        return "55-60"
    if value < 65:
        return "60-65"
    if value < 70:
        return "65-70"
    if value < 75:
        return "70-75"
    return "75+"


def _edge_pct(row: Dict[str, Any]) -> Optional[float]:
    ret = _f(row.get("return_pct"))
    if ret is None:
        return None
    direction = str(row.get("direction") or "").upper()
    if direction == "UP":
        return ret
    if direction == "DOWN":
        return -ret
    return None


def _load_rows(data_root: Path, days: int) -> List[Dict[str, Any]]:
    raw_root = data_root / "raw"
    if not raw_root.exists():
        return []
    dbs = sorted(raw_root.glob("*/ticks.sqlite3"), reverse=True)
    if days > 0:
        dbs = dbs[:days]
    rows: List[Dict[str, Any]] = []
    cols = (
        "symbol,horizon_seconds,bucket_start,generated_ts,generated_at,expires_at,"
        "entry_price,exit_price,return_pct,direction,score,score_abs,tier,high_confidence,"
        "flat,correct,valid,invalid_reason,score_delay_seconds,actual_horizon_seconds,"
        "features_json,model_version,scored_at"
    )
    for path in reversed(dbs):
        conn = sqlite3.connect(path, timeout=5.0)
        try:
            exists = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='forward_eval_v3'"
            ).fetchone()
            if not exists:
                continue
            names = [r[1] for r in conn.execute("PRAGMA table_info(forward_eval_v3)").fetchall()]
            required = {
                "symbol", "horizon_seconds", "generated_ts", "generated_at", "return_pct",
                "direction", "score", "flat", "correct", "valid", "features_json",
                "model_version", "scored_at",
            }
            if not required.issubset(names):
                continue
            cur = conn.execute(
                f"SELECT {cols} FROM forward_eval_v3 WHERE scored_at IS NOT NULL ORDER BY generated_ts"
            )
            for r in cur.fetchall():
                features: Dict[str, Any] = {}
                try:
                    features = json.loads(r[20] or "{}")
                except Exception:
                    pass
                rows.append({
                    "symbol": r[0],
                    "horizon_seconds": _i(r[1], 0),
                    "bucket_start": _i(r[2], 0),
                    "generated_ts": _f(r[3]),
                    "generated_at": r[4],
                    "expires_at": r[5],
                    "entry_price": _f(r[6]),
                    "exit_price": _f(r[7]),
                    "return_pct": _f(r[8]),
                    "direction": str(r[9] or "").upper(),
                    "score": _i(r[10], 0),
                    "score_abs": _i(r[11], 0),
                    "tier": r[12],
                    "high_confidence": bool(r[13]),
                    "flat": None if r[14] is None else bool(r[14]),
                    "correct": None if r[15] is None else bool(r[15]),
                    "valid": bool(r[16]),
                    "invalid_reason": r[17],
                    "score_delay_seconds": _f(r[18]),
                    "actual_horizon_seconds": _f(r[19]),
                    "features": features,
                    "model_version": str(r[21] or ""),
                    "scored_at": r[22],
                    "source_db": str(path),
                })
        finally:
            conn.close()
    rows.sort(key=lambda x: (_f(x.get("generated_ts"), 0.0) or 0.0, x.get("symbol") or ""))
    return rows


def _choose_model(rows: Sequence[Dict[str, Any]], horizon: int, requested: str) -> str:
    if requested:
        return requested
    candidates = [
        r for r in rows
        if r.get("horizon_seconds") == horizon and r.get("valid") and r.get("model_version")
    ]
    if not candidates:
        return ""
    latest = max(candidates, key=lambda r: _f(r.get("generated_ts"), 0.0) or 0.0)
    return str(latest.get("model_version") or "")


def _stats(rows: Sequence[Dict[str, Any]], horizon: int) -> Dict[str, Any]:
    valid = [r for r in rows if r.get("valid")]
    directional = [r for r in valid if r.get("direction") in {"UP", "DOWN"}]
    moving = [
        r for r in directional
        if r.get("flat") is False and r.get("correct") is not None
    ]
    correct = sum(1 for r in moving if r.get("correct") is True)
    incorrect = sum(1 for r in moving if r.get("correct") is False)
    up = [r for r in directional if r.get("direction") == "UP"]
    down = [r for r in directional if r.get("direction") == "DOWN"]
    invalid = [r for r in rows if not r.get("valid")]
    flat = [r for r in directional if r.get("flat") is True]

    return {
        "horizon_seconds": horizon,
        "total_scored": len(rows),
        "valid_observations": len(valid),
        "invalid_observations": len(invalid),
        "directional_predictions": len(directional),
        "up_predictions": len(up),
        "down_predictions": len(down),
        "flat_outcomes": len(flat),
        "moving_directional_samples": len(moving),
        "correct": correct,
        "incorrect": incorrect,
        "directional_accuracy_pct": (100.0 * correct / len(moving)) if moving else None,
        "directional_coverage_pct": (100.0 * len(directional) / len(valid)) if valid else None,
        "moving_coverage_pct": (100.0 * len(moving) / len(directional)) if directional else None,
        "avg_return_after_up_pct": _mean(r.get("return_pct") for r in up),
        "avg_return_after_down_pct": _mean(r.get("return_pct") for r in down),
        "avg_directional_edge_pct": _mean(_edge_pct(r) for r in directional),
        "avg_edge_moving_only_pct": _mean(_edge_pct(r) for r in moving),
        "avg_score_delay_seconds": _mean(r.get("score_delay_seconds") for r in valid),
        "avg_actual_horizon_seconds": _mean(r.get("actual_horizon_seconds") for r in valid),
        "invalid_reasons": dict(Counter(str(r.get("invalid_reason") or "UNKNOWN") for r in invalid)),
    }


def _group_report(rows: Sequence[Dict[str, Any]], horizon: int, key_fn) -> Dict[str, Any]:
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for row in rows:
        groups.setdefault(str(key_fn(row)), []).append(row)
    out: Dict[str, Any] = {}
    for key, group in groups.items():
        out[key] = _stats(group, horizon)
    return out


def build_report(rows: Sequence[Dict[str, Any]], horizon: int, model_version: str) -> Dict[str, Any]:
    filtered = [
        r for r in rows
        if r.get("horizon_seconds") == horizon
        and (not model_version or r.get("model_version") == model_version)
    ]
    filtered.sort(key=lambda r: _f(r.get("generated_ts"), 0.0) or 0.0)

    directional_moving = [
        r for r in filtered
        if r.get("valid")
        and r.get("direction") in {"UP", "DOWN"}
        and r.get("flat") is False
        and r.get("correct") is not None
    ]
    rolling: Dict[str, Any] = {}
    for n in ROLLING_WINDOWS:
        sample = directional_moving[-n:]
        rolling[f"last_{n}"] = _stats(sample, horizon)
    rolling["all_moving_directional"] = _stats(directional_moving, horizon)

    valid_directional = [
        r for r in filtered
        if r.get("valid") and r.get("direction") in {"UP", "DOWN"}
    ]

    confidence_groups: Dict[str, List[Dict[str, Any]]] = {}
    for row in valid_directional:
        b = _confidence_bucket(_confidence(row, horizon))
        confidence_groups.setdefault(b, []).append(row)

    confidence_order = ["<50", "50-55", "55-60", "60-65", "65-70", "70-75", "75+", "NO_CONFIDENCE"]
    confidence_report = {
        key: _stats(confidence_groups[key], horizon)
        for key in confidence_order if key in confidence_groups
    }

    return {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "horizon_seconds": horizon,
        "model_version": model_version,
        "overall": _stats(filtered, horizon),
        "rolling": rolling,
        "by_time_of_day": _group_report(valid_directional, horizon, _time_bucket),
        "by_confidence": confidence_report,
        "by_symbol": _group_report(valid_directional, horizon, lambda r: r.get("symbol") or "UNKNOWN"),
    }


def _print_stats(label: str, s: Dict[str, Any]) -> None:
    print(
        f"{label:<24} n={s['moving_directional_samples']:<4} "
        f"acc={_pct(s['directional_accuracy_pct'])} "
        f"edge={_bp(s['avg_directional_edge_pct'])} "
        f"UP/DOWN={s['up_predictions']}/{s['down_predictions']} "
        f"flat={s['flat_outcomes']}"
    )


def print_report(report: Dict[str, Any]) -> None:
    o = report["overall"]
    print("=" * 78)
    print("AStock forward validation")
    print(f"Model: {report['model_version'] or 'ALL'}")
    print(f"Horizon: {report['horizon_seconds']}s")
    print("=" * 78)
    print(
        f"Scored={o['total_scored']}  Valid={o['valid_observations']}  "
        f"Directional={o['directional_predictions']}  Moving={o['moving_directional_samples']}  "
        f"Flat={o['flat_outcomes']}"
    )
    print(
        f"Correct={o['correct']}  Incorrect={o['incorrect']}  "
        f"Accuracy(moving only)={_pct(o['directional_accuracy_pct'])}  "
        f"Directional coverage={_pct(o['directional_coverage_pct'])}"
    )
    print(
        f"Avg directional edge(all directional)={_bp(o['avg_directional_edge_pct'])}  "
        f"Avg edge(moving only)={_bp(o['avg_edge_moving_only_pct'])}"
    )
    print(
        f"Avg return after UP={_pct(o['avg_return_after_up_pct'], 4)}  "
        f"Avg return after DOWN={_pct(o['avg_return_after_down_pct'], 4)}"
    )
    print(
        f"Scoring delay={o['avg_score_delay_seconds'] or 0:.2f}s  "
        f"Actual horizon={o['avg_actual_horizon_seconds'] or 0:.2f}s"
    )
    print()

    print("Rolling moving-directional samples")
    for key in ("last_50", "last_100", "last_250", "last_500", "all_moving_directional"):
        _print_stats(key, report["rolling"][key])
    print()

    print("Confidence buckets")
    for key, stats in report["by_confidence"].items():
        _print_stats(key, stats)
    print()

    print("Time of day")
    order = ("09:30-10:00", "10:00-11:00", "11:00-11:30", "13:00-14:00", "14:00-14:30", "14:30-15:00", "OTHER")
    for key in order:
        if key in report["by_time_of_day"]:
            _print_stats(key, report["by_time_of_day"][key])
    print()

    print("By symbol")
    for key, stats in sorted(
        report["by_symbol"].items(),
        key=lambda kv: kv[1]["moving_directional_samples"],
        reverse=True,
    ):
        _print_stats(key, stats)
    if o["invalid_reasons"]:
        print()
        print("Invalid samples:", json.dumps(o["invalid_reasons"], ensure_ascii=False, sort_keys=True))


def main() -> None:
    parser = argparse.ArgumentParser(description="Read-only forward validation report.")
    parser.add_argument("--data-root", default=str(DEFAULT_DATA_ROOT))
    parser.add_argument("--days", type=int, default=14, help="How many daily SQLite files to scan; 0 = all.")
    parser.add_argument("--horizon", type=int, choices=(60, 120), default=60)
    parser.add_argument("--model-version", default="", help="Exact model version. Default: latest valid model.")
    parser.add_argument("--json-out", default="", help="Optional output JSON path.")
    args = parser.parse_args()

    data_root = Path(args.data_root).expanduser()
    rows = _load_rows(data_root, args.days)
    if not rows:
        raise SystemExit(f"No forward_eval_v3 samples found under {data_root}")

    model_version = _choose_model(rows, args.horizon, args.model_version)
    report = build_report(rows, args.horizon, model_version)
    report["data_root"] = str(data_root)
    print_report(report)

    out_path = Path(args.json_out).expanduser() if args.json_out else data_root / "reports" / f"validation_{args.horizon}s_latest.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print()
    print(f"JSON report: {out_path}")


if __name__ == "__main__":
    main()
