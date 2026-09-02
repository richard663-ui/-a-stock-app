# -*- coding: utf-8 -*-
"""V5B launcher: V4b recorder integrity + strict 60s regime safety gate + MACD context."""
from __future__ import annotations

import services.qmt_research_recorder_v4b as v4b
import modules.research_forward_model_v5 as model
from modules.macd_calibration_v5 import get_context

base = v4b.base
RECORDER_VERSION = "research-recorder-v5b-20260902"
PRODUCTION_MODEL = "mobile-v10-regime-safety-60s"

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
    print("V5B active: V4b direction + symbol normalization + strict regime safety gate")
    print("60s remains primary. Only 4/4 factor alignment + confidence>=65 + abnormality 0.50-1.50 may emit direction.")
    print("Everything else becomes WATCH. Signals are NOT inverted. MACD is confidence context only.")
    base.main()


if __name__ == "__main__":
    main()
