"""Live collection-health monitor with alerts (read-only towards the recorders).

Separates *process health* (is the recorder running?) from *ingestion health*
(are new samples and raw snapshots actually arriving?). During every session
phase on a trading day it checks, every ``interval_seconds``:

* new 5-second training samples per basket symbol (continuous phases)
* new raw L1 snapshots per symbol (all phases, including the opening auction)
* recorder heartbeat freshness and QMT exchange-tick freshness
* collector processes, MiniQMT, code integrity, AC power, disk space

When the monitor itself was suspended (Windows standby / shutdown), it reports
the lost session time and the cause from the event log once it runs again.
Outside sessions it runs a readiness check (processes, QMT, AC power, power
button/lid actions) so problems are visible before the next open.

Outputs: ``<data>/reports/collection/live_status.json``,
``<data>/reports/collection/alerts.jsonl`` and optional phone push via
``~/.a_stock_qmt/alerts.toml`` (see modules/alerts.py). Never writes market data.
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from modules.alerts import Alert, AlertDispatcher, SEVERITY_RANK, load_alert_config
from modules.market_clock import SHANGHAI, market_now
from modules.paths import config_dir, data_root, install_root, runtime_dir
from modules.trading_calendar import CONTINUOUS_PHASES, PHASES, load_calendar, phase_bounds, phase_of, refresh_from_qmt

MONITOR_VERSION = "collection-monitor-v1-20261023"
LOCK_PORT = 49331
DUPLICATE_EXIT_CODE = 17
READINESS_TIMES = ("04:00", "08:40", "12:40")   # Shanghai; 04:00 is the evening before in Europe


@dataclass
class Finding:
    key: str
    severity: str           # INFO | WARN | CRITICAL
    title: str
    message: str
    immediate: bool = False  # send without debounce (incidents, integrity)


@dataclass
class KeyState:
    first_seen: float
    consecutive: int = 0
    sent_severity: str = "OK"
    last_sent: float = 0.0


@dataclass
class AlertState:
    warn_after_checks: int = 2
    critical_after_seconds: float = 180.0
    repeat_seconds: float = 1800.0
    keys: Dict[str, KeyState] = field(default_factory=dict)
    sent_once: set = field(default_factory=set)

    def update(self, findings: List[Finding], now: float) -> List[Alert]:
        out: List[Alert] = []
        for finding in findings:
            # One-shot events (incidents, readiness, daily reports): send once, never "recover".
            if finding.immediate and finding.key not in self.sent_once and not finding.key.startswith("INTEGRITY"):
                self.sent_once.add(finding.key)
                out.append(Alert(key=finding.key, severity=finding.severity, title=finding.title,
                                 message=finding.message))
        active = {f.key: f for f in findings if not f.immediate or f.key.startswith("INTEGRITY")}
        for key, finding in active.items():
            state = self.keys.setdefault(key, KeyState(first_seen=now))
            state.consecutive += 1
            severity = finding.severity
            if not finding.immediate:
                if state.consecutive < self.warn_after_checks:
                    continue
                if severity == "WARN" and now - state.first_seen >= self.critical_after_seconds \
                        and key.startswith(("INGEST_ALL", "HEARTBEAT", "PROCESS_COLLECTOR", "MINIQMT")):
                    severity = "CRITICAL"
            escalated = SEVERITY_RANK[severity] > SEVERITY_RANK[state.sent_severity]
            due = now - state.last_sent >= self.repeat_seconds
            if SEVERITY_RANK[severity] >= SEVERITY_RANK["INFO"] and (escalated or due):
                out.append(Alert(key=key, severity=severity, title=finding.title, message=finding.message))
                state.sent_severity, state.last_sent = severity, now
        for key in list(self.keys):
            if key not in active:
                state = self.keys.pop(key)
                if SEVERITY_RANK[state.sent_severity] >= SEVERITY_RANK["WARN"]:
                    out.append(Alert(key=key, severity="INFO", title="Recovered", resolved=True,
                                     message=f"{key} cleared."))
        return out


# ---------------------------------------------------------------- data probes

def _ro(path: Path) -> Optional[sqlite3.Connection]:
    if not path.exists():
        return None
    try:
        return sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=3)
    except sqlite3.Error:
        return None


def training_recent(root: Path, day: date, since: float) -> Dict[str, Tuple[float, int]]:
    conn = _ro(root / "training" / day.isoformat() / "l2_training.sqlite3")
    if conn is None:
        return {}
    try:
        rows = conn.execute("SELECT upper(symbol), max(generated_ts), count(*) FROM training_samples_v2 "
                            "WHERE generated_ts >= ? GROUP BY 1", (since,)).fetchall()
        return {s: (float(t), int(n)) for s, t, n in rows}
    except sqlite3.Error:
        return {}
    finally:
        conn.close()


def raw_recent(root: Path, day: date) -> Dict[str, float]:
    """Latest exchange timestamp per symbol among the newest raw snapshots."""
    conn = _ro(root / "raw" / day.isoformat() / "ticks.sqlite3")
    if conn is None:
        return {}
    try:
        top = conn.execute("SELECT max(id) FROM raw_ticks_v3").fetchone()[0] or 0
        rows = conn.execute("SELECT upper(symbol), max(qmt_time) FROM raw_ticks_v3 WHERE id > ? GROUP BY 1",
                            (top - 5000,)).fetchall()
        out = {}
        for symbol, qmt_time in rows:
            if qmt_time:
                value = float(qmt_time)
                out[symbol] = value / 1000.0 if value > 1e12 else value
        return out
    except sqlite3.Error:
        return {}
    finally:
        conn.close()


def read_json(path: Path) -> Dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def json_age(payload: Dict[str, Any], key: str, now: float) -> Optional[float]:
    try:
        return now - datetime.fromisoformat(str(payload[key])).timestamp()
    except (KeyError, TypeError, ValueError):
        return None


def process_snapshot(registry: Dict[str, Any]) -> Dict[str, Any]:
    """Worker counts per service and whether MiniQMT runs (Windows only)."""
    if sys.platform != "win32":
        return {"available": False}
    cmd = ("Get-CimInstance Win32_Process -Filter \"Name='python.exe' or Name='pythonw.exe' or "
           "Name='XtMiniQmt.exe' or Name='XtItClient.exe'\" | Select-Object Name,CommandLine | ConvertTo-Json -Compress")
    try:
        proc = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", cmd],
                              capture_output=True, timeout=30,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        items = json.loads(proc.stdout.decode("utf-8", errors="replace") or "[]")
    except (subprocess.TimeoutExpired, OSError, ValueError):
        return {"available": False}
    items = [items] if isinstance(items, dict) else (items or [])
    counts: Dict[str, int] = {}
    for svc in registry.get("services", []):
        needle = svc["script"].replace("/", "\\").lower()
        counts[svc["id"]] = sum(1 for i in items if needle in str(i.get("CommandLine") or "").lower()
                                and str(i.get("Name", "")).lower().startswith("python"))
    names = {str(i.get("Name", "")).lower() for i in items}
    return {"available": True, "workers": counts, "miniqmt": "xtminiqmt.exe" in names,
            "qmt_client": "xtitclient.exe" in names}


def ac_connected() -> Optional[bool]:
    try:
        from modules.collection_runtime import WindowsPowerBackend
        return WindowsPowerBackend().ac_connected()
    except Exception:
        return None


# ---------------------------------------------------------------- evaluation

@dataclass
class Context:
    now: float
    now_dt: datetime
    trading_day: bool
    calendar_basis: str
    phase: Optional[str]
    basket: List[str]
    training: Dict[str, Tuple[float, int]]
    raw: Dict[str, float]
    status: Dict[str, Any]
    processes: Dict[str, Any]
    registry: Dict[str, Any]
    integrity: Dict[str, Any]
    ac: Optional[bool]
    power_risks: List[str]
    free_gb: Optional[float]
    cfg: Dict[str, Any]


def _phase_age(ctx: Context) -> Optional[float]:
    if ctx.phase is None:
        return None
    start, _ = phase_bounds(ctx.now_dt.date(), ctx.phase)
    return ctx.now - start.timestamp()


def evaluate(ctx: Context) -> List[Finding]:
    m = ctx.cfg["monitor"]
    findings: List[Finding] = []
    in_session = ctx.trading_day and ctx.phase is not None
    phase_age = _phase_age(ctx) or 0.0

    if ctx.integrity and ctx.integrity.get("ok") is False:
        findings.append(Finding("INTEGRITY", "CRITICAL", "Code integrity check failed",
                                "; ".join(ctx.integrity.get("problems") or [])[:400] +
                                " Services refuse to start until a pinned release is reinstalled.", immediate=True))

    procs = ctx.processes
    if procs.get("available"):
        for svc in ctx.registry.get("services", []):
            if svc["id"] == "collection_monitor":
                continue
            n = procs["workers"].get(svc["id"], 0)
            collector = svc.get("collects_market_data")
            if n == 0 and (collector or in_session):
                sev = "CRITICAL" if (collector and in_session) else "WARN"
                findings.append(Finding(f"PROCESS_{'COLLECTOR' if collector else 'SERVICE'}_{svc['id']}", sev,
                                        f"{svc['id']} not running", f"No worker process for {svc['script']}."))
            elif n > 1:
                findings.append(Finding(f"PROCESS_DUPLICATE_{svc['id']}", "WARN", f"{svc['id']} duplicated",
                                        f"{n} workers for {svc['script']}; duplicates can double-write data."))
        if not procs.get("miniqmt") and ctx.trading_day:
            findings.append(Finding("MINIQMT", "CRITICAL" if in_session else "WARN", "MiniQMT not running",
                                    "Log in to the QMT terminal and enable the market-data connection."))

    if ctx.trading_day and ctx.ac is False:
        findings.append(Finding("AC_POWER", "WARN", "Laptop on battery",
                                "The recorder only keeps Windows awake on AC power. Plug in before the session."))
    if ctx.free_gb is not None and ctx.free_gb < m["min_free_disk_gb"]:
        findings.append(Finding("DISK", "WARN", "Low disk space", f"{ctx.free_gb:.1f} GB free on the data drive."))

    if not in_session:
        return findings

    heartbeat = json_age(ctx.status, "updated_at", ctx.now)
    if heartbeat is None or heartbeat > m["heartbeat_stale_seconds"]:
        findings.append(Finding("HEARTBEAT", "WARN", "Training recorder heartbeat stale",
                                f"training_status.json age={heartbeat if heartbeat is None else round(heartbeat)}s; "
                                "process may be hung or stopped."))

    if phase_age < 90:  # recorders rebuild their 60-second windows after each break
        return findings

    stale_train, stale_raw = [], []
    for symbol in ctx.basket:
        if ctx.phase in CONTINUOUS_PHASES:
            last = ctx.training.get(symbol, (None, 0))[0]
            if last is None or ctx.now - last > m["training_stale_seconds"]:
                stale_train.append(symbol)
        last_raw = ctx.raw.get(symbol)
        if last_raw is None or ctx.now - last_raw > m["raw_stale_seconds"]:
            stale_raw.append(symbol)
    n = len(ctx.basket)
    training_all = ctx.phase in CONTINUOUS_PHASES and len(stale_train) == n
    if training_all and len(stale_raw) == n:
        findings.append(Finding("INGEST_ALL", "WARN", "No market data is being recorded",
                                f"Phase {ctx.phase}: no new training samples or raw snapshots for any of {n} symbols."))
    elif training_all:
        findings.append(Finding("INGEST_ALL_TRAINING", "WARN", "Training samples stopped",
                                f"Raw snapshots still arrive but no training samples for {n} symbols in {ctx.phase}."))
    elif len(stale_raw) == n:
        findings.append(Finding("INGEST_ALL_RAW", "WARN", "Raw snapshot recording stopped",
                                f"No raw snapshots for any symbol in {ctx.phase}; research recorder may be down."))
    elif stale_train or stale_raw:
        names = sorted(set(stale_train) | set(stale_raw))
        if len(names) * 2 >= n:
            findings.append(Finding("INGEST_PARTIAL", "WARN", "Some symbols not recorded",
                                    f"{', '.join(names)} stale in {ctx.phase} (suspension, limit lock or feed issue)."))

    feeds = (ctx.status.get("feeds") or {}) if isinstance(ctx.status, dict) else {}
    bad = [s for s, f in feeds.items() if isinstance(f, dict) and f.get("state") in ("STALE_EXCHANGE_TICK", "NO_QMT_TICK")]
    if ctx.phase in CONTINUOUS_PHASES and feeds and len(bad) * 2 >= len(feeds):
        findings.append(Finding("EXCHANGE_FEED", "WARN", "QMT quotes stale",
                                f"{len(bad)}/{len(feeds)} symbols have no fresh exchange tick; check QMT login/network."))
    return findings


# ---------------------------------------------------------------- incidents

def lost_session_seconds(start: float, end: float) -> Dict[str, float]:
    lost: Dict[str, float] = {}
    day = datetime.fromtimestamp(start, SHANGHAI).date()
    last_day = datetime.fromtimestamp(end, SHANGHAI).date()
    while day <= last_day:
        for name, _, _ in PHASES:
            lo, hi = (x.timestamp() for x in phase_bounds(day, name))
            overlap = min(hi, end) - max(lo, start)
            if overlap > 0:
                lost[f"{day.isoformat()} {name}"] = round(overlap, 1)
        day += timedelta(days=1)
    return lost


def suspension_finding(start: float, end: float, calendar) -> Optional[Finding]:
    lost = {k: v for k, v in lost_session_seconds(start, end).items()
            if calendar.resolve(date.fromisoformat(k.split()[0]))[0]}
    if not lost:
        return None
    cause = "unknown"
    try:
        from services.collection_report import attribute_gap, load_power_history
        history = load_power_history(datetime.fromtimestamp(start, SHANGHAI).date(),
                                     datetime.fromtimestamp(end, SHANGHAI).date()) or []
        attributed = attribute_gap(start, end, history)
        cause = attributed.get("standby_reason") or attributed.get("cause") or cause
    except Exception:
        pass
    total_min = sum(lost.values()) / 60.0
    span = (f"{datetime.fromtimestamp(start, SHANGHAI):%m-%d %H:%M} to "
            f"{datetime.fromtimestamp(end, SHANGHAI):%m-%d %H:%M} Shanghai")
    return Finding(f"SUSPENDED_{int(start)}", "CRITICAL", "Collection was interrupted",
                   f"The PC/monitor was not running {span}; {total_min:.0f} session minutes lost "
                   f"({', '.join(lost)}). Cause: {cause}. Missing data is not filled.", immediate=True)


# ---------------------------------------------------------------- main loop

def load_registry() -> Dict[str, Any]:
    return read_json(PROJECT_ROOT / "deploy" / "services.json")


def load_config() -> Dict[str, Any]:
    return json.loads((PROJECT_ROOT / "configs" / "collection.json").read_text(encoding="utf-8"))


def _hhmm(text: str) -> int:
    hour, minute = text.split(":")
    return int(hour) * 60 + int(minute)


def write_status(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    os.replace(tmp, path)


class CloudPublisher:
    """Best-effort upload of live status to collection_health_v1 (never blocks the loop)."""

    def __init__(self) -> None:
        self.disabled_until = 0.0
        self.error = ""
        self.busy = False
        self.bridge = None
        self.bridge_id = ""

    def publish(self, payload: Dict[str, Any], now: float) -> None:
        if self.busy or now < self.disabled_until:
            return
        self.busy = True
        import threading
        threading.Thread(target=self._run, args=(payload, now), daemon=True, name="collection-cloud").start()

    def _run(self, payload: Dict[str, Any], now: float) -> None:
        try:
            if self.bridge is None:
                from modules.cloud_bridge import CloudBridge, load_bridge_config
                cfg = load_bridge_config()
                if not cfg.ok:
                    self.error, self.disabled_until = "bridge config incomplete", now + 3600
                    return
                self.bridge, self.bridge_id = CloudBridge(cfg, timeout=5.0), cfg.bridge_id
            row = {"bridge_id": self.bridge_id, "updated_at": payload["checked_at"],
                   "monitor_version": MONITOR_VERSION, "overall": payload["overall"], "phase": payload["phase"],
                   "trading_day": payload["trading_day"], "findings": payload["findings"],
                   "symbols": payload["symbols"], "processes": payload["processes"]}
            self.bridge._request("POST", "collection_health_v1?on_conflict=bridge_id", json=row,
                                 headers={"Prefer": "resolution=merge-duplicates,return=minimal"})
            self.error = ""
        except Exception as exc:
            # 404 = table not created yet (migration pending): retry hourly, otherwise in 2 minutes.
            self.error = f"{type(exc).__name__}: {str(exc)[:120]}"
            self.disabled_until = now + (3600 if "404" in str(exc) else 120)
        finally:
            self.busy = False


def daily_report(root: Path, day: date, cfg: Dict[str, Any]) -> Optional[Finding]:
    from services.collection_report import build_report, write_outputs
    calendar = load_calendar(root)
    report = build_report(root, day, day, calendar, cfg["basket"], cfg)
    if not report["days"]:
        return None
    write_outputs(root / "reports" / "coverage", report, f"daily_{day.isoformat()}")
    d = report["days"][0]
    phases = ", ".join(f"{k} {v['avg_coverage_pct']}%" for k, v in d["phases"].items())
    severity = "INFO" if d["status"] == "GOOD" else "WARN"
    return Finding(f"DAILY_REPORT_{day.isoformat()}", severity, f"Coverage {day.isoformat()}: {d['status']}",
                   f"Overall {d['overall_coverage_pct']}% ({phases}).", immediate=True)


def main() -> int:
    from modules.runtime_guard import acquire_bridge_lock
    lock = acquire_bridge_lock(port=LOCK_PORT)
    if lock is None:
        print("collection monitor already running; exiting duplicate")
        return DUPLICATE_EXIT_CODE
    root = data_root()
    cfg = load_config()
    m = cfg["monitor"]
    registry = load_registry()
    out_dir = root / "reports" / "collection"
    dispatcher = AlertDispatcher(out_dir / "alerts.jsonl", load_alert_config(config_dir()))
    state = AlertState(m["warn_after_checks"], m["critical_after_seconds"], m["repeat_alert_minutes"] * 60.0)
    calendar = load_calendar(root)
    processes: Dict[str, Any] = {"available": False}
    power_risks: List[str] = []
    last_proc = last_power = 0.0
    last_loop: Optional[float] = None
    done: Dict[str, str] = {}
    cloud = CloudPublisher()
    last_cloud = 0.0
    print(f"Collection monitor {MONITOR_VERSION} started; data root {root}")
    while True:
        now = time.time()
        now_dt = market_now()
        pending: List[Finding] = []
        if last_loop is not None and now - last_loop > max(60.0, 4 * m["interval_seconds"]):
            incident = suspension_finding(last_loop, now, calendar)
            if incident:
                pending.append(incident)
        last_loop = now
        today = now_dt.date()
        trading, basis = calendar.resolve(today)
        if now - last_proc >= m["process_check_seconds"]:
            processes, last_proc = process_snapshot(registry), now
        if now - last_power >= 1800:
            try:
                from modules.power_events import power_settings
                power_risks = power_settings().get("ac_risks") or []
            except Exception:
                power_risks = []
            last_power = now
        integrity = read_json(runtime_dir() / "integrity_status.json") if install_root() else {}
        try:
            free_gb = shutil.disk_usage(root).free / 1e9
        except OSError:
            free_gb = None
        phase = phase_of(now_dt)
        ctx = Context(now=now, now_dt=now_dt, trading_day=trading, calendar_basis=basis, phase=phase,
                      basket=[s.upper() for s in cfg["basket"]],
                      training=training_recent(root, today, now - 600) if phase else {},
                      raw=raw_recent(root, today) if phase else {},
                      status=read_json(root / "training" / "training_status.json"),
                      processes=processes, registry=registry, integrity=integrity, ac=ac_connected(),
                      power_risks=power_risks, free_gb=free_gb, cfg=cfg)
        findings = evaluate(ctx) + pending

        minute = now_dt.hour * 60 + now_dt.minute
        slot = next((t for t in READINESS_TIMES if 0 <= minute - _hhmm(t) < 10), None)
        if slot and trading and done.get(f"ready{slot}") != today.isoformat():
            done[f"ready{slot}"] = today.isoformat()
            issues = [f for f in findings if f.severity != "INFO"]
            if power_risks:
                issues.append(Finding("POWER_SETTINGS", "WARN", "Power settings will stop recording",
                                      "; ".join(power_risks) + ". Run tools\\set_collection_power_settings.ps1 "
                                      "-Apply (docs/OPERATIONS.md section 2) and lock with Win+L."))
            text = "; ".join(f"{f.title}" for f in issues) or "All collectors, QMT, AC power and integrity OK."
            pending.append(Finding(f"READINESS_{slot}", "WARN" if issues else "INFO",
                                   f"Pre-session readiness {slot}", text, immediate=True))
        if trading and minute >= _hhmm(m["daily_report_after"]) and done.get("report") != today.isoformat():
            done["report"] = today.isoformat()
            try:
                finding = daily_report(root, today, cfg)
                if finding:
                    pending.append(finding)
            except Exception as exc:
                print(f"[WARN] daily report failed: {type(exc).__name__}: {exc}")
        if minute >= _hhmm(m["calendar_refresh_after"]) and done.get("calendar") != today.isoformat():
            done["calendar"] = today.isoformat()
            calendar, note = refresh_from_qmt(root)
            print(f"calendar {note} source={calendar.source}")

        extra = [f for f in pending if all(f.key != g.key for g in findings)]
        for alert in state.update(findings + extra, now):
            record = dispatcher.dispatch(alert)
            print(f"{now_dt:%H:%M:%S} ALERT {record['severity']} {record['key']}: {record['message']}")
        overall = max([f.severity for f in findings] + ["OK"], key=lambda s: SEVERITY_RANK[s])
        status_payload = {
            "monitor_version": MONITOR_VERSION, "checked_at": now_dt.isoformat(timespec="seconds"),
            "trading_day": trading, "calendar_basis": basis, "phase": phase, "overall": overall,
            "findings": [f.__dict__ for f in findings],
            "symbols": {s: {"training_age_s": round(now - ctx.training[s][0], 1) if s in ctx.training else None,
                            "raw_age_s": round(now - ctx.raw[s], 1) if s in ctx.raw else None}
                        for s in ctx.basket},
            "processes": processes, "ac_power": ctx.ac, "power_setting_risks": power_risks,
            "integrity_ok": integrity.get("ok") if integrity else None,
            "alert_delivery_error": dispatcher.last_delivery_error,
            "cloud_publish_error": cloud.error,
        }
        write_status(out_dir / "live_status.json", status_payload)
        if now - last_cloud >= 60:
            cloud.publish(status_payload, now)
            last_cloud = now
        time.sleep(max(1.0, m["interval_seconds"] - (time.time() - now)))


if __name__ == "__main__":
    raise SystemExit(main())
