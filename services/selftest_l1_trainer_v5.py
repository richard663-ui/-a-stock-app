# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import services.selftest_l1_trainer_v4 as synth
import services.train_l1_60s_model_v5r as t
import services.train_l1_60s_model_v5_challenger as v5


def main() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        start = datetime(2026, 9, 1)
        for j in range(5):
            synth._write_day(root, start + timedelta(days=j), j)

        old_v5_dir = v5.MODEL_DIR
        old_t_dir = t.MODEL_DIR
        try:
            new_dir = root / "models" / "l1_60s" / "v5_challenger"
            v5.MODEL_DIR = new_dir
            t.MODEL_DIR = new_dir
            rc = t.train("301236.SZ", 200, root, 2.0)
            assert rc == 0, f"V5R trainer rc={rc}"
            path = new_dir / "301236_SZ_training_report_latest.json"
            assert path.exists(), "V5 challenger report missing"
            obj = json.loads(path.read_text(encoding="utf-8"))
            assert obj.get("trainer_version") == t.TRAINER_VERSION
            assert obj.get("candidate_role") == "CHALLENGER_ONLY"
            assert obj.get("champion_reference")
            assert obj.get("history_days") == 5
            assert obj.get("validation_days") == 2
            assert obj.get("test_used_for_selection") is False
            assert obj.get("eligible_for_champion_promotion") is False
            assert set(obj.get("models", {})) == {"logistic_balanced", "hist_gradient_boosting"}
            assert obj["models"]["logistic_balanced"].get("candidate_role") == "PROMOTION_CANDIDATE"
            assert obj["models"]["hist_gradient_boosting"].get("candidate_role") == "CONTROL_ONLY"
            proxy_error = obj.get("execution_proxy_error")
            for item in obj.get("models", {}).values():
                assert "selected_probability_threshold" in item
                assert "heads" in item
                assert "up_entry_spread_adjusted_proxy" in item, f"proxy missing; error={proxy_error!r}"
        finally:
            v5.MODEL_DIR = old_v5_dir
            t.MODEL_DIR = old_t_dir
    print("PASS: L1 V5 robust challenger -> separate artifacts -> stability gating -> execution proxy")


if __name__ == "__main__":
    main()
