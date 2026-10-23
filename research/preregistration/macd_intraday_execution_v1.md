# Pre-registration: intraday MACD for entry/exit timing (v1)

Committed before any result was computed. Question: when a stock will be bought
(or sold) today anyway, does waiting for a 5-minute MACD signal get a better price
than simple schedules? This is execution value for 5–20 day holdings, not a
prediction of the stock's direction.

## Data

- QMT 5-minute bars (the broker keeps one year: 2025-10-22 to 2026-10-23), times
  converted from epoch to Shanghai time. No filling of missing bars.
- Sample: 400 stocks drawn with seed 20261024 from SH/SZ A-shares with 20-day
  average amount ≥ 50m CNY and close ≥ 2 CNY **as of 2025-10-21** (no look-ahead),
  names without "ST".
- A stock-day is used only if it has all 48 continuous-session bars, volume > 0,
  and did not open at the up- or down-limit (fills would be impossible).

## Rules compared (buy side; the sell side mirrors them)

MACD 12/26/9 on continuous 5-minute closes across days (as on a chart). Signals
are evaluated at bar closes from 09:45 to 14:50; a rule with no signal fills at the
close of the 14:55 bar.

| Rule | Buy fill | Sell fill (mirror) |
|---|---|---|
| OPEN | day open | day open |
| T1000 | close of the 10:00 bar | same |
| M_GC / M_DC | first 5m golden cross | first 5m death cross |
| M_GC_below / M_DC_above | first golden cross with DIF < 0 | first death cross with DIF > 0 |
| M_TURN | first bar with HIST < 0 and rising | first bar with HIST > 0 and falling |

## Measurement

- Cost versus the day's VWAP (Σamount / Σvolume), in bp, sign chosen so that
  **positive = worse than VWAP** for both buys and sells.
- Time-of-day control: for a MACD fill at bar b, subtract the average cost of
  filling at bar b on all eligible stock-days in the same period. This separates
  "MACD timing" from "afternoon prices differ from morning prices".
- Standard errors: daily cross-sectional means, Newey-West lag 1.

## Periods

Development 2025-10-23 – 2026-04-30; holdout 2026-05-01 – 2026-10-23, evaluated once.

## Decision rule

A MACD rule is useful if in development it beats VWAP **and** the time-of-day
control by ≥ 3 bp with |t| ≥ 3, and in the holdout keeps the sign with |t| ≥ 2.
Fewer than 3 bp is too small to matter after 0.1–0.3% round-trip costs.

## Changes after the first run

- 5-minute bars are dated from the epoch `time` field and were never affected by
  the daily-store dating bug. The 400-stock liquidity screen used that mis-dated
  daily store (dates one session early); for a 20-day average-amount screen this is
  immaterial, and redrawing the sample after seeing results would be worse, so the
  sample is kept.
