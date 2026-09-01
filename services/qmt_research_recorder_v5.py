# -*- coding: utf-8 -*-
"""V5A launcher: V4b recorder integrity + adaptive 60s gate + rich minute-K MACD."""
from __future__ import annotations

import services.qmt_research_recorder_v4b as v4b
import modules.research_forward_model_v5 as model
from modules.macd_calibration_v5 import get_context

base = v4b.base
RECORDER_VERSION = "research-recorder-v5a-20260901"
PRODUCTION_MODEL = "mobile-v9-adaptive-normalized-selective-60s"

base.RECORDER_VERSION = RECORDER_VERSION
base.MODEL_VERSION = model.MODEL_VERSION
base.PRODUCTION_MODEL = PRODUCTION_MODEL
base.score_rows = model.score_rows
base.score_label = model.score_label
base.high_confidence = model.high_confidence


def _macd_context(self, symbol: str):
    try:
        return get_context(symbol)
    except Exception:
        return {}

base.CloudState.macd_bias = _macd_context


def main():
    print("V5A active: V4b direction + symbol-local normalization + selective 60s gate + MACD confidence")
    print("60s remains the primary target. Weak/conflicted candidates become WATCH instead of forced UP/DOWN.")
    print("MACD is confidence context only; it is NOT a hard gate and does NOT decide direction.")
    base.main()


if __name__ == "__main__":
    main()
