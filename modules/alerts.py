"""Alert delivery for collection health: local log always, phone push optional.

Optional private config ``~/.a_stock_qmt/alerts.toml`` (never in the repo)::

    [webhook]
    url = "https://ntfy.sh/<long-random-topic>"   # topic/key in the URL is a secret
    format = "ntfy"            # ntfy | bark | serverchan | json
    min_severity = "WARN"      # INFO | WARN | CRITICAL

    [toast]
    enabled = true             # Windows notification while someone is at the PC

Delivery failures are logged and never raised into the monitor loop.
"""
from __future__ import annotations

import json
import subprocess
import sys
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

try:
    import tomllib
except ImportError:  # pragma: no cover
    tomllib = None

SEVERITY_RANK = {"OK": 0, "INFO": 1, "WARN": 2, "CRITICAL": 3}


@dataclass
class Alert:
    key: str
    severity: str
    title: str
    message: str
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds"))
    resolved: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {"key": self.key, "severity": self.severity, "title": self.title,
                "message": self.message, "created_at": self.created_at, "resolved": self.resolved}


def load_alert_config(config_dir: Path) -> Dict[str, Any]:
    path = config_dir / "alerts.toml"
    if tomllib is None or not path.exists():
        return {}
    try:
        with path.open("rb") as handle:
            return tomllib.load(handle)
    except (OSError, ValueError):
        return {"_error": "alerts.toml could not be parsed"}


def _post(url: str, data: bytes, headers: Dict[str, str], timeout: float = 6.0) -> int:
    request = urllib.request.Request(url, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return int(response.status)


def build_webhook_request(cfg: Dict[str, Any], alert: Alert) -> Optional[tuple]:
    url = str(cfg.get("url") or "").strip()
    if not url.startswith("https://"):
        return None
    fmt = str(cfg.get("format") or "json").lower()
    title = f"[{alert.severity}] {alert.title}"
    if fmt == "ntfy":
        priority = {"CRITICAL": "urgent", "WARN": "high"}.get(alert.severity, "default")
        # HTTP headers are Latin-1 only; keep the (possibly Chinese) title in the body.
        headers = {"Title": f"AStock {alert.severity}", "Priority": priority,
                   "Tags": "chart_with_downwards_trend", "Content-Type": "text/plain; charset=utf-8"}
        return url, (title + "\n" + alert.message).encode("utf-8"), headers
    if fmt == "bark":
        body = {"title": title, "body": alert.message,
                "level": "timeSensitive" if alert.severity == "CRITICAL" else "active"}
        return url, json.dumps(body, ensure_ascii=False).encode("utf-8"), {"Content-Type": "application/json"}
    if fmt == "serverchan":
        form = urllib.parse.urlencode({"title": title[:32], "desp": alert.message}).encode("utf-8")
        return url, form, {"Content-Type": "application/x-www-form-urlencoded"}
    body = {"title": title, **alert.to_dict()}
    return url, json.dumps(body, ensure_ascii=False).encode("utf-8"), {"Content-Type": "application/json"}


def _toast(alert: Alert) -> None:
    if sys.platform != "win32":
        return
    text = (alert.title + ": " + alert.message).replace("'", "''")[:240]
    script = (
        "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] > $null;"
        "$t = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText01);"
        f"$t.GetElementsByTagName('text').Item(0).AppendChild($t.CreateTextNode('{text}')) > $null;"
        "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('AStock').Show([Windows.UI.Notifications.ToastNotification]::new($t))"
    )
    subprocess.Popen(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


class AlertDispatcher:
    def __init__(self, log_path: Path, config: Dict[str, Any], poster=_post, toaster=_toast):
        self.log_path = log_path
        self.config = config or {}
        self.poster = poster
        self.toaster = toaster
        self.last_delivery_error = ""

    def dispatch(self, alert: Alert) -> Dict[str, Any]:
        record = alert.to_dict()
        delivered = []
        webhook = self.config.get("webhook") or {}
        threshold = SEVERITY_RANK.get(str(webhook.get("min_severity", "WARN")).upper(), 2)
        if webhook and (SEVERITY_RANK.get(alert.severity, 0) >= threshold or alert.resolved):
            built = build_webhook_request(webhook, alert)
            if built:
                try:
                    status = self.poster(*built)
                    delivered.append(f"webhook:{status}")
                    self.last_delivery_error = ""
                except Exception as exc:  # network problems must not stop monitoring
                    self.last_delivery_error = type(exc).__name__
                    delivered.append(f"webhook_failed:{type(exc).__name__}")
        if (self.config.get("toast") or {}).get("enabled") and SEVERITY_RANK.get(alert.severity, 0) >= 2:
            try:
                self.toaster(alert)
                delivered.append("toast")
            except Exception as exc:
                delivered.append(f"toast_failed:{type(exc).__name__}")
        record["delivered"] = delivered
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        return record
