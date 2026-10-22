"""Compute which Python modules each production entry point actually loads.

Static import graph (``import services.x``, ``from modules.y import z``) starting
from the services in deploy/services.json, the Streamlit app and the batch tools.
``--check`` fails when a module under modules/ or services/ is missing from
docs/COMPONENTS.md, so the registry cannot silently go stale.
"""
from __future__ import annotations

import argparse
import ast
import json
import sys
from pathlib import Path
from typing import Dict, Set

ROOT = Path(__file__).resolve().parents[1]
BATCH_ENTRIES = ["services/market_alpha_data.py", "services/market_alpha_research.py",
                 "services/collection_report.py", "services/qmt_tick_backfill.py", "services/monitor_health.py"]


def module_path(name: str) -> Path | None:
    parts = name.split(".")
    if parts[0] not in ("modules", "services"):
        return None
    path = ROOT.joinpath(*parts).with_suffix(".py")
    return path if path.exists() else None


def imports_of(path: Path) -> Set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: Set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
            if node.module in ("modules", "services"):
                found.update(f"{node.module}.{a.name}" for a in node.names)
    return {m for m in found if m.split(".")[0] in ("modules", "services")}


def closure(entry: Path) -> Set[str]:
    seen: Set[str] = set()
    stack = [entry]
    while stack:
        path = stack.pop()
        rel = path.relative_to(ROOT).with_suffix("").as_posix().replace("/", ".")
        if rel in seen:
            continue
        seen.add(rel)
        for name in imports_of(path):
            target = module_path(name)
            if target is not None:
                stack.append(target)
    return seen


def entry_points() -> Dict[str, Path]:
    registry = json.loads((ROOT / "deploy" / "services.json").read_text(encoding="utf-8"))
    entries = {f"service:{s['id']}": ROOT / s["script"] for s in registry["services"]}
    entries["streamlit:app_1"] = ROOT / "app_v18_live.py"
    entries.update({f"batch:{Path(p).stem}": ROOT / p for p in BATCH_ENTRIES})
    return entries


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    used: Dict[str, Set[str]] = {}
    for label, path in entry_points().items():
        for module in closure(path):
            used.setdefault(module, set()).add(label)
    all_modules = sorted(p.relative_to(ROOT).with_suffix("").as_posix().replace("/", ".")
                         for folder in ("modules", "services") for p in (ROOT / folder).glob("*.py"))
    if args.check:
        registry_text = (ROOT / "docs" / "COMPONENTS.md").read_text(encoding="utf-8")
        missing = [m for m in all_modules if f"`{m.split('.')[-1]}`" not in registry_text]
        for m in missing:
            print(f"[REGISTRY] {m} is not listed in docs/COMPONENTS.md")
        return 1 if missing else 0
    for m in all_modules:
        print(f"{m:55s} {', '.join(sorted(used.get(m, []))) or '-'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
