# -*- coding: utf-8 -*-
from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from modules.cloud_bridge import CloudBridge, load_bridge_config


def main() -> int:
    config = load_bridge_config()
    errors = config.validation_errors()
    if errors:
        print("[CONFIG ERROR]")
        for error in errors:
            print(f"- {error}")
        print()
        print("Open .streamlit\\secrets.toml and replace all placeholder text with real values.")
        return 1

    try:
        bridge = CloudBridge(config)
        symbol = bridge.get_requested_symbol()
        print("[CONFIG OK] Supabase connection succeeded.")
        print(f"Bridge ID: {config.bridge_id}")
        print(f"Requested symbol: {symbol or 'not set yet'}")
        return 0
    except Exception as exc:
        print("[CONNECTION ERROR]")
        print(str(exc))
        print()
        print("Check that supabase_schema.sql was run and that the secret key is correct.")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
