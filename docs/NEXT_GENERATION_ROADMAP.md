# Next-generation quantitative roadmap

Written after the October 2026 audit and hardening cycle. The guiding question
is not "can we predict the next minute" but "where can a retail investor with
one PC, a broker QMT terminal and a few hours a week plausibly earn a durable,
measurable, risk-adjusted excess return".

## 1. Independent assessment of the current system

**Engineering.** Until this cycle the system was fragile in exactly the places
that decide whether research is possible: code was pulled from a moving branch
onto the brokerage PC, and data collection failed on 31 of 32 trading days
(standby, power loss, no login). Both are now fixed or instrumented (pinned
releases, collection monitor, coverage reports, backfill). The data asset is
still small: 5 usable recorded sessions plus whatever QMT history the backfill
recovers.

**Quantitative.** The work so far concentrates on the horizon where a retail
taker has the least chance of an edge (60–120 s). The evidence is consistent:

| Evidence (recorded data, Aug–Oct 2026) | Result |
|---|---|
| Typical 60 s mid move vs. cost | median 6.6–6.9 bp; round trip costs ≈10.2 bp fees + ≈3.4 bp spread |
| Best single L1 feature IC (5 days, minute-thinned) | ≤ 0.04, sign unstable by day; top-bottom decile ≤ 3 bp |
| Phone model mobile-v10, offline replay (10 days, 5,084 non-overlapping calls) | UP hit 33.9% vs base 34.5%; mean forward mid move −0.6 bp after UP, +0.8 bp after DOWN; −15 bp per call after costs; stronger scores no better |
| ML champion V4R on its only full out-of-sample day | 35% (3-class) accuracy, ≈0 bp gross |
| Perfect foresight at 11 bp cost | only 32% of minutes worth trading, 3.5 bp average |

The daily horizon looks very different. Preliminary single-factor results on
1,518 cached stocks (biased subset; full universe pending) show the classic
A-share effects with large, persistent t-statistics. The current market-alpha
baseline puts 70% of its weight on momentum, which **has the wrong sign** here:

| Factor (preliminary, 5-day horizon) | Mean rank IC | Newey-West t | Stable in both halves |
|---|---|---|---|
| Low max daily return (lottery) | +0.071 | 7.1 | yes |
| Low trading amount (small/illiquid) | +0.072 | 6.0 | yes |
| 1-month reversal | +0.058 | 5.6 | yes |
| Low 20-day volatility | +0.067 | 5.0 | yes |
| 20-day momentum (baseline weight +35%) | −0.058 | −5.6 | yes (negative) |
| 60-day momentum ex last week (baseline weight +35%) | −0.041 | −3.7 | yes (negative) |

These are before costs. They carry survivorship bias (delisted names missing,
which flatters small/illiquid factors), and they are well-known, crowded effects
that crashed hard in episodes like early 2024. They are hypotheses with
a sound prior, not a strategy.

**Events, sentiment, capital flow.** There is no verified data feed yet, and
the event framework correctly scores everything 0. "Capital flow" from L1
3-second snapshots is weak: estimated aggressive-buy share had a slightly
negative IC at 60 s.

## 2. Research directions — verdicts

### A. Short-term (60/120 s) models — deprioritise as forecasts, repurpose the data

- **Stop** developing standalone 60/120 s direction models and stop daily
  retraining on unchanged data. The arithmetic (move size vs. cost, T+1, no
  shorting) makes a profitable retail round trip at this horizon implausible,
  and the evidence agrees.
- **Keep** the raw L1 archive and the recorder. They are the base for two uses
  that do not require a round trip:
  1. *Execution timing* for trades the daily model wants anyway. The value is
     bp saved against a naive "buy at open / buy now", measured as
     implementation shortfall. Even 3–5 bp per trade matters and costs nothing extra.
  2. *Risk monitoring* for held positions (liquidity collapse, spread blow-out,
     approach to limit-down) as alerts, not predictions.
- Keep the V6 shadow runner passive until it has 60 prospective sessions, then
  close the experiment with a written verdict. Label phone direction cards
  experimental (done) or remove them.
- Do **not** buy Level-2 until execution-timing value is shown with L1.

### B. 5–20 day cross-sectional alpha — the main research line

1. **Data first (point-in-time).** Full-universe daily bars (QMT, running),
   listing/delisting dates and **delisted stocks** (survivorship), ST and
   suspension history, industry history, financial statements with
   **announcement dates** (BaoStock provides publication dates; Tushare Pro is a
   low-cost alternative), corporate events (earnings pre-announcements,
   buybacks, shareholder reductions, lock-up expiries).
2. **Evaluate every factor alone** with `research/tools/factor_ic_study.py`:
   rank IC, ICIR, Newey-West t, positive-month share, decile spreads and
   monotonicity, turnover, decay over horizons 1/5/10/20, and **size- and
   industry-neutralised** IC (cross-sectional regression on log market cap and
   industry dummies). A factor that only proxies size is reported as such.
3. **Factor families to test, in this order** (prior evidence for A-shares):
   reversal (5/20 d), idiosyncratic volatility, max-return/lottery, turnover and
   Amihud illiquidity, size; profitability/quality (ROE, gross profitability,
   accruals); growth and earnings surprise with post-announcement drift;
   industry (not stock) relative strength; margin-financing changes; event
   flags (reductions, unlocks negative; buybacks, pre-announced growth positive).
   Northbound-flow data availability was cut back in 2024: verify before use.
4. **Combine simply**: equal-weight z-scores of factors that pass (Newey-West
   t > 3, sign stable in ≥ 70% of years, survives neutralisation), then rolling
   IC weights, then a ridge/elastic-net on ranks. Gradient boosting only as a
   challenger that must beat the linear model out of sample after costs.
5. **Themes you care about** (power grid, energy infrastructure, optical
   communications, semiconductors, AI infrastructure): define the theme
   universe mechanically (industry/concept membership, point-in-time) and test
   the same factor model *inside* the theme against the whole market. Industry
   momentum and earnings revisions are the natural theme signals. Do not hand-pick.

### C. Trading decisions — a rule-based, testable framework

| Element | Proposal to test (not assume) |
|---|---|
| Universe | All A-shares minus ST, < 120 trading days listed, 20-day amount < CNY 20–50m, suspended; optional theme sleeve |
| Entry | Top-decile composite rank after close; skip names limit-up at the open or with spread above a cap; enter at open or a simple VWAP window; later the execution-timing overlay |
| Risk/reward | Expected excess return per decile from walk-forward history vs. 20-day volatility; no trade below a minimum expected-edge-after-cost |
| Exit | Rank-based (drops out of top 30%), time-based (max holding = horizon x 2), catastrophic stop only. Test whether price stops help: in reversal-dominated markets they often hurt |
| Re-entry | Allowed after a 5-day cool-off when rank criteria hold again (prevents churn) |
| Sizing | Equal weight or inverse volatility, 20–40 names, single-name cap 5%, industry cap 25% |
| Exposure / drawdown | Long-only; test a simple regime filter (index trend and breadth) and a strategy-drawdown de-risking rule against "always invested". Index-futures hedging only if the account qualifies |
| Process | Pre-registered rules, decision journal, weekly review against the paper ledger |

### D. Statistics — transparent first, complexity only if it earns it

- Rank IC/ICIR with Newey-West errors; Fama-MacBeth regressions for premia.
- **Count every trial** in an experiment registry; use deflated Sharpe ratio or
  White/Hansen reality checks. A new factor needs t > 3, not 2.
- Shrink estimated premia (James-Stein / ridge); prefer ranks over raw values.
- Calibrate anything displayed as a probability (isotonic/Platt, reliability
  plots, Brier score). Show uncalibrated scores as scores.
- Regimes: observable splits (market trend, volatility, breadth, size spread)
  before hidden-Markov or deep models.
- Deep learning: not recommended at this data size and signal-to-noise ratio.
  Gains reported for Chinese equities rarely survive point-in-time data, costs
  and trial counting.
- Remove factors that fail. A smaller model that survives is worth more.

## 3. Retain, simplify, abandon

| Retain | Simplify | Freeze / abandon |
|---|---|---|
| Raw L1 recorder and archive; collection monitor and coverage reports; pinned deployment; cost model; market-alpha data pipeline; event-evidence verification rules; the V6 shadow runner's prospective design (as a template) | Flatten the training-recorder chain (v2->v6->l1_v1) into one module behind an equivalence test; reduce the research recorder to raw archiving once the V5B experiment closes; one phone/desktop dashboard | Daily retraining of 60 s ML (V4R/V5R/V6) after a final evaluation on backfilled days; Level-2 work until permission and value exist; MACD calibration as a signal; hand-set desktop grades and position ranges as decision rules (keep as context only); the market-alpha baseline's momentum weights |

## 4. Architecture for the next major version

```
data/        daily bars, PIT metadata (listing, ST, suspension, industry), fundamentals with
             announcement dates, events, intraday raw L1 (execution research only)
             -> Parquet partitioned by date, queried with DuckDB; quality gates (coverage, PIT)
research/    factor library (pure, versioned functions) -> evaluation harness (IC, deciles,
             neutralisation, regimes) -> walk-forward backtester with A-share rules (T+1,
             limits, lots, suspensions, fees, impact) -> experiment registry (config hash, data
             snapshot, results; trial counts for deflated Sharpe)
decision/    after close: composite ranks -> target portfolio -> next-day order list for HUMAN
             approval -> paper ledger; optional execution-timing overlay
monitoring/  collection health (done), factor IC drift, portfolio risk, performance attribution
interface/   one phone-friendly dashboard: health, today's targets, paper P&L, alerts
broker/      LAST: xttrader in a separate process and account, hard limits (per-order value,
             daily turnover, symbol whitelist), manual confirmation, kill switch, audit log
```

Everything fits on one PC: 5,600 stocks x 10 years of daily data is about 14
million rows, and factor runs take minutes.

## 5. Validation strategy

| Stage | Gate to pass before the next stage |
|---|---|
| 0 Data | ≥ 99% of trading days complete; PIT checks (no announcement used before its date); delisted names present |
| 1 Single factors | Pre-registered list; NW t > 3; sign stable in ≥ 70% of years; survives size/industry neutralisation; monotone deciles |
| 2 Combination | Walk-forward on development years (e.g. 2016–2023) with 20-day embargo; **holdout 2024–2026 touched once**; deflated Sharpe > 0 at 95% |
| 3 Portfolio | After realistic costs and capacity; drawdown and recovery reported; beats simple benchmarks (CSI 500/1000, equal-weight universe, best single factor) |
| 4 Paper trading | ≥ 60 trading days with the frozen model; realised vs. expected within error bands |
| 5 Small capital | Hard limits, weekly review; scale only on evidence |

Accuracy is never a gate. Net, risk-adjusted excess return after costs is.

## 6. Priorities for the next 4–8 weeks

1. Collection: 10 consecutive sessions at ≥ 95% coverage; complete the backfill.
2. Full-universe daily data plus point-in-time metadata (delisted, ST, suspension, listing dates).
3. Factor study v1 on the full universe with neutralisation. Fix or replace the momentum-weighted baseline.
4. Walk-forward portfolio backtester with A-share execution rules and the cost model.
5. Paper-trading ledger and a daily target-portfolio report on the phone.
6. Execution-timing study on the raw L1 archive (Direction A repurposed).
7. Then fundamentals/events, the theme sleeve, and only much later broker connectivity.

## 7. If I built it from scratch with your resources

I would start from the horizon where a retail investor can plausibly win:
**days to weeks, cross-sectional, long-only**. Round-trip costs of 0.2–0.3% are
small there relative to multi-percent moves, and well-documented A-share
anomalies exist. I would not start at seconds, where costs exceed typical moves
and professional market makers set the price.

- **Data before models.** First build a point-in-time daily database of the whole
  market, including delisted stocks, ST and suspension history, financials with
  announcement dates, and events. Paying a few hundred CNY a year for clean data
  (e.g. Tushare Pro) beats paying for Level-2.
- **One research harness and one protocol.** Pre-register hypotheses, count
  trials, use purged walk-forward, and touch the holdout once. No model versions
  without a written result.
- **Simple, robust models.** Ranked factors combined linearly with shrinkage;
  complexity only when it wins out of sample after costs.
- **Portfolio and risk as first-class parts.** 20–40 names, turnover-aware
  rebalancing, industry caps, explicit drawdown rules tested against doing nothing.
- **Operations sized to reality.** An always-on mini PC or cloud Windows VM for
  QMT, monitored by the cloud; research runs offline on any machine.
- **Your themes as testable hypotheses** (industry momentum, earnings revisions
  inside power grid / optics / semiconductors / AI infrastructure), measured
  against the whole market rather than chosen by hand.
- **No live trading** until a frozen model has 60+ days of paper results that
  match its backtest.

Realistic expectation: a carefully built long-only model may add a modest
excess return over a small-cap benchmark, with drawdowns dominated by the market
and by crowded-factor crashes. Nothing here is a guarantee. The point of the
process is to find out cheaply, on paper, whether the edge is real before any
money depends on it.
