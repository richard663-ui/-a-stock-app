# -*- coding: utf-8 -*-
"""Find an existing local secrets.toml and persist it outside downloaded ZIP folders."""
from __future__ import annotations

import shutil
from pathlib import Path

try:
    import tomllib
except Exception:
    tomllib = None

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PROJECT_SECRET = PROJECT_ROOT / ".streamlit" / "secrets.toml"
PERSISTENT_DIR = Path.home() / ".a_stock_qmt"
PERSISTENT_SECRET = PERSISTENT_DIR / "secrets.toml"


def _valid(path: Path) -> bool:
    if tomllib is None or not path.exists():
        return False
    try:
        with path.open("rb") as f:
            data = tomllib.load(f)
    except Exception:
        return False
    url = str(data.get("SUPABASE_URL", ""))
    key = str(data.get("SUPABASE_SERVICE_KEY", ""))
    bridge = str(data.get("BRIDGE_ID", ""))
    return (
        url.startswith("https://")
        and "supabase.co" in url
        and (key.startswith("sb_secret_") or key.startswith("eyJ"))
        and bool(bridge)
    )


def _candidates():
    seen = set()
    roots = [
        PROJECT_ROOT.parent,
        Path.home() / "Desktop",
        Path.home() / "OneDrive" / "Desktop",
    ]
    for root in roots:
        if not root.exists():
            continue
        try:
            for path in root.glob("**/.streamlit/secrets.toml"):
                key = str(path.resolve()).lower()
                if key in seen or path.resolve() == PROJECT_SECRET.resolve():
                    continue
                seen.add(key)
                yield path
        except Exception:
            continue


def main() -> int:
    PERSISTENT_DIR.mkdir(parents=True, exist_ok=True)

    if _valid(PERSISTENT_SECRET):
        print(f"[CONFIG FOUND] {PERSISTENT_SECRET}")
        return 0

    if _valid(PROJECT_SECRET):
        shutil.copy2(PROJECT_SECRET, PERSISTENT_SECRET)
        print(f"[CONFIG SAVED] Persistent config created at {PERSISTENT_SECRET}")
        return 0

    for candidate in _candidates():
        if _valid(candidate):
            shutil.copy2(candidate, PERSISTENT_SECRET)
            print(f"[CONFIG RECOVERED] Copied existing config from {candidate}")
            print(f"[CONFIG SAVED] {PERSISTENT_SECRET}")
            return 0

    example = PROJECT_ROOT / ".streamlit" / "secrets.toml.example"
    if example.exists() and not PERSISTENT_SECRET.exists():
        shutil.copy2(example, PERSISTENT_SECRET)

    print("[CONFIG ERROR] No valid Supabase secret configuration was found.")
    print(f"Open this file and fill in the real values once: {PERSISTENT_SECRET}")
    print("After that, every newly downloaded project folder will reuse it automatically.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
