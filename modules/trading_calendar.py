"""Shanghai trading days and intraday collection phases.

Trading days come from QMT (``xtdata.get_trading_calendar``) and are cached in
``<data root>/reference/trading_calendar_SH.json``. When the cache does not
cover a date the answer is ``None`` (unknown) and callers fall back to the
weekday rule, labelled as such, instead of silently assuming a session.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from modules.market_clock import SHANGHAI, market_now

# (name, start minute, end minute) in Shanghai wall-clock minutes; end exclusive.
PHASES: Tuple[Tuple[str, int, int], ...] = (
    ("OPEN_AUCTION", 9 * 60 + 15, 9 * 60 + 25),
    ("OPEN_CORE", 9 * 60 + 30, 10 * 60 + 30),
    ("AM_LATE", 10 * 60 + 30, 11 * 60 + 30),
    ("PM", 13 * 60, 15 * 60),
)
CONTINUOUS_PHASES = ("OPEN_CORE", "AM_LATE", "PM")
CALENDAR_FILE = Path("reference") / "trading_calendar_SH.json"


def phase_of(moment: datetime) -> Optional[str]:
    local = market_now(moment)
    minute = local.hour * 60 + local.minute + local.second / 60.0
    for name, start, end in PHASES:
        if start <= minute < end:
            return name
    return None


def phase_bounds(day: date, name: str) -> Tuple[datetime, datetime]:
    for phase, start, end in PHASES:
        if phase == name:
            base = datetime(day.year, day.month, day.day, tzinfo=SHANGHAI)
            return base + timedelta(minutes=start), base + timedelta(minutes=end)
    raise KeyError(name)


def phase_seconds(name: str) -> int:
    for phase, start, end in PHASES:
        if phase == name:
            return (end - start) * 60
    raise KeyError(name)


@dataclass
class TradingCalendar:
    days: List[str] = field(default_factory=list)   # YYYY-MM-DD
    first: str = ""
    last: str = ""
    source: str = "none"
    fetched_at: str = ""

    def is_trading_day(self, day: date) -> Optional[bool]:
        text = day.isoformat()
        if not self.days or not (self.first <= text <= self.last):
            return None
        return text in set(self.days)

    def resolve(self, day: date) -> Tuple[bool, str]:
        """(is trading day, basis) where basis says how the answer was obtained."""
        known = self.is_trading_day(day)
        if known is not None:
            return known, self.source
        return day.weekday() < 5, "weekday_fallback_unverified"

    def trading_days_between(self, start: date, end: date) -> List[Tuple[date, str]]:
        out: List[Tuple[date, str]] = []
        current = start
        while current <= end:
            ok, basis = self.resolve(current)
            if ok:
                out.append((current, basis))
            current += timedelta(days=1)
        return out

    def to_json(self) -> Dict:
        return {"days": self.days, "first": self.first, "last": self.last,
                "source": self.source, "fetched_at": self.fetched_at}


def load_calendar(data_root: Path) -> TradingCalendar:
    path = data_root / CALENDAR_FILE
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return TradingCalendar(days=sorted(raw.get("days") or []), first=raw.get("first", ""),
                               last=raw.get("last", ""), source=raw.get("source", "cache"),
                               fetched_at=raw.get("fetched_at", ""))
    except (OSError, ValueError):
        return TradingCalendar()


def save_calendar(data_root: Path, calendar: TradingCalendar) -> None:
    path = data_root / CALENDAR_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(calendar.to_json(), ensure_ascii=True, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def _normalize_days(values: Iterable) -> List[str]:
    out = set()
    for value in values or []:
        text = str(value).strip()
        if text.isdigit() and len(text) == 8:
            out.add(f"{text[:4]}-{text[4:6]}-{text[6:]}")
        elif text.isdigit() and len(text) >= 12:   # epoch milliseconds
            out.add(datetime.fromtimestamp(int(text) / 1000, SHANGHAI).date().isoformat())
        elif len(text) == 10 and text[4] == "-":
            out.add(text)
    return sorted(out)


def _worker(out_path: str, start: str, end: str) -> None:
    """Past days from get_trading_dates; future days from weekdays minus QMT holidays.

    Some broker builds do not implement get_trading_calendar ("function not
    realize"), so every call is attempted independently.
    """
    result: Dict = {"days": [], "source": "", "error": "", "future_known": False}
    errors: List[str] = []
    with contextlib.redirect_stdout(io.StringIO()):
        try:
            from xtquant import xtdata
            xtdata.enable_hello = False
        except Exception as exc:
            result["error"] = f"import: {type(exc).__name__}"
            Path(out_path).write_text(json.dumps(result), encoding="utf-8")
            return
        days: List[str] = []
        try:
            days = _normalize_days(xtdata.get_trading_calendar("SH", start, end) or [])
            result["source"] = "qmt_get_trading_calendar"
            result["future_known"] = bool(days)
        except Exception as exc:
            errors.append(f"calendar: {type(exc).__name__}")
        if not days:
            try:
                days = _normalize_days(xtdata.get_trading_dates("SH", start, "") or [])
                result["source"] = "qmt_get_trading_dates"
            except Exception as exc:
                errors.append(f"dates: {type(exc).__name__}")
            holidays: List[str] = []
            try:
                try:
                    xtdata.download_holiday_data()
                except Exception:
                    pass
                holidays = _normalize_days(xtdata.get_holidays() or [])
            except Exception as exc:
                errors.append(f"holidays: {type(exc).__name__}")
            if days and holidays:
                last = datetime.strptime(max(days), "%Y-%m-%d").date()
                stop = datetime.strptime(end, "%Y%m%d").date()
                current, hol = last + timedelta(days=1), set(holidays)
                while current <= stop:
                    if current.weekday() < 5 and current.isoformat() not in hol:
                        days.append(current.isoformat())
                    current += timedelta(days=1)
                result["source"] = "qmt_trading_dates+holidays"
                result["future_known"] = True
    result["days"] = sorted(set(days))
    result["error"] = "; ".join(errors)
    Path(out_path).write_text(json.dumps(result), encoding="utf-8")


def refresh_from_qmt(data_root: Path, timeout: float = 60.0, today: Optional[date] = None) -> Tuple[TradingCalendar, str]:
    """Query QMT in a bounded subprocess; keep the old cache on any failure."""
    today = today or market_now().date()
    start = (today - timedelta(days=400)).strftime("%Y%m%d")
    end = (today + timedelta(days=60)).strftime("%Y%m%d")
    root = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix="astock-cal-") as tmp:
        out = Path(tmp) / "calendar.json"
        code = f"import sys; sys.path.insert(0, {str(root)!r}); from modules.trading_calendar import _worker; _worker({str(out)!r}, {start!r}, {end!r})"
        try:
            subprocess.run([sys.executable, "-c", code], timeout=timeout, capture_output=True, cwd=str(root))
            raw = json.loads(out.read_text(encoding="utf-8"))
        except (subprocess.TimeoutExpired, OSError, ValueError) as exc:
            return load_calendar(data_root), f"refresh_failed:{type(exc).__name__}"
    days = raw.get("days") or []
    if not days:
        return load_calendar(data_root), "refresh_failed:" + (raw.get("error") or "empty_calendar")
    last = f"{end[:4]}-{end[4:6]}-{end[6:]}" if raw.get("future_known") else max(days)
    calendar = TradingCalendar(days=days, first=f"{start[:4]}-{start[4:6]}-{start[6:]}",
                               last=last, source=raw.get("source", "qmt"),
                               fetched_at=market_now().isoformat(timespec="seconds"))
    save_calendar(data_root, calendar)
    return calendar, "refreshed"
