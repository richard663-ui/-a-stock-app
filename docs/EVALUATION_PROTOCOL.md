# Evaluation protocol

This project separates **signal confidence** from **validated accuracy**.

## 1-minute / 2-minute direction labels

Use the current tradable quote, not the last traded price, as the reference.

- UP: future mid-price clears the current ask plus a noise/cost buffer.
- DOWN: future mid-price clears the current bid minus a noise/cost buffer.
- WATCH: neither barrier is reached.

Primary horizons: 60s and 120s.

### Required evaluation

1. Walk-forward split by trading day. Never random-shuffle ticks across days.
2. No look-ahead: every feature must exist before the signal timestamp.
3. Benchmark against simple baselines: last-30s momentum, order-book imbalance only, and always-WATCH.
4. Report precision separately for UP and DOWN, signal coverage, false-alert rate, average future return, MAE/MFE, and results after spread/slippage.
5. Break results down by morning open, normal continuous trading, lunch-reopen, final hour, high/low volatility, and market regime.
6. A displayed 90% condition agreement is **not** a 90% historical hit rate.

A production signal should only be promoted after enough out-of-sample events exist and the edge remains positive after costs.

## Daily MACD event study

Standard MACD: EMA12, EMA26, signal EMA9.

- Water-under golden cross: DIF and DEA are both below zero on the cross day.
- Water-above golden cross: DIF and DEA are both above zero on the cross day.
- Zero-axis/mixed: all other golden crosses.

To avoid look-ahead, a confirmed daily cross is entered at the **next trading day's open**, not the cross-day close.

For each event record:

- 1/2/3/5-day return from next-day open
- 1/2/3 consecutive positive close probabilities
- 3-day maximum adverse excursion and maximum favourable excursion
- event counts, mean/median return and win rate by cross zone

The separate `pre-cross` research setup measures whether a narrowing negative MACD gap predicts a next-day cross. It is not treated as a tradable previous-day-close rule because it uses completed daily data. A genuine previous-day entry must use an intraday pre-close snapshot, e.g. 14:50 QMT data, to avoid look-ahead bias.
