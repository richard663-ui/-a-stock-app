# Pre-registration: C1 "avoid list" exclusion screen, forward test (v1)

Written 2026-10-25, before any outcome after the first signal date exists. Research
only; no orders and no production change.

## Background (why this hypothesis)

The pre-registered composite C1 failed as a top-30 long-only ranking
(`ranking_composite_v1.md`, results `ranking_composite_{dev,seen}.json`). An
exploratory post-hoc breakdown (`ranking_quintiles_exploratory.json`) showed that
its information sits in the bottom fifth: −14% a year excess in 2015–21, −12% in
2022–26, while "everything except the bottom fifth" earned +3.5% / +3.0% gross.
All history has been seen, so only a **forward** test is clean.

## Hypothesis

In the universe of SH/SZ A-shares with ≥ 50m CNY 20-day average amount (same
eligibility as the ranking study), stocks in the bottom C1 quintile on a signal
date underperform the equal-weight universe over the next 20 sessions, and
"universe minus bottom quintile" outperforms it.

## Protocol

- Signal dates: 2026-10-23 (first, recorded in `research/paper/ranking_forward_2026-10-23.csv`
  and committed before 2026-10-26), then every 20 sessions after it.
- Entry at the next session's open; return to the close 20 sessions later.
- C1 definition, eligibility and code unchanged: `research/tools/ranking_study.py forward`.
- Evaluation once, after at least 3 signal dates have 20-session outcomes (about 80
  sessions, i.e. February 2027), from epoch-dated QMT daily bars.

## Decision rule

Supported if the bottom quintile's mean 20-session excess return is negative and
"universe minus bottom quintile" is positive on average across the evaluated
dates. With only 3–4 dates this is weak evidence. It decides whether to keep
tracking (to 12 dates) and later test the screen as a filter on your own
candidate lists. Any use for real decisions needs your approval.

## Changes

(none yet)
