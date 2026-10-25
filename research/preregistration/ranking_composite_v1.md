# Pre-registration: replacement research ranking (composite v1)

Committed before any portfolio result of this study was computed. Research only:
the production market-alpha configuration and every live signal stay unchanged
whatever the result. Changes after the first run are logged at the end.

## Question

Does a simple equal-weight composite of the price/volume effects that carried
independent information in the MACD study give a better long-only 1–4 week
ranking than (a) the current momentum-weighted baseline, (b) the equal-weight
universe, (c) each of its components alone, after realistic costs?

## Honest status of the data

The components and their signs were chosen from the joint regression in the MACD
study, which used **both** 2015–2021 and 2022–2026. So 2022–2026 is *not* a clean
holdout for the choice of components: it is reported as a "seen period"
consistency check. Weights are fixed (equal), so nothing else is fitted. The only
clean out-of-sample test is the **forward paper track** from 2026-10-26, logged
weekly before outcomes are known.

## Data

`~/AStockData/research_daily` (QMT daily bars 2014-, epoch-dated, plus BaoStock
delisted stocks) through the feature files of the MACD study. Eligibility as in the
MACD study (≥ 120 bars, traded, close ≥ 2 CNY, not known ST, next open not at the
up-limit), plus a capacity filter.

## Rankings (all cross-sectional percentile ranks, higher = buy)

| Name | Definition |
|---|---|
| **C1 (primary)** | mean rank of: low abnormal volume (−vol 5d/20d), small/illiquid (−log 20-day amount), low volatility (−20-day vol), near 60-day high (+close / 60-day high), weak 20-day return (−20-day return) |
| C2 | mean rank of: −abnormal volume, −log amount, −volatility (the three significant alone and jointly) |
| C1_nosize | C1 without the size/liquidity term (capacity and survivorship check) |
| B0 | current baseline: 0.35 rank(20-day return) + 0.35 rank(60-day return) + 0.15 rank(20-day amount) + 0.15 rank(low volatility) |
| S_* | each C1 component alone |
| EW | equal-weight eligible universe (benchmark) |

## Portfolio test

- **Primary specification:** universe with 20-day average amount ≥ 50m CNY
  ("investable"), top 30 names, equal weight, rebalanced every 5 trading days
  from a fixed anchor, entry at the next open, return to the close 5 sessions later
  (last available close if suspended or delisted).
- Secondary: rebalance every 20 days; top decile; universe ≥ 20m CNY.
- Costs: 30 bp round trip per replaced name (turnover × 30 bp per rebalance);
  gross figures also reported.
- Benchmarks: EW of the same universe (primary), CSI 1000 index.
- Metrics: annualised net excess return, Newey-West t of period excess returns,
  information ratio, maximum drawdown (absolute and excess), turnover, years with
  positive excess, results by market trend and volatility regime, median 20-day
  amount of holdings; rank IC (5 and 20 days) of every ranking.

## Periods

Development 2015-01-01 – 2021-12-31. Seen period 2022-01-01 – latest.
Forward paper track from 2026-10-26.

## Decision rules

C1 is called **promising** if, in development, the primary specification has net
annualised excess > 0 with Newey-West t ≥ 2.5, a higher information ratio than
B0, and positive excess in ≥ 5 of 7 years; and in the seen period net excess > 0
with t ≥ 2.0.

"Promising" means only: start the forward paper track and report it after 60
trading days. Switching the research ranking needs your approval; any live use
needs a separate decision. If C1 fails, the report says which component carried
what, and no replacement is proposed.

## Known limitations

No industry classification or market cap (size proxied by trading amount); no
point-in-time ST flag for listed stocks; limit-down exits and queue position not
modelled; close-to-open overnight gap between consecutive holding periods not
compounded (same for all rankings); equal-weight benchmarks tilt small.

## Changes after the first run

(none yet)
