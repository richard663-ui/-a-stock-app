# -*- coding: utf-8 -*-
from __future__ import annotations

import numpy as np
import pandas as pd

import services.v4r_cross_sectional_rank_audit_v1 as xr


def main() -> int:
    assert xr.POLICIES == ("EXTREMES_1", "UP_1", "DOWN_1", "EXTREMES_2")
    rows = []
    for bucket in (100, 101):
        for i in range(8):
            rows.append({"symbol": f"S{i}", "generated_ts": bucket*60 + 1})
    frame = pd.DataFrame(rows)
    score = np.tile(np.arange(8, dtype=float), 2)
    p = xr._rank_pred(frame, score, "EXTREMES_1")
    assert int((p == 1).sum()) == 2, p
    assert int((p == -1).sum()) == 2, p
    assert int((p != 0).sum()) == 4, p
    p2 = xr._rank_pred(frame, score, "EXTREMES_2")
    assert int((p2 == 1).sum()) == 4, p2
    assert int((p2 == -1).sum()) == 4, p2
    # Ranking policy must not inspect future/label columns.
    frame["ret_future_should_not_matter"] = np.arange(len(frame))
    p3 = xr._rank_pred(frame, score, "EXTREMES_1")
    assert np.array_equal(p, p3)
    print("v4r_cross_sectional_rank_audit_v1 selftest PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
