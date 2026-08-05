# -*- coding: utf-8 -*-
"""Small Supabase REST bridge used by both Streamlit Cloud and local QMT.

No secret is committed to GitHub. Configuration is read from Streamlit secrets,
environment variables, or a local .streamlit/secrets.toml file.
"""
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


@dataclass(frozen=True)
class BridgeConfig:
    url: str
    service_key: str
    bridge_id: str

    @property
    def ok(self) -> bool:
        return bool(self.url and self.service_key and self.bridge_id)


def _read_local_toml(path: str = ".streamlit/secrets.toml") -> Dict[str, Any]:
    if tomllib is None:
        return {}
    p = Path(path)
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
    return BridgeConfig(str(url).rstrip("/"), str(key), str(bridge_id))


class CloudBridge:
    def __init__(self, config: BridgeConfig, timeout: float = 8.0):
        if not config.ok:
            raise ValueError("Cloud bridge configuration is incomplete")
        self.config = config
        self.timeout = timeout
        self.base = f"{config.url}/rest/v1"
        self.headers = {
            "apikey": config.service_key,
            "Authorization": f"Bearer {config.service_key}",
            "Content-Type": "application/json",
        }

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
        payload = {
            "bridge_id": self.config.bridge_id,
            "symbol": str(symbol).upper(),
        }
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
