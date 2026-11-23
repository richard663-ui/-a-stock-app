# PROPOSED pre-registration: earnings surprise and cash-flow quality, 5–20 trading days (v1)

**Status: proposal only — not approved, not run.** Nothing below may be computed
until the owner approves this text (or an edited version). The approved text is
then committed unchanged before the first result; any later change is logged at
the end. Research only: no production, signal, model-weight or trading change,
whatever the result.

## Why this study

Price/volume ideas have been exhausted without a tradable edge (MACD study,
ranking study, 60-second tick models). Earnings news is a different information
source. The best-documented short-horizon fundamental effect is post-earnings
announcement drift (PEAD): prices keep moving for weeks in the direction of an
earnings surprise. The point-in-time store `~/AStockData/fundamentals`
(see `docs/research/FUNDAMENTALS_PIT_VERIFICATION.md`) makes an honest A-share
test possible for the first time: every figure is dated by its publication,
and restated figures carry the date of the restatement. (Eastmoney alone would
not do: it shows restated figures under the first publication date for a
material share of stock-periods; see the verification report.)

## Questions

- **H1 (primary, long side):** stocks whose newly published quarter shows a
  large positive earnings surprise outperform the equal-weight universe over the
  next 20 trading days, net of costs, beyond the known price/volume effects.
- **H2 (primary, avoid side):** stocks with a large negative surprise **and**
  weak cash backing (operating cash flow below half of net profit, or negative)
  underperform over the next 20 trading days. Earlier studies found the usable
  information mostly in the bottom fifth, so the avoid side gets its own test.
- H3 (secondary): cash confirmation — among positive surprises, cash-backed ones
  (CFO / 归母净利润 ≥ 1) beat accrual-driven ones (< 0.5).
- H4 (secondary): earnings previews (业绩预告) — same drift test on preview
  dates, using the BaoStock preview table (all versions, delisted included).

5- and 10-day horizons are reported for H1–H2 as secondary results.

## Data (all point-in-time)

- Fundamentals: `reports_pit.parquet`, joined with `asof_period` /
  `asof_latest` on `available_date` (primary) and `strict_date` (check R1).
- Prices: `~/AStockData/research_daily` (QMT 2014– plus BaoStock delisted),
  SH/SZ only. BJ stocks are excluded (no price data).
- Previews: `previews.parquet`, source `bs_forecast` for H4.

## Definitions (fixed before any result)

- **Event:** first publication (version 1) of a periodic report, dated
  `available_date`. Later versions are not new events.
- **Single-quarter profit:** from cumulative 归母净利润 known on the event date
  (Q2 = H1 − Q1, Q3 = Q3 − H1, Q4 = FY − Q3), each component taken as known on
  that date (`asof_period`), so restatements published later are never used.
- **Surprise (SUE):** (quarter profit − same quarter a year earlier) divided by
  the standard deviation of that yoy difference over the previous 8 quarters
  (at least 6 required). Ranked within all events of the same calendar month.
  Robustness variant: the same with 扣非净利润.
- **Cash backing:** trailing-four-quarter CFO / trailing-four-quarter 归母净利润,
  as known on the event date; undefined when profit ≤ 0 (then "weak" if CFO < 0).
- **Entry:** open of the first trading day **after** `available_date`; exit at
  the close 5, 10 or 20 trading days later (last close if suspended/delisted).
- **Excess return:** versus the equal-weight eligible universe over the same
  window. Eligibility as in the MACD study (≥ 120 bars, close ≥ 2 CNY, traded,
  not known ST, entry open not at the up-limit), plus 20-day average amount ≥
  20m CNY.

## Tests

1. Event regression: 20-day excess return on SUE quintile dummies plus the five
   price/volume controls (abnormal volume, 20-day amount, 20-day volatility,
   distance to 60-day high, 20-day return), standard errors clustered by event
   week.
2. Portfolio (long-only, what the owner could trade): each week hold, equal
   weight, the top-SUE-quintile events of the last 5 trading days that pass
   eligibility; 30 bp round-trip cost per name; benchmark the equal-weight
   eligible universe. Avoid side: excess return of the bottom-quintile ∩ weak
   cash events (a stock to exclude, not a short).
3. Report gross and net, Newey-West t, years positive, results by announcement
   season (Apr / Aug / Oct), by size tercile and by market regime.

## Periods

- Development: events 2016-01-01 – 2021-12-31 (2015 lacks 8 quarters of history).
- **Holdout: events 2022-01-01 – latest, run once, only after the development
  verdict is committed.** No fundamentals study has used this store before,
  so the holdout is clean for the fundamental variables. (The price/volume
  controls were chosen on 2015–2026 data; they are controls only.)

## Decision rules

- H1 or H2 is **promising** if in development the primary 20-day test has the
  expected sign with clustered t ≥ 3.0 (two primaries, three horizons: a strict
  bar), the long-only (H1) or avoid (H2) portfolio has net excess with the
  expected sign and Newey-West t ≥ 2.0 in at least 4 of 6 years, **and** in the
  holdout the same sign with t ≥ 2.0.
- Promising → forward paper track for 60 trading days (lists logged before
  outcomes), then a report. Any live use needs a separate decision by the owner.
- Not promising → report what each piece carried; no replacement is proposed
  from the same data.

## Point-in-time robustness (must not change the verdict)

- R1 `strict_date` instead of `available_date`.
- R2 drop every row that is not `verified`, `first_from_baostock` or
  `screened` (i.e. `unverified` and `revised_only`).
- R3 entry one day later (second open after publication).
- R4 drop the first report after listing and pre-listing periods.
- R5 only fully checked rows (`verified` or `first_from_baostock`; smaller,
  correction-heavy sample).
- R6 cash-backing variables only from fully checked rows (the one-query screen
  does not see cash-flow-only restatements).

If R1, R2, R5 or R6 changes the sign or halves the effect, the result is
treated as contaminated by revised data and the verdict is "not promising".

## Known limitations

No point-in-time market cap (20-day amount as size proxy), no point-in-time
industry (Shenwan history still unsolved), ST flags not point-in-time for listed
stocks, limit-down exits not modelled. Most periodic rows are compared with
the first-published figures only through the one-query screen (profit/revenue
ratio), so cash-flow-only restatements can remain (rate measured in the
verification report; R6 guards against it). BJ excluded.

## Effort

Uses only data already on disk; one script plus tests; a few hours of compute
at low priority. No purchase needed.

## Changes after approval

(none yet)
