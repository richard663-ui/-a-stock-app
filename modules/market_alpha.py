"""Separate daily cross-sectional research; never changes live/mobile scores."""
from __future__ import annotations

import re
from pathlib import Path
import numpy as np
import pandas as pd

PRICE_COLUMNS = ["date", "stock_code", "open", "high", "low", "close", "volume", "amount"]


def atomic_csv(frame, path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    frame.to_csv(temporary, index=False, encoding="utf-8-sig")
    temporary.replace(path)


def normalize_daily(frame, symbol, as_of):
    if frame is None or frame.empty:
        raise ValueError("QMT daily history is empty")
    result = frame.copy()
    if "time" in result:
        times = pd.to_numeric(result["time"], errors="coerce")
        result["date"] = pd.to_datetime(times, unit="ms", utc=True, errors="coerce").dt.tz_convert(
            "Asia/Shanghai").dt.tz_localize(None).dt.normalize()
    else:
        result["date"] = pd.to_datetime(result.index.astype(str), errors="coerce").normalize()
    required = ["open", "high", "low", "close", "volume", "amount"]
    if not set(required).issubset(result):
        raise ValueError("Missing required OHLCV columns")
    for column in required:
        result[column] = pd.to_numeric(result[column], errors="coerce")
    if result["date"].isna().any() or result[required].isna().any().any():
        raise ValueError("Invalid daily dates or numeric fields")
    if not np.isfinite(result[required].to_numpy(dtype=float)).all():
        raise ValueError("Non-finite daily prices or amounts")
    if (result[["open", "high", "low", "close"]] <= 0).any().any():
        raise ValueError("Non-positive daily price")
    if (result[["volume", "amount"]] < 0).any().any():
        raise ValueError("Negative volume or amount")
    if ((result["high"] < result[["open", "close", "low"]].max(axis=1))
            | (result["low"] > result[["open", "close", "high"]].min(axis=1))).any():
        raise ValueError("Inconsistent daily OHLC")
    result["stock_code"] = symbol
    result = result.loc[result["date"] <= pd.Timestamp(as_of), PRICE_COLUMNS].reset_index(drop=True)
    result = result.drop_duplicates(["stock_code", "date"], keep="last").sort_values("date")
    if result.empty:
        raise ValueError("No completed daily rows at or before the research date")
    result["date"] = result["date"].dt.strftime("%Y-%m-%d")
    return result.reset_index(drop=True)


def normalize_universe(symbols):
    return sorted({str(code).upper() for code in symbols
                   if re.fullmatch(r"\d{6}\.(SH|SZ|BJ)", str(code).upper())})


def daily_features(prices, calendar):
    data = prices.copy()
    data["date"] = pd.to_datetime(data["date"])
    data = data.set_index("date").reindex(pd.DatetimeIndex(calendar)).rename_axis("date")
    data["stock_code"] = prices["stock_code"].iloc[0]
    close = pd.to_numeric(data["close"], errors="coerce")
    returns = close.pct_change(fill_method=None)
    data["momentum_20d"] = close / close.shift(20) - 1
    data["momentum_60d"] = close / close.shift(60) - 1
    data["volatility_20d"] = returns.rolling(20, min_periods=20).std()
    data["amount_20d"] = data["amount"].rolling(20, min_periods=20).mean()
    data["history_rows"] = close.notna().cumsum()
    data["tradable_proxy"] = close.notna() & (data["volume"] > 0) & (data["amount"] > 0)
    return data.reset_index()


def attach_forward_labels(features, benchmark, horizons, cost_bp):
    # Index on benchmark sessions, not each stock's potentially gapped row count.
    data = features.copy().set_index("date")
    bench = benchmark.copy()
    bench["date"] = pd.to_datetime(bench["date"])
    bench = bench.set_index("date").reindex(data.index)
    entry = data["open"].shift(-1)
    entry_available = data["tradable_proxy"].shift(-1, fill_value=False) & (entry > 0)
    bench_entry = bench["open"].shift(-1)
    for horizon in horizons:
        exit_available = data["tradable_proxy"].shift(-horizon, fill_value=False)
        future = data["close"].shift(-horizon) / entry - 1
        reference = bench["close"].shift(-horizon) / bench_entry - 1
        available = entry_available & exit_available & (bench_entry > 0) & reference.notna()
        data[f"future_return_{horizon}d"] = future.where(available)
        data[f"future_benchmark_return_{horizon}d"] = reference.where(available)
        data[f"future_net_excess_{horizon}d"] = (future - reference - cost_bp / 10000).where(available)
        data[f"has_future_{horizon}d"] = available
    return data.reset_index()


def rank_baseline(features, config):
    data = features.copy()
    required = ["momentum_20d", "momentum_60d", "volatility_20d", "amount_20d"]
    data["research_eligible"] = (data["tradable_proxy"]
        & (data["history_rows"] >= config["minimum_history"])
        & (data["amount_20d"] >= config["minimum_amount_20d"])
        & data[required].notna().all(axis=1))
    data["baseline_rank_score"] = np.nan
    selected = data.loc[data["research_eligible"]]
    groups = selected.groupby("date", sort=False)
    scores = {
        "momentum_20d": groups["momentum_20d"].rank(pct=True),
        "momentum_60d": groups["momentum_60d"].rank(pct=True),
        "amount_20d": groups["amount_20d"].rank(pct=True),
        "low_volatility_20d": groups["volatility_20d"].rank(pct=True, ascending=False),
    }
    data.loc[selected.index, "baseline_rank_score"] = sum(
        config["baseline_weights"][name] * scores[name] for name in scores) * 100
    data["research_version"] = config["version"]
    data["active_for_signals"] = False
    return data


def summarize_rank_ic(data, horizons, minimum_cross_section=30):
    rows = []
    for horizon in horizons:
        target = f"future_net_excess_{horizon}d"
        for date, group in data.groupby("date", sort=True):
            usable = group.loc[group["research_eligible"], ["baseline_rank_score", target]].dropna()
            enough = len(usable) >= minimum_cross_section
            ic = (usable["baseline_rank_score"].rank().corr(usable[target].rank())
                  if enough and usable["baseline_rank_score"].nunique() > 1
                  and usable[target].nunique() > 1 else np.nan)
            rows.append({"date": date, "horizon": horizon, "evaluated_stocks": len(usable),
                         "rank_ic": ic, "status": "DESCRIPTIVE_ONLY" if enough else "INSUFFICIENT_DATA",
                         "point_in_time_universe": False, "eligible_for_live_deployment": False})
    return pd.DataFrame(rows, columns=["date", "horizon", "evaluated_stocks", "rank_ic", "status",
                                      "point_in_time_universe", "eligible_for_live_deployment"])
