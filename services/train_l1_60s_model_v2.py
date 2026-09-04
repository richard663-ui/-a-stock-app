# -*- coding: utf-8 -*-
"""L1 60s trainer V2: duplicate-column safe wrapper for V1.

V1 correctly reuses the mature L2 training machinery, but pandas expansion can
retain both table columns and features_json columns with the same name (for
example spread_pct). That makes one-dimensional numeric conversion fail. V2
collapses duplicate names deterministically before V1 feature engineering.
"""
from __future__ import annotations

from pathlib import Path
import argparse

import services.train_l1_60s_model_v1 as v1

TRAINER_VERSION = "l1-60s-trainer-v2-dedup-columns-20260904"
_original_expand = v1._original_expand


def _dedup_expand(frame):
    out = _original_expand(frame)
    if not out.empty and out.columns.duplicated().any():
        # features_json is concatenated after table columns by the base expander;
        # keeping the last duplicate preserves the richer sampled feature value.
        out = out.loc[:, ~out.columns.duplicated(keep="last")].copy()
    return out


v1._original_expand = _dedup_expand
v1.TRAINER_VERSION = TRAINER_VERSION


def train(symbol: str, min_samples: int, data_root: Path, hurdle_bp: float) -> int:
    return int(v1.train(symbol, min_samples, data_root, hurdle_bp))


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--symbol", default=v1.base.PRIMARY_SYMBOL)
    p.add_argument("--min-samples", type=int, default=200)
    p.add_argument("--data-root", default=str(v1.base.DATA_ROOT))
    p.add_argument("--hurdle-bp", type=float, default=v1.v4.DEFAULT_HURDLE_BP)
    a = p.parse_args()
    raise SystemExit(train(a.symbol, a.min_samples, Path(a.data_root).expanduser(), a.hurdle_bp))


if __name__ == "__main__":
    main()
