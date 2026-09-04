# -*- coding: utf-8 -*-
"""V4R launcher: asymmetric V4 with rotating-phase 15s train thinning.

One observation is retained from each symbol/15s time bucket, but the selected
5s position rotates deterministically with the time bucket. Selection depends
only on timestamps, never targets, so it avoids fixed-phase aliasing without
introducing label leakage.
"""
from __future__ import annotations

import argparse
from pathlib import Path
from typing import List

import pandas as pd

import services.train_l1_60s_model_v4 as core

TRAINER_VERSION = "l1-60s-trainer-v4r-asymmetric-rotating-thin-20260904"
DATA_ROOT = core.DATA_ROOT
MODEL_DIR = core.MODEL_DIR


def _rotating_thin(frame: pd.DataFrame, seconds: float) -> pd.DataFrame:
    if frame.empty:
        return frame.copy()
    work = frame.sort_values(["symbol", "generated_ts"]).copy()
    ts = pd.to_numeric(work["generated_ts"], errors="coerce")
    work = work[ts.notna()].copy()
    work["_thin_bucket"] = (pd.to_numeric(work["generated_ts"], errors="coerce") // float(seconds)).astype("int64")
    pieces: List[pd.DataFrame] = []
    for (_, bucket), part in work.groupby(["symbol", "_thin_bucket"], sort=False, dropna=False):
        part = part.sort_values("generated_ts")
        pos = int(abs(int(bucket))) % len(part)
        pieces.append(part.iloc[[pos]])
    if not pieces:
        return frame.iloc[0:0].copy()
    out = pd.concat(pieces, ignore_index=False).drop(columns=["_thin_bucket"], errors="ignore")
    return out.sort_values("generated_ts").copy()


def train(symbol: str, min_samples: int, data_root: Path, hurdle_bp: float) -> int:
    core.TRAINER_VERSION = TRAINER_VERSION
    core.MODEL_DIR = MODEL_DIR
    core._thin = _rotating_thin
    return int(core.train(symbol, min_samples, data_root, hurdle_bp))


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--symbol", default="301236.SZ")
    p.add_argument("--min-samples", type=int, default=200)
    p.add_argument("--data-root", default=str(DATA_ROOT))
    p.add_argument("--hurdle-bp", type=float, default=2.0)
    a = p.parse_args()
    raise SystemExit(train(a.symbol, a.min_samples, Path(a.data_root).expanduser(), a.hurdle_bp))


if __name__ == "__main__":
    main()
