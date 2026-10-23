# MACD as a research indicator: findings (2026-10-24)

Pre-registrations: `research/preregistration/macd_daily_v1.md`,
`macd_intraday_execution_v1.md` (including every change made after the first run).
Machine-readable results: `macd_daily_dev.json`, `macd_daily_holdout.json`,
`macd_intraday_execution_{dev,holdout}.json`, `macd_old_study_check.json`.
Code: `research/tools/macd_daily_study.py`, `macd_intraday_execution.py`,
`build_research_daily.py`, `macd_old_study_check.py`.

## Data and method in one paragraph

All SH/SZ A-shares 2014–2026 from QMT (5,187 listed stocks) plus 252 delisted
stocks from BaoStock, 12.4 million stock-days. Eligible: ≥120 bars, traded, price
≥ 2 CNY, 20-day amount ≥ 20m CNY, not ST where known; entry at the next open,
skipped if that open is at the up-limit. Returns are excess over the equal-weighted
eligible universe on the same day. MACD 12/26/9 only, never tuned. Development
2015–2021, holdout 2022–Oct 2026. Event means are event-weighted with standard
errors clustered by date (Newey-West, lag = horizon). Costs: 30 bp round trip.
Controls: 5-day return, 20-day return, 60-day-ex-5 return, 20-day volatility, 20-day
max return, log 20-day amount, distance to 20-day VWAP, 5/20-day volume ratio,
distance to 60-day high.

## Answers

**Does MACD add predictive value after momentum, volatility, VWAP, volume and
market conditions? No robust value at 5–20 days.**

| Question | Development 2015–21 | Holdout 2022–26 | Verdict |
|---|---|---|---|
| Golden cross above zero, 20 d excess | −27 bp (t −1.4) | −64 bp (t −3.0) | No edge; recently negative |
| Golden cross below zero, 20 d | +19 bp (t 1.5) | +15 bp (t 0.7) | No edge after 30 bp cost |
| DIF crosses above zero, 20 d | −6 bp (t −0.6) | −49 bp (t −2.6) | No edge; recently negative |
| Daily cross with weekly histogram agreeing, 20 d | −26 bp (t −1.9) | −56 bp (t −2.9) | Agreement does not help |
| Golden cross above 20-day VWAP, 20 d | −20 bp (t −1.6) | −62 bp (t −3.8) | Negative |
| Golden cross below 20-day VWAP, 20 d | +30 bp (t 1.9) | +54 bp (t 1.8) | Positive but not significant; this is reversal |
| **Golden cross on a volume surge (5d/20d > 1.5), 20 d** | **−123 bp (t −3.9), 7/7 years** | **−114 bp (t −2.5), 5/5 years** | **Replicated warning signal** |
| Golden cross on low volume, 20 d | +12 bp (t 1.6) | +8 bp (t 0.6) | No edge |
| Golden cross near 60-day high, 20 d | −6 bp | −61 bp (t −2.8) | Negative recently |
| Bullish divergence, 5 d | −34 bp (t −3.3) | +23 bp (t 1.95) | Sign flips: unreliable |
| Bearish divergence, 20 d | +3 bp | −72 bp (t −1.8) | Not significant |
| Death cross below zero, 5 d | −25 bp (t −3.3) | +5 bp (t 0.6) | Did not replicate |
| Red histogram shrinking, 5 d | +13 bp (t 4.8) | +5 bp (t 1.3) | Did not replicate |
| Uptrend pullback: DIF > 0 ("healthy") vs DIF ≤ 0, 20 d | −10 vs −32 bp | −19 vs +7 bp | MACD does not separate healthy pullbacks from deterioration |

Continuous MACD values:

| Feature | Alone (t, dev / holdout) | With the 9 controls | Reading |
|---|---|---|---|
| DIF / price | −5.8 / −4.9 | −0.1 / −2.0 | Rank correlation 0.84 with 20-day momentum; duplicates it |
| Histogram / price | −4.4 / −2.8 | +1.0 / +0.4 | Duplicates 5-day return and VWAP distance |
| 1-day histogram change | −1.5 / −0.1 | −4.5 / −1.5 | Dev-only; did not replicate |
| Weekly histogram | −5.1 / −3.5 | −3.3 / −2.0 | Weakened to borderline in holdout |

Every MACD level has a *negative* sign on its own: stronger MACD readings are
followed by weaker 5–20 day relative returns. This is the A-share short-term
reversal and momentum-crash pattern. MACD measures it but adds nothing new.

**Why.** MACD is a fixed linear filter of past prices. DIF weights the last 2–3
months of daily returns, heaviest about 8 days back. The histogram gives positive
weight to the last ~13 days and negative weight to older days (computed exactly in
`return_kernel()`). So MACD signals restate "the stock has risen recently /
accelerated", which in A-shares predicts lower, not higher, relative returns over
the next 1–4 weeks.

**Market regimes.** The bullish setups are worst in falling markets (golden cross
above zero, 20 d: −77 bp dev / −167 bp holdout when the market is below its 120-day
average) and roughly neutral in rising markets (−8 / −29 bp). No regime turns them
into a net-of-cost long signal.

**Intraday MACD for entry/exit timing (5-minute bars, 381 stocks, one year).**
Compared with filling at the same clock time on an average day, buying at the first
5-minute golden cross cost +2.9 bp (dev, t 1.5) and −2.5 bp (holdout, t −0.9).
Selling at the first death cross cost +1.3 bp and −3.4 bp. No rule met the
pre-registered ≥3 bp improvement in both periods. Intraday MACD does not improve
execution.

**The earlier 111-event study (57.7% next-day up).** Matched date by date, QMT
data reproduces it exactly (111 events, 57.7%). Those 10 stocks rose on 47% of all
days in that window, so the excess is about +10 points (p ≈ 0.13). In 2015–2021
the same rule on the same stocks gave 42.6% against a 50% base (p = 0.01). The
stocks were chosen after they had performed well, and the effect reverses
between periods. It was not evidence of an edge.

## Recommendations

1. Do not use golden crosses, zero-line crosses, divergences or multi-timeframe
   MACD agreement as buy triggers for 5–20 day holdings. Do not change production
   weights in MACD's favour.
2. Keep one rule as a **risk filter**: do not chase a golden cross that comes with
   a volume surge (5-day volume > 1.5× the 20-day average). Most of this is the
   abnormal-volume effect, the strongest independent price/volume variable found
   (joint t −6.6 dev / −7.4 holdout). A volume-based filter carries it directly.
3. Keep MACD on the desktop and phone as a **description** of trend state, labelled
   as such. Remove it from confidence calibration of the phone forecasts (pending
   your approval: it changes production behaviour).
4. For execution, use simple schedules (open or VWAP-style slices). No MACD timing.
5. If MACD is revisited, test it only as an interaction with a validated factor
   (e.g. reversal in stocks below VWAP), pre-registered, on new data.

## Limitations

No point-in-time ST flag for listed stocks; size proxied by trading amount; no
industry neutralisation yet; costs fixed at 30 bp; equal-weighted excess returns
emphasise small stocks; the holdout was evaluated a second time after the dating
correction (documented in the pre-registration).
