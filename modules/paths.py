"""Stable locations for state that must survive pinned releases.

Code runs from ``<install root>/current`` (a junction to ``releases/<commit>``),
so nothing stateful may live next to the code. The watchdogs export
``ASTOCK_INSTALL_ROOT`` and ``ASTOCK_RUNTIME_DIR``; developer checkouts fall
back to the old repository-relative ``runtime`` folder.
"""
from __future__ import annotations

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def install_root() -> Path | None:
    value = os.environ.get("ASTOCK_INSTALL_ROOT", "").strip()
    return Path(value).expanduser() if value else None


def runtime_dir() -> Path:
    value = os.environ.get("ASTOCK_RUNTIME_DIR", "").strip()
    if value:
        path = Path(value).expanduser()
    elif install_root() is not None:
        path = install_root() / "runtime"
    else:
        path = PROJECT_ROOT / "runtime"
    path.mkdir(parents=True, exist_ok=True)
    return path


def data_root() -> Path:
    return Path(os.environ.get("ASTOCK_RESEARCH_DATA_DIR", str(Path.home() / "AStockData"))).expanduser()


def config_dir() -> Path:
    """User-private settings and secrets; never inside the repository."""
    return Path(os.environ.get("ASTOCK_CONFIG_DIR", str(Path.home() / ".a_stock_qmt"))).expanduser()
