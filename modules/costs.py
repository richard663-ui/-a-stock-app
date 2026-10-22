"""A-share execution costs for research evaluation (not a broker statement).

Historic research metrics used a flat 2 bp "execution hurdle". A real round
trip on an A-share costs, per side, commission (with a CNY 5 minimum) and the
transfer fee, plus stamp duty on the sell, plus crossing the bid/ask spread
when the edge is measured on mid prices. For a CNY 20,000 ticket that is about
10-11 bp in fees before the spread. ``configs/costs.json`` holds the
assumptions; set them to your broker's terms.

T+1 is not a cost but a constraint: a same-day round trip requires shares
already held from a previous day. Evaluations must state that assumption.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Iterable, List, Optional

CONFIG_PATH = Path(__file__).resolve().parents[1] / "configs" / "costs.json"


@dataclass(frozen=True)
class CostModel:
    commission_rate: float = 0.00025
    commission_min_cny: float = 5.0
    stamp_duty_sell_rate: float = 0.0005
    transfer_fee_rate: float = 0.00001
    reference_notional_cny: float = 20000.0
    extra_slippage_bp: float = 0.0
    version: str = "a-share-costs-v1-20261023"

    def side_fee_bp(self, side: str, notional: Optional[float] = None) -> float:
        notional = float(notional or self.reference_notional_cny)
        commission = max(self.commission_rate * notional, self.commission_min_cny)
        fee = commission + self.transfer_fee_rate * notional
        if side == "sell":
            fee += self.stamp_duty_sell_rate * notional
        return fee / notional * 1e4

    def round_trip_fees_bp(self, notional: Optional[float] = None) -> float:
        return self.side_fee_bp("buy", notional) + self.side_fee_bp("sell", notional) + self.extra_slippage_bp

    def round_trip_bp(self, spread_bp: float, include_spread: bool = True, notional: Optional[float] = None) -> float:
        """Fees plus, for mid-to-mid edges, one full spread (half on entry, half on exit)."""
        spread = max(0.0, float(spread_bp)) if include_spread else 0.0
        return self.round_trip_fees_bp(notional) + spread

    def costs_bp(self, spread_pct: Iterable[float], include_spread: bool = True) -> List[float]:
        # spread_pct is in percent units (0.03 == 3 bp), as stored by the recorders.
        out = []
        for value in spread_pct:
            try:
                bp = float(value) * 100.0
            except (TypeError, ValueError):
                bp = 0.0
            out.append(self.round_trip_bp(bp if bp == bp else 0.0, include_spread))
        return out


def load_cost_model(path: Path = CONFIG_PATH) -> CostModel:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return CostModel()
    allowed = {f.name for f in fields(CostModel)}
    return CostModel(**{k: v for k, v in raw.items() if k in allowed})
