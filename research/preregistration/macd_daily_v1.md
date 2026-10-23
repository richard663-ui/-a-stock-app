# Pre-registration: MACD on daily bars, 5–20 day horizons (v1)

Written and committed **before** any result of this study was computed. Changes
after the first run are listed at the end with reasons; the holdout is evaluated
once.

## Question

Does MACD (standard 12/26/9) carry information about 5, 10 and 20-day forward
returns of A-shares, and does any of it survive after accounting for momentum,
reversal, volatility, VWAP position, volume, liquidity and market conditions, and
after costs?

## Data

- `~/AStockData/research_daily`: QMT daily bars of all currently listed SH/SZ
  A-shares from 2014 (front-ratio adjusted prices for returns and MACD, raw prices
  for VWAP and limit checks), plus delisted SH/SZ stocks from BaoStock
  (survivorship correction; not available from QMT).
- Missing bars are never filled. A stock is eligible on day t if: ≥ 120 bars of
  history, traded on t (not suspended, volume > 0), raw close ≥ 2 CNY, 20-day
  average amount ≥ 20m CNY, BaoStock ST flag false where known.
- Entry: open of t+1. If t+1 opens at the up-limit (open ≥ 0.995 × limit price) the
  entry is not possible and the observation is dropped. Limit = 10% main board,
  20% ChiNext from 2020-08-24 and STAR, 5% for known ST.
- Forward return R(h) = close(t+h) / open(t+1) − 1, h ∈ {5, 10, 20}. Excess return =
  R(h) minus the equal-weighted mean R(h) of all eligible stocks on the same t.
- Known limitations: no point-in-time ST flag for listed stocks; no market cap
  (20-day amount is the size/liquidity proxy); no industry classification history.

## Periods

- Warm-up: 2014. **Development: 2015-01-01 – 2021-12-31.** All exploration,
  selection and plots use only this period.
- **Holdout: 2022-01-01 – latest.** Evaluated once, after the development verdicts
  below are written into the results file.

## MACD definitions (fixed, never tuned)

EMA12, EMA26 of adjusted close; DIF = EMA12 − EMA26; DEA = EMA9(DIF);
HIST = DIF − DEA (the "2×" chart convention is irrelevant for signs and ranks).
Weekly MACD: same 12/26/9 on weekly closes of **completed** weeks only.

Events on day t (using data up to the close of t):

| ID | Event |
|---|---|
| GC_above / GC_below | golden cross (HIST turns > 0) with DIF > 0 / DIF ≤ 0 |
| DC_above / DC_below | death cross (HIST turns < 0) with DIF > 0 / DIF ≤ 0 |
| ZERO_up / ZERO_down | DIF crosses above / below 0 |
| H_red_exp, H_red_con, H_green_exp, H_green_con | HIST > 0 rising, > 0 falling, < 0 falling, < 0 rising (states) |
| DIV_bull | close is a 40-day closing low, the previous 40-day closing low is 10–40 days earlier, DIF now > DIF then, DIF < 0 |
| DIV_bear | close is a 40-day closing high, previous 40-day closing high 10–40 days earlier, DIF now < DIF then, DIF > 0 |
| GC_week_agree / GC_week_disagree | daily golden cross with weekly HIST > 0 / ≤ 0 |
| GC x context | golden cross split by: close above/below 20-day VWAP; volume ratio 5d/20d > 1.5 / < 1; within 5% of 60-day high / within 5% of 60-day low |
| PULLBACK_healthy / PULLBACK_broken | uptrend (close > MA60 and MA60 above its value 20 days ago) and 5-day return ≤ −3%, with DIF > 0 / DIF ≤ 0 |

Continuous features: DIF/close, HIST/close, ΔHIST/close (1 day), weekly HIST/close.

Controls: 5-day return, 20-day return, 60-day return excluding last 5 days, 20-day
volatility, 20-day max daily return, log 20-day amount, close / 20-day VWAP − 1,
volume ratio 5d/20d, distance to 60-day high.

## Tests

1. **Event study**: per event and horizon, mean excess return with standard errors
   clustered in time (daily event means, Newey-West lag = h), hit rate of positive
   excess, number of events and of distinct dates.
2. **Rank IC** of each continuous feature (daily cross-sections, Newey-West lag = h).
3. **Incremental value**: Fama-MacBeth regressions of cross-sectionally ranked
   excess returns on ranked MACD feature with and without the controls, on every
   5th trading day; Newey-West t of the MACD coefficient.
4. **Regimes** (development only for selection): market trend (equal-weight
   universe index above/below its 120-day mean), market volatility (20-day vol
   above/below its trailing 250-day median), calendar years.
5. **Redundancy**: average cross-sectional Spearman correlation of MACD features
   with the controls; the implied weights of DIF and HIST on past daily returns.
6. **Costs**: long signals are judged on mean excess return minus 30 bp round trip
   (sensitivity 20 and 50 bp).

## Decision rules

Development pass for an event: |NW t| ≥ 3.0, the same sign in ≥ 5 of 7 years, and
for long signals mean excess return > 30 bp at that horizon. For continuous
features: |NW t| ≥ 3.0 of the Fama-MacBeth coefficient **with controls**.

Holdout confirmation: same sign, NW t ≥ 2.0, and (long signals) net excess > 0.
About 60 event×horizon tests are run; a nominal t of 3 is used to limit false
discoveries.

MACD enters a production model only if it passes development and holdout and
adds net return in a walk-forward portfolio test. The verdict is research only;
no production weights change as part of this study.

## Changes after the first run

- Before the first run (no results seen): added a joint Fama-MacBeth regression
  of all indicators together, and each control alone. Descriptive only, for the
  redundancy question; it is not a pass/fail test.
- After the first development run (2026-10-23):
  1. **Bug fix.** The event verdict paired the event-weighted (pooled) mean with
     the t-statistic of the date-mean series; the two can differ in sign when
     events cluster on certain days. Verdicts now use the pre-registered estimator
     (mean of daily event means, NW t on that series). The pooled mean is reported
     with its own consistent t. Year signs use date means.
  2. **Post-hoc robustness (labelled as such).** The 1-day histogram change passed
     with controls. By construction it is mostly the last 1–2 daily returns, which
     are not among the controls, so its coefficient is also reported with the
     1-day return added. This can only weaken, never strengthen, the MACD result.
  3. Hit rates are shown next to the hit rate of all eligible stocks (about 43%
     because returns are right-skewed), not next to 50%.
