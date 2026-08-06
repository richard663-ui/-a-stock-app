# -*- coding: utf-8 -*-
"""Find a valid local secrets.toml and persist it outside downloaded ZIP folders."""
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
    url = str(data.get("SUPABASE_URL", "")).strip()
    key = str(data.get("SUPABASE_SERVICE_KEY", "")).strip()
    bridge = str(data.get("BRIDGE_ID", "")).strip()
    return (
        url.startswith("https://")
        and "supabase.co" in url
        and (key.startswith("sb_secret_") or key.startswith("eyJ"))
        and bool(bridge)
    )


def _copy(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)


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
                resolved = path.resolve()
                key = str(resolved).lower()
                if key in seen or resolved == PROJECT_SECRET.resolve():
                    continue
                seen.add(key)
                yield path
        except Exception:
            continue


def main() -> int:
    PERSISTENT_DIR.mkdir(parents=True, exist_ok=True)

    if _valid(PERSISTENT_SECRET):
        if not _valid(PROJECT_SECRET):
            _copy(PERSISTENT_SECRET, PROJECT_SECRET)
            print(f"[CONFIG RESTORED] {PROJECT_SECRET}")
        print(f"[CONFIG FOUND] {PERSISTENT_SECRET}")
        return 0

    if _valid(PROJECT_SECRET):
        _copy(PROJECT_SECRET, PERSISTENT_SECRET)
        print(f"[CONFIG SAVED] {PERSISTENT_SECRET}")
        return 0

    for candidate in _candidates():
        if _valid(candidate):
            _copy(candidate, PROJECT_SECRET)
            _copy(candidate, PERSISTENT_SECRET)
            print(f"[CONFIG RECOVERED] {candidate}")
            print(f"[CONFIG SAVED] {PERSISTENT_SECRET}")
            return 0

    print("[CONFIG NOT SET]")
    print("The one-time setup wizard will open next.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
