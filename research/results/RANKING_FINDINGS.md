# Replacement research ranking: findings (2026-10-25)

Pre-registration: `research/preregistration/ranking_composite_v1.md` (written before
results). Results: `ranking_composite_dev.json` (2015–21), `ranking_composite_seen.json`
(2022–26; components were chosen with this period in view, so it is a consistency
check, not a clean holdout). Code: `research/tools/ranking_study.py`; tests:
`tests/test_ranking_study.py`.

## Verdict

**The pre-registered composite C1 fails as a top-30 long-only ranking.** Primary
specification: universe ≥ 50m CNY daily amount, top 30, weekly rebalance, 30 bp
per replaced name:

| | 2015–21 net excess/yr | 2022–26 net excess/yr |
|---|---|---|
| C1 (new composite) | −6.4% (t −1.3), 2/7 years positive | −12.4% (t −2.1), 0/5 |
| B0 (current momentum baseline) | **−39.2%** (t −3.7) | **−25.3%** (t −1.9) |

The weekly version replaces about 80% of its names each week (≈12% a year in
costs). Rebalancing every 20 days, C1 top-30 nets +2.6% / +2.9% a year (t 0.5–0.6):
positive but not significant. No replacement ranking is proposed.

## What the study did show

1. **C1 ranks the whole market well**: rank IC +0.10 at 20 days in both periods
   (t 8.9 and 6.5, positive on about 75% of days). The current baseline is reliably
   wrong: IC −0.068 / −0.090; its top 30 lost 25–39% a year against the market.
   **The momentum-weighted baseline should not be used for anything**.
2. **The information sits in the bottom of the ranking.** Exploratory, post-hoc
   (`ranking_quintiles_exploratory.json`, gross, every 20 days):

   | C1 fifth | 2015–21 | 2022–26 |
   |---|---|---|
   | Bottom (Q1) | −14.0%/yr (t −5.2) | −12.1%/yr (t −2.4) |
   | Q3–Q5 | +2.9 to +6.6% | +3.5 to +4.9% |
   | All except the bottom | +3.5% (t 5.2) | +3.0% (t 2.4) |

   Single components show the same thing. Low abnormal volume, low volatility and
   "near the 60-day high" have positive ICs, yet their top-30 baskets lost money:
   the effects work by *avoiding* the worst names (volume spikes, high volatility,
   recent run-ups), which a long-only investor can use only as an exclusion filter.
3. **Size is the only component that pays on the long side**: the smallest liquid
   names (≥ 50m CNY), every 20 days, +12.7% a year (t 2.9) in 2015–21 and +12.8%
   (t 3.4) in 2022–26. This is the known A-share small-cap premium: crowded, with
   deep drawdowns (−30 to −45%) and capacity limits; delisted stocks are included
   here, which reduces but may not remove survivorship bias.

## Next step (pre-registered, forward only)

`research/preregistration/exclusion_screen_v1.md`: track the C1 bottom fifth as an
"avoid list" forward from 2026-10-23 (first list:
`research/paper/ranking_forward_2026-10-23.csv`, 811 of 4,051 eligible stocks),
new list every 20 sessions, first evaluation after about 80 sessions. Practical use
if it holds: a filter on your own candidate lists (themes, quality, catalysts), not a
stock picker.
