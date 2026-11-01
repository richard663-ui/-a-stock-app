# Model evaluation, 2026-11-01

Existing data only; no model weights changed, no trades. Recovered (backfilled)
quotes are **not** counted as live predictions.

## Evidence classes

| Class | What it is | Sources |
|---|---|---|
| Live prospective | Prediction stored during the session, outcome settled later | Supabase `forward_eval_samples_v2` (research model), `ml_shadow_samples_v1` (V6 shadow) |
| Out-of-sample | Model trained on earlier days, scored afterwards on a later held-out day | Auto-trainer report `models/l1_60s/ALL_training_report_latest.json` (2026-10-30) |
| Historical backtest / replay | Rules applied to past data after the fact | Phone-model replay, simple baselines, MACD and ranking studies |

Phone model v10: prospective log `mobile_prediction_log_v1` has **0 rows**, so no live
evidence exists for it. No live predictions exist after 2026-10-27 (Oct 28–30 lost;
their recovered quotes are excluded).

## 1. 60-second models

Labels differ between rows (research model: mid move with a neutral band, 46% flat;
trainer/baselines: smoothed-mid label, 27% flat; V6: buy at ask, value at the bid 60 s
later, beat 2 bp). Compare within a row with right-way vs wrong-way and the signed move.

| Model (class) | Period, sample | Coverage | Precision | Wrong-way | Signed move / edge |
|---|---|---|---|---|---|
| Research v5b, 60 s (live) | Sep 2–Oct 27, 9 sessions, 9,201 | 4.6% (421 calls) | UP 33.2%, DOWN 36.0% | UP 34.7%, DOWN 33.8% | −0.1/−0.2 bp mid (SE ≈ 1 bp) |
| Research v5b, 120 s (live) | 7 sessions, 4,640 | 43.9% (2,038) | UP 25.4%, DOWN 34.7% | UP 37.7%, DOWN 30.7% | −2.4 / −0.6 bp mid |
| V6 shadow (live) | Sep 7–Oct 27, 6 sessions, 6,010 settled | 0.28% (17 UP, 0 DOWN) | 7 of 16 on Oct 21 | – | +1.4 bp gross ≈ −8.8 bp after 10.2 bp fees |
| V4R logistic (out-of-sample) | test day Oct 27, 1,776 | 22.1% | 37.8% | – | −15.9 bp after realistic costs |
| V4R gradient boosting (out-of-sample) | same | 49.7% (mostly DOWN-risk) | 46.9% | – | −11.7 bp after costs |
| Phone v10 (replay) | 10 days, 5,084 calls | – | UP 33.9% (base 34.5%) | – | −15 bp per call after fees |
| Always DOWN (baseline, 7 live-recorded days, 11,063 min) | | 100% | 39.4% | 33.1% | +0.4 bp |
| Coin flip (baseline) | | 100% | 36.2% | 36.3% | 0.0 bp |
| Reversal of last minute (baseline) | | 78% | 39.2% | 37.2% | +0.4 bp |

Test-day base rates (Oct 27, smoothed-mid label): DOWN 41.8%, UP 29.8%, FLAT 28.5%.
Verdict: no 60-second model shows directional skill above simple baselines, and every
one is negative after realistic costs.

## 2. Confidence calibration

Research v5b, by score (directional calls only):

| Score | 60 s n | 60 s hit | 60 s signed bp | 120 s n | 120 s hit | 120 s signed bp |
|---|---|---|---|---|---|---|
| < 20 | – | – | – | 276 | 24.6% | −2.3 |
| 20–29 | 35 | 28.6% | +0.7 | 881 | 28.7% | −1.4 |
| 30–39 | 138 | 36.2% | +1.1 | 572 | 33.7% | −0.5 |
| 40–54 | 180 | 36.1% | 0.0 | 273 | 33.7% | −2.7 |
| ≥ 55 | 68 | 30.9% | −3.4 | 36 | 41.7% | +0.2 |

The "high confidence" flag never fired (0 of 2,459 calls).

V6 shadow, UP probability vs realized executable up-move (all settled samples):

| Predicted | n | Mean predicted | Realized |
|---|---|---|---|
| 0.0–0.1 | 183 | 6% | 17% |
| 0.1–0.2 | 162 | 15% | 24% |
| 0.2–0.3 | 303 | 26% | 15% |
| 0.3–0.4 | 827 | 36% | 16% |
| 0.4–0.5 | 1,953 | 45% | 22% |
| 0.5–0.6 | 1,616 | 54% | 28% |
| 0.6–0.7 | 615 | 64% | 32% |
| 0.7–0.8 | 227 | 74% | 31% |
| 0.8–0.9 | 113 | 84% | 42% |
| ≥ 0.9 | 11 | 93% | 45% |

UP probability ranks outcomes somewhat above 0.3 but is overconfident by about 2×.
DOWN probability does not rank: 0.4–0.6 → 44.5% (n 5,262), 0.6–1.0 → 39–46% (n 613,
1–2 sessions). Samples are overlapping 5-second snapshots from 6 sessions, so the
effective sample size is far smaller than n. V4R reports contain no calibration data.

## 3. 5–20 day stock selection (historical backtests only)

Top 30 names, universe ≥ 50m CNY daily amount, 30 bp per replaced name, annualised
arithmetic mean of non-overlapping periods (overnight gaps between periods not
compounded, so absolute returns are approximate; excess returns use the same method
for strategy and benchmark). Benchmark: equal-weight universe.

| Ranking, rebalance | 2015–21 net/yr | excess/yr (t) | max DD | 2022–26 net/yr | excess/yr (t) | max DD |
|---|---|---|---|---|---|---|
| C1 composite, weekly (pre-registered primary) | +13.0% | −6.4% (−1.3) | −61% | −3.8% | −12.4% (−2.1) | −47% |
| C1 composite, every 20 days | +15.4% | +2.6% (0.6) | −41% | +11.7% | +2.9% (0.5) | −30% |
| Current baseline B0, weekly | −19.8% | −39.2% (−3.7) | −93% | −16.6% | −25.3% (−1.9) | −68% |
| Current baseline B0, every 20 days | −6.7% | −19.5% (−1.6) | −80% | −27.4% | −36.2% (−3.6) | −75% |
| Size only (smallest liquid), every 20 days | +25.5% | +12.7% (2.9) | −44% | +21.5% | +12.8% (3.4) | −31% |

2022–26 is not a clean holdout for C1's component choice. Live forward evidence:
the avoid-list paper track started 2026-10-23; no outcome is evaluable before about
February 2027.

## 4. Progress

The October changes improved data collection, stability and measurement (realistic
costs, coverage-gated test days, pre-registration, dating and estimator corrections).
They did not improve predictive performance: no production model weight changed, live
60-second results are at chance, and the 5–20 day work so far found what *not* to use
(MACD as a trigger, the momentum baseline) plus an exclusion screen still to be tested.

## 5. Limits and priority

1. 60-second economics: typical move ≈ 7 bp vs 10–16 bp round-trip costs.
2. Too little live evidence: 9 (research), 6 (V6) and 1 (V4R test) sessions on 8 stocks.
3. Overconfident probabilities; scores that do not rank.
4. 5–20 day: long-only cannot use bottom-quintile information except as a filter;
   turnover; no fundamentals or catalyst data.
5. Operations: the bridge crash loop and handle leak that cost Oct 28–30 are unfixed.

Priority: fix item 5 before the next session; freeze 60-second model development
(keep the data for execution research); put research effort into the 5–20 day track
(avoid-list forward test, low-turnover portfolio backtester, then point-in-time
fundamentals and catalysts).
