# -*- coding: utf-8 -*-
"""L1 ML auto-trainer V2: use the duplicate-safe L1 trainer."""
from pathlib import Path
import services.l1_ml_autotrain_daemon_v1 as base

AUTO_TRAINER_VERSION = "l1-ml-autotrain-v2-dedup-20260904"
base.AUTO_TRAINER_VERSION = AUTO_TRAINER_VERSION
base.TRAINER = Path(__file__).with_name("train_l1_60s_model_v2.py")


def main() -> None:
    print("AStock L1/Tick 60s ML auto-trainer V2 started")
    print(f"Daemon: {AUTO_TRAINER_VERSION}")
    print("Duplicate feature columns are collapsed before training. No live deployment.")
    base.main()


if __name__ == "__main__":
    main()
