"""Collection coverage by trading day, symbol and session phase (read-only).

Measures what was actually recorded, never what the processes claimed:

* training samples (5-second buckets, ``training/<day>/l2_training.sqlite3``)
* raw L1 snapshots (``raw/<day>/ticks.sqlite3``, exchange timestamps)
* collection-wide gaps, duplicate buckets, rows outside session phases,
  suspected stale-quote runs, and the Windows standby periods behind gaps.

Nothing is filled or repaired. Missing data is reported as missing.

    python services/collection_report.py --start 2026-09-01 --end 2026-10-23
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sqlite3
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from modules.market_clock import SHANGHAI, market_now
from modules.paths import data_root as default_data_root
from modules.trading_calendar import PHASES, TradingCalendar, load_calendar, phase_bounds, phase_seconds

REPORT_VERSION = "collection-report-v1-20261023"


def load_config() -> Dict[str, Any]:
    return json.loads((PROJECT_ROOT / "configs" / "collection.json").read_text(encoding="utf-8"))


def _connect_ro(path: Path) -> Optional[sqlite3.Connection]:
    if not path.exists():
        return None
    try:
        return sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=5)
    except sqlite3.Error:
        return None


def _has_table(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def load_training(path: Path) -> List[Tuple]:
    conn = _connect_ro(path)
    if conn is None:
        return []
    try:
        if not _has_table(conn, "training_samples_v2"):
            return []
        return conn.execute(
            "SELECT upper(symbol), sample_bucket, generated_ts, valid, labeled_at IS NOT NULL, mid_price, "
            "CAST(json_extract(features_json,'$.volume') AS REAL), recorder_version "
            "FROM training_samples_v2 ORDER BY generated_ts"
        ).fetchall()
    except sqlite3.Error:
        return []
    finally:
        conn.close()


def load_raw(path: Path) -> List[Tuple[str, float]]:
    conn = _connect_ro(path)
    if conn is None:
        return []
    try:
        if not _has_table(conn, "raw_ticks_v3"):
            return []
        rows = []
        for symbol, qmt_time, recorded in conn.execute("SELECT upper(symbol), qmt_time, recorded_at FROM raw_ticks_v3"):
            ts = None
            if qmt_time:
                ts = float(qmt_time) / 1000.0 if float(qmt_time) > 1e12 else float(qmt_time)
            elif recorded:
                try:
                    ts = datetime.fromisoformat(str(recorded).replace("Z", "+00:00")).timestamp()
                except ValueError:
                    ts = None
            if ts:
                rows.append((symbol, ts))
        return rows
    except sqlite3.Error:
        return []
    finally:
        conn.close()


def _status(pct: float, cfg: Dict[str, Any]) -> str:
    if pct >= cfg["coverage_good_pct"]:
        return "GOOD"
    if pct >= cfg["coverage_partial_pct"]:
        return "PARTIAL"
    return "LOW" if pct > 0 else "MISSING"


def find_gaps(stamps: List[float], start: float, end: float, min_gap: float) -> List[Tuple[float, float]]:
    gaps, previous = [], start
    for ts in sorted(s for s in stamps if start <= s < end):
        if ts - previous > min_gap:
            gaps.append((previous, ts))
        previous = ts
    if end - previous > min_gap:
        gaps.append((previous, end))
    return gaps


def stale_runs(rows: List[Tuple], min_len: int) -> int:
    """Runs of >= min_len consecutive samples with identical mid price and cumulative volume."""
    runs, length, last = 0, 1, None
    for row in rows:
        key = (row[5], row[6])
        if last is not None and key == last and row[6] is not None:
            length += 1
            if length == min_len:
                runs += 1
        else:
            length = 1
        last = key
    return runs


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, SHANGHAI).isoformat(timespec="seconds")


def analyze_day(root: Path, day: date, symbols: Iterable[str], cfg: Dict[str, Any],
                standby: Optional[List[Dict]] = None) -> Dict[str, Any]:
    day_text = day.isoformat()
    training = load_training(root / "training" / day_text / "l2_training.sqlite3")
    raw = load_raw(root / "raw" / day_text / "ticks.sqlite3")
    by_symbol: Dict[str, List[Tuple]] = defaultdict(list)
    for row in training:
        by_symbol[row[0]].append(row)
    raw_by_symbol: Dict[str, List[float]] = defaultdict(list)
    for symbol, ts in raw:
        raw_by_symbol[symbol].append(ts)
    symbol_list = sorted(set(s.upper() for s in symbols) | set(by_symbol))
    step = cfg["sample_seconds"]
    rows: List[Dict[str, Any]] = []
    gaps: List[Dict[str, Any]] = []
    phase_windows = {name: tuple(x.timestamp() for x in phase_bounds(day, name)) for name, _, _ in PHASES}

    for name, _, _ in PHASES:
        lo, hi = phase_windows[name]
        expected = phase_seconds(name) // step
        union: List[float] = []
        for symbol in symbol_list:
            part = [r for r in by_symbol.get(symbol, []) if lo <= r[2] < hi]
            union.extend(r[2] for r in part)
            buckets = {int(r[1]) for r in part}
            dup = len(part) - len({(r[1]) for r in part})
            raw_ts = [t for t in raw_by_symbol.get(symbol, []) if lo <= t < hi]
            raw_buckets = {int(t // 10) for t in raw_ts}
            coverage = 100.0 * min(len(buckets), expected) / expected
            raw_cov = 100.0 * min(len(raw_buckets), phase_seconds(name) // 10) / (phase_seconds(name) // 10)
            # The auction is judged on the raw snapshot archive: training samples
            # there are intentionally sparse (warm-up + stale-tick rejection).
            measure = raw_cov if name == "OPEN_AUCTION" else coverage
            rows.append({
                "date": day_text, "symbol": symbol, "phase": name,
                "expected_samples": expected, "samples": len(buckets),
                "valid_labeled": sum(1 for r in part if r[3] == 1 and r[4]),
                "coverage_pct": round(coverage, 2),
                "measure": "raw_10s" if name == "OPEN_AUCTION" else "training_5s",
                "measure_pct": round(measure, 2), "status": _status(measure, cfg),
                "raw_snapshots": len(raw_ts),
                "raw_10s_coverage_pct": round(raw_cov, 2),
                "duplicate_bucket_rows": dup,
                "suspected_stale_runs": stale_runs(part, cfg["stale_run_samples"]) if name != "OPEN_AUCTION" else 0,
                "recorder_versions": "|".join(sorted({r[7] for r in part})),
            })
        for start, end in find_gaps(union, lo, hi, cfg["gap_report_seconds"]):
            cause = attribute_gap(start, end, standby)
            gaps.append({"date": day_text, "phase": name, "from": _iso(start), "to": _iso(end),
                         "seconds": round(end - start, 1), "scope": "all_symbols", **cause})

    in_phase = 0
    for row in training:
        if any(lo <= row[2] < hi for lo, hi in phase_windows.values()):
            in_phase += 1
    phase_summary = {}
    for name, _, _ in PHASES:
        part = [r for r in rows if r["phase"] == name]
        avg = sum(r["measure_pct"] for r in part) / len(part) if part else 0.0
        phase_summary[name] = {"avg_coverage_pct": round(avg, 2), "status": _status(avg, cfg),
                               "measure": part[0]["measure"] if part else None}
    overall = (sum(r["measure_pct"] * r["expected_samples"] for r in rows) /
               max(1, sum(r["expected_samples"] for r in rows)))
    return {
        "date": day_text,
        "has_training_db": (root / "training" / day_text / "l2_training.sqlite3").exists(),
        "training_rows": len(training), "raw_snapshots": len(raw),
        "out_of_phase_rows": len(training) - in_phase,
        "overall_coverage_pct": round(overall, 2), "status": _status(overall, cfg),
        "phases": phase_summary, "rows": rows, "gaps": gaps,
        "standby_periods": [
            {"entered": _iso(s["entered"].timestamp()),
             "exited": _iso(s["exited"].timestamp()) if s.get("exited") else None,
             "reason": s.get("reason"), "user_initiated": s.get("user_initiated")}
            for s in (standby or [])],
    }


def suspends_apps(item: Dict[str, Any]) -> bool:
    """Did this interval really stop desktop processes?

    Idle-timeout "standby" while a recorder holds ES_SYSTEM_REQUIRED only turns
    the screen off; the CPU stays active (Oct 21 recorded a full session through
    one). User-initiated standby, shutdowns, standby ended by a power loss or
    reboot, and intervals spent mostly in low-power DRIPS do stop collection.
    """
    if item.get("user_initiated") or item.get("contains_restart") or str(item.get("reason", "")).startswith("PC "):
        return True
    seconds = float(item.get("seconds") or 0.0)
    active = item.get("active_seconds")
    if active is None:
        return item.get("exited") is None
    return seconds > 0 and float(active) < 0.5 * seconds


def attribute_gap(start: float, end: float, standby: Optional[List[Dict]]) -> Dict[str, Any]:
    for item in standby or []:
        if not suspends_apps(item):
            continue
        entered = item["entered"].timestamp()
        exited = item["exited"].timestamp() if item.get("exited") else float("inf")
        if entered < end and exited > start:
            cause = "pc_off" if str(item.get("reason", "")).startswith("PC ") else "windows_standby"
            return {"cause": cause, "standby_reason": item.get("reason"),
                    "user_initiated_standby": bool(item.get("user_initiated"))}
    return {"cause": "pc_awake_but_not_recording_or_no_power_record", "standby_reason": None,
            "user_initiated_standby": None}


def load_power_history(start: date, end: date) -> Optional[List[Dict]]:
    """Shutdown and standby intervals from 10 days before start to the end of end.

    A standby interval that contains a boot / unexpected-restart event ended in
    a power loss or forced restart; its wake-time residency figures belong to a
    different session, so it is marked ``contains_restart``.
    """
    try:
        from modules.power_events import boot_events, power_off_intervals, standby_intervals
    except Exception:
        return None
    lo = datetime(start.year, start.month, start.day, tzinfo=SHANGHAI) - timedelta(days=10)
    hi = datetime(end.year, end.month, end.day, 23, 59, tzinfo=SHANGHAI)
    try:
        boots = boot_events(lo)
        standby = standby_intervals(lo, hi)
    except Exception:
        return None
    for item in standby:
        until = item.get("exited") or hi
        item["contains_restart"] = any(item["entered"] <= b["time"] <= until
                                       for b in boots if b["kind"] in ("boot", "unexpected_restart"))
        if item["contains_restart"]:
            item["reason"] = f"{item['reason']} then power loss/restart"
    # An unclean restart (Kernel-Power 41) means the PC died or was forced off:
    # it was unavailable from the last logged activity until that boot.
    activity = sorted([i["entered"] for i in standby] + [i["exited"] for i in standby if i.get("exited")] +
                      [b["time"] for b in boots if b["kind"] != "unexpected_restart"])
    outages = []
    for b in boots:
        if b["kind"] != "unexpected_restart":
            continue
        before = [t for t in activity if t < b["time"] - timedelta(minutes=5)]
        if before:
            outages.append({"entered": before[-1], "exited": b["time"], "user_initiated": False,
                            "reason": "PC off or died (unclean restart; last activity "
                                      f"{before[-1].astimezone(SHANGHAI):%m-%d %H:%M} Shanghai)"})
    return outages + power_off_intervals(lo, hi, boots) + standby


def build_report(root: Path, start: date, end: date, calendar: TradingCalendar,
                 symbols: Iterable[str], cfg: Dict[str, Any], include_power: bool = True) -> Dict[str, Any]:
    days = []
    history = load_power_history(start, end) if include_power else None
    for day, basis in calendar.trading_days_between(start, end):
        standby = None
        if history is not None:
            session_start, _ = phase_bounds(day, "OPEN_AUCTION")
            _, session_end = phase_bounds(day, "PM")
            standby = [i for i in history
                       if i["entered"] <= session_end and (i.get("exited") or session_end) >= session_start]
        result = analyze_day(root, day, symbols, cfg, standby)
        result["calendar_basis"] = basis
        days.append(result)
    return {"report_version": REPORT_VERSION, "generated_at": market_now().isoformat(timespec="seconds"),
            "start": start.isoformat(), "end": end.isoformat(), "calendar_source": calendar.source,
            "days": days}


def summary_markdown(report: Dict[str, Any]) -> str:
    lines = [f"# Collection coverage {report['start']} .. {report['end']}",
             f"Generated {report['generated_at']} ({report['report_version']}); calendar: {report['calendar_source']}.",
             "Coverage = distinct 5-second training samples / expected (auction: raw 10-second snapshot buckets),",
             "averaged over the basket. The first ~60 s after 09:15, 09:30 and 13:00 are training warm-up by design.", "",
             "| Date | Overall | Auction | 09:30-10:30 | 10:30-11:30 | PM | Status | Main gap cause |",
             "|---|---|---|---|---|---|---|---|"]
    for day in report["days"]:
        phases = day["phases"]
        causes = sorted({g.get("standby_reason") or g["cause"] for g in day["gaps"] if g["seconds"] >= 300})
        lines.append("| {} | {}% | {}% | {}% | {}% | {}% | {} | {} |".format(
            day["date"], day["overall_coverage_pct"],
            *(phases[p]["avg_coverage_pct"] for p in ("OPEN_AUCTION", "OPEN_CORE", "AM_LATE", "PM")),
            day["status"] + ("" if day.get("calendar_basis", "").startswith("qmt") else " (calendar unverified)"),
            ", ".join(causes) or "-"))
    good = sum(1 for d in report["days"] if d["status"] == "GOOD")
    lines += ["", f"{good} of {len(report['days'])} trading days have GOOD coverage."]
    return "\n".join(lines) + "\n"


def write_outputs(out_dir: Path, report: Dict[str, Any], tag: str) -> Dict[str, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "rows": out_dir / f"coverage_{tag}.csv", "gaps": out_dir / f"gaps_{tag}.csv",
        "json": out_dir / f"summary_{tag}.json", "markdown": out_dir / f"summary_{tag}.md",
    }
    rows = [r for d in report["days"] for r in d["rows"]]
    gaps = [g for d in report["days"] for g in d["gaps"]]
    for key, data in (("rows", rows), ("gaps", gaps)):
        tmp = paths[key].with_suffix(".tmp")
        with tmp.open("w", newline="", encoding="utf-8-sig") as handle:
            if data:
                writer = csv.DictWriter(handle, fieldnames=list(data[0].keys()))
                writer.writeheader()
                writer.writerows(data)
        os.replace(tmp, paths[key])
    slim = {**report, "days": [{k: v for k, v in d.items() if k not in ("rows", "gaps")} for d in report["days"]]}
    for key, text in (("json", json.dumps(slim, ensure_ascii=False, indent=1)), ("markdown", summary_markdown(report))):
        tmp = paths[key].with_suffix(".tmp")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, paths[key])
    return paths


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Collection coverage report (read-only)")
    parser.add_argument("--start", type=date.fromisoformat, default=None)
    parser.add_argument("--end", type=date.fromisoformat, default=None)
    parser.add_argument("--data-root", type=Path, default=default_data_root())
    parser.add_argument("--no-power-events", action="store_true")
    args = parser.parse_args(argv)
    today = market_now().date()
    end = args.end or today
    start = args.start or end - timedelta(days=7)
    cfg = load_config()
    calendar = load_calendar(args.data_root)
    report = build_report(args.data_root, start, end, calendar, cfg["basket"], cfg, not args.no_power_events)
    paths = write_outputs(args.data_root / "reports" / "coverage", report, f"{start}_{end}")
    print(summary_markdown(report))
    print("Files: " + ", ".join(str(p) for p in paths.values()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
