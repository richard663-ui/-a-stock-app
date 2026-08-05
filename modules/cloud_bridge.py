# -*- coding: utf-8 -*-
"""Supabase REST bridge shared by Streamlit Cloud and the local QMT bridge."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

try:
    import tomllib
except Exception:  # pragma: no cover
    tomllib = None

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SECRETS_PATH = PROJECT_ROOT / ".streamlit" / "secrets.toml"


def _clean_text(value: Any) -> str:
    return str(value or "").strip().strip("\ufeff")


def _contains_non_ascii(value: str) -> bool:
    try:
        value.encode("ascii")
        return False
    except UnicodeEncodeError:
        return True


@dataclass(frozen=True)
class BridgeConfig:
    url: str
    service_key: str
    bridge_id: str

    def validation_errors(self) -> List[str]:
        errors: List[str] = []
        if not self.url:
            errors.append("SUPABASE_URL is missing")
        elif not self.url.startswith("https://"):
            errors.append("SUPABASE_URL must start with https://")
        elif "supabase.co" not in self.url:
            errors.append("SUPABASE_URL does not look like a Supabase project URL")
        elif _contains_non_ascii(self.url):
            errors.append("SUPABASE_URL contains Chinese or other non-ASCII characters")

        if not self.service_key:
            errors.append("SUPABASE_SERVICE_KEY is missing")
        else:
            placeholders = ("YOUR_", "CHANGE_ME", "这里", "粘贴", "填写", "密钥")
            if any(x in self.service_key for x in placeholders):
                errors.append("SUPABASE_SERVICE_KEY still contains placeholder text")
            elif _contains_non_ascii(self.service_key):
                errors.append("SUPABASE_SERVICE_KEY contains Chinese or other non-ASCII characters")
            elif not (
                self.service_key.startswith("sb_secret_")
                or self.service_key.startswith("eyJ")
            ):
                errors.append("SUPABASE_SERVICE_KEY must be an sb_secret_ key or legacy service_role JWT")

        if not self.bridge_id:
            errors.append("BRIDGE_ID is missing")
        elif _contains_non_ascii(self.bridge_id):
            errors.append("BRIDGE_ID must use English letters, numbers, hyphen or underscore")
        return errors

    @property
    def ok(self) -> bool:
        return not self.validation_errors()


def _read_local_toml(path: Optional[str] = None) -> Dict[str, Any]:
    if tomllib is None:
        return {}
    p = Path(path) if path else DEFAULT_SECRETS_PATH
    if not p.is_absolute():
        p = PROJECT_ROOT / p
    if not p.exists():
        return {}
    try:
        with p.open("rb") as f:
            return tomllib.load(f)
    except Exception:
        return {}


def load_bridge_config(streamlit_secrets: Optional[Any] = None) -> BridgeConfig:
    values: Dict[str, Any] = {}
    if streamlit_secrets is not None:
        try:
            values = dict(streamlit_secrets)
        except Exception:
            values = {}

    local = _read_local_toml()
    values = {**local, **values}
    section = values.get("bridge", {}) if isinstance(values.get("bridge"), dict) else {}

    url = (
        section.get("SUPABASE_URL")
        or values.get("SUPABASE_URL")
        or os.environ.get("SUPABASE_URL")
        or ""
    )
    key = (
        section.get("SUPABASE_SERVICE_KEY")
        or values.get("SUPABASE_SERVICE_KEY")
        or os.environ.get("SUPABASE_SERVICE_KEY")
        or ""
    )
    bridge_id = (
        section.get("BRIDGE_ID")
        or values.get("BRIDGE_ID")
        or os.environ.get("BRIDGE_ID")
        or ""
    )
    return BridgeConfig(
        _clean_text(url).rstrip("/"),
        _clean_text(key),
        _clean_text(bridge_id),
    )


class CloudBridge:
    def __init__(self, config: BridgeConfig, timeout: float = 8.0):
        errors = config.validation_errors()
        if errors:
            raise ValueError("; ".join(errors))
        self.config = config
        self.timeout = timeout
        self.base = f"{config.url}/rest/v1"
        self.headers = {
            "apikey": config.service_key,
            "Content-Type": "application/json",
        }
        if config.service_key.startswith("eyJ"):
            self.headers["Authorization"] = f"Bearer {config.service_key}"

    def _request(self, method: str, path: str, **kwargs):
        headers = dict(self.headers)
        headers.update(kwargs.pop("headers", {}) or {})
        response = requests.request(
            method,
            f"{self.base}/{path.lstrip('/')}",
            headers=headers,
            timeout=self.timeout,
            **kwargs,
        )
        response.raise_for_status()
        if not response.content:
            return None
        try:
            return response.json()
        except Exception:
            return response.text

    def request_symbol(self, symbol: str) -> None:
        payload = {"bridge_id": self.config.bridge_id, "symbol": str(symbol).upper()}
        self._request(
            "POST",
            "qmt_watch_requests?on_conflict=bridge_id",
            json=payload,
            headers={"Prefer": "resolution=merge-duplicates,return=minimal"},
        )

    def get_requested_symbol(self) -> str:
        rows = self._request(
            "GET",
            "qmt_watch_requests",
            params={
                "bridge_id": f"eq.{self.config.bridge_id}",
                "select": "symbol,requested_at",
                "limit": "1",
            },
        ) or []
        if not rows:
            return ""
        return str(rows[0].get("symbol") or "").upper()

    def publish_ticks(self, symbol: str, ticks: List[Dict[str, Any]], status: str = "online") -> None:
        payload = {
            "bridge_id": self.config.bridge_id,
            "symbol": str(symbol).upper(),
            "status": status,
            "ticks": ticks,
        }
        self._request(
            "POST",
            "qmt_live_cache?on_conflict=bridge_id,symbol",
            json=payload,
            headers={"Prefer": "resolution=merge-duplicates,return=minimal"},
        )

    def fetch_ticks(self, symbol: str) -> Dict[str, Any]:
        rows = self._request(
            "GET",
            "qmt_live_cache",
            params={
                "bridge_id": f"eq.{self.config.bridge_id}",
                "symbol": f"eq.{str(symbol).upper()}",
                "select": "symbol,status,updated_at,ticks",
                "limit": "1",
            },
        ) or []
        return rows[0] if rows else {}
