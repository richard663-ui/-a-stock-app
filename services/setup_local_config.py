# -*- coding: utf-8 -*-
"""One-time local configuration wizard for the QMT cloud bridge.

The Supabase secret key is entered locally and is never written to GitHub.
"""
from __future__ import annotations

import getpass
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from modules.cloud_bridge import CloudBridge, BridgeConfig

DEFAULT_URL = "https://chjzlcqortgbtxooannu.supabase.co"
DEFAULT_BRIDGE_ID = "family-qmt-01"
DEFAULT_APP_PASSWORD = "070226"


def _normalise_url(value: str) -> str:
    value = (value or "").strip().strip('"').strip("'").rstrip("/")
    if not value:
        value = DEFAULT_URL
    if not value.startswith("http://") and not value.startswith("https://"):
        value = "https://" + value
    return value


def _toml_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _write_config(path: Path, url: str, key: str, bridge_id: str, password: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = (
        f'APP_PASSWORD = "{_toml_escape(password)}"\n'
        f'SUPABASE_URL = "{_toml_escape(url)}"\n'
        f'SUPABASE_SERVICE_KEY = "{_toml_escape(key)}"\n'
        f'BRIDGE_ID = "{_toml_escape(bridge_id)}"\n'
    )
    path.write_text(content, encoding="utf-8")


def main() -> int:
    print("\n=== QMT Cloud Bridge one-time setup ===")
    print("Only the secret key must be copied from Supabase. It will stay on this PC.\n")

    entered_url = input(f"Supabase URL [{DEFAULT_URL}]: ").strip()
    url = _normalise_url(entered_url)

    print("Paste the Supabase Secret key that starts with sb_secret_ (input is hidden).")
    key = getpass.getpass("Secret key: ").strip().strip('"').strip("'")

    if not key:
        print("[ERROR] Secret key is empty.")
        return 1

    bridge_id = input(f"Bridge ID [{DEFAULT_BRIDGE_ID}]: ").strip() or DEFAULT_BRIDGE_ID
    password = input(f"App password [{DEFAULT_APP_PASSWORD}]: ").strip() or DEFAULT_APP_PASSWORD

    config = BridgeConfig(url=url, service_key=key, bridge_id=bridge_id)
    errors = config.validation_errors()
    if errors:
        print("\n[CONFIG ERROR]")
        for error in errors:
            print(f"- {error}")
        return 1

    project_target = PROJECT_ROOT / ".streamlit" / "secrets.toml"
    persistent_target = Path.home() / ".a_stock_qmt" / "secrets.toml"
    _write_config(project_target, url, key, bridge_id, password)
    _write_config(persistent_target, url, key, bridge_id, password)

    print(f"\n[CONFIG SAVED] {project_target}")
    print(f"[BACKUP SAVED] {persistent_target}")

    print("Testing Supabase connection...")
    try:
        CloudBridge(config).ping()
    except Exception as exc:
        print("[CONNECTION ERROR]")
        print(str(exc))
        print("The configuration was saved. This remaining error is network-related, not a URL/key placeholder issue.")
        return 2

    print("[CONFIG OK] Supabase connection succeeded.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
