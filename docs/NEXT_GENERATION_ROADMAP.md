# Next-generation quantitative roadmap (v2, 2026-10-24)

The question is not "can we predict the next minute" but "where can a retail
investor with one PC, a broker QMT terminal and a few hours a week earn a durable,
measurable, risk-adjusted excess return". v2 adds the full-market factor study,
the pre-registered MACD study and the audit of the current market-alpha baseline.

## 1. What the evidence says now

| Study (all research only) | Result |
|---|---|
| 60–120 s direction models (V4R/V5/V6, phone v10) | No edge: 60 s moves (~7 bp) are smaller than costs (~14 bp); phone v10 offline −15 bp per call after costs |
| MACD, daily, 5–20 days (12.4 m stock-days, delisted included, holdout 2022–26) | No robust incremental value; classic bullish setups were negative in 2022–26; one replicated warning: golden cross on a volume surge (−1.1 to −1.2% over 20 days). See `research/results/MACD_FINDINGS.md` |
| MACD, 5-minute, execution timing (381 stocks, 1 year) | No improvement over same-time or VWAP fills |
| Full-market single factors 2023–26 (5,537 stocks) | 20-day momentum IC −0.072 (t −4.0); reversal, low volatility, low max-return, low trading amount positive (IC 0.04–0.10 at 20 days) |
| Current market-alpha baseline (70% momentum weights) | **Anti-predictive over the last year:** rank IC −0.045 (5 d) and −0.063 (20 d), positive on only 36–40% of days. Research-only; nothing trades on it |

### Which indicators are complementary and which are duplicates

Joint Fama-MacBeth regression of 5-day excess returns on 13 ranked indicators,
development 2015–21 and holdout 2022–26 (t-statistics; 20-day results agree):

| Indicator | Alone (dev / holdout) | Jointly (dev / holdout) | Reading |
|---|---|---|---|
| Abnormal volume (5 d / 20 d) | −4.5 / −5.4 | **−6.6 / −7.4** | Strongest independent effect: attention spikes reverse |
| Log 20-day amount (size/liquidity) | −7.4 / −6.3 | **−5.2 / −3.2** | Smaller, less traded names do better (capacity and survivorship caveats) |
| 20-day volatility | −5.5 / −4.6 | **−3.1 / −4.2** | Low volatility does better |
| Distance to 60-day high | −2.5 / −1.4 | **+4.5 / +2.8** | Given the others, nearer the high is better |
| 5-day return, 20-day return, 60-day return, max return, VWAP distance, DIF, histogram | −3 to −9 alone | mostly insignificant jointly | **One cluster:** "recent strength → weakness". One member is enough |

So MACD, reversal, momentum and VWAP distance largely duplicate one another.
Volatility, size/liquidity, abnormal volume and position-in-range carry separate
information. The next model should take one variable from the recent-strength
cluster, not five.

## 2. Your preferred direction, made testable

| Component | What exists | What to build | How it is judged |
|---|---|---|---|
| **Fundamental quality** | Nothing point-in-time | ROE, gross profitability, accruals, cash conversion, leverage, with **announcement dates** (Tushare Pro `fina_indicator`/`income` by period, about 200–500 CNY/year; BaoStock is free but too slow for the full market) | Single-factor IC and decile spreads, size/industry neutralised; walk-forward |
| **Genuine catalysts** | Event framework scores all events 0 (no verified feed) | Earnings pre-announcements and express reports, buybacks, major-shareholder reductions, lock-up expiries, index inclusions, all dated (Tushare/AKShare) | Event studies with the MACD harness (clustered t, holdout); a catalyst must beat its own pre-event drift |
| **Market sentiment** | Abnormal volume (strong, contrarian) | Market breadth, limit-up/limit-down counts, turnover percentile, new highs minus lows (computable from the daily store) | As regime variables: does factor performance differ by sentiment state? |
| **Capital-flow confirmation** | L1 "aggressive buy" share had no value at 60 s | Margin-financing balance changes (daily, by stock); northbound only quarterly since 2024; vendor "main-force" flows only with a published method | Incremental IC over the price/volume model |
| **Entry/exit timing** | 5-minute MACD tested: no value | Open vs VWAP slices vs limit orders at the prior close; avoid gap-up chases; exits by rank decay and time | Implementation shortfall vs VWAP, holdout |
| **Risk control** | Cost model, T+1 | 20–40 names, 5% name cap, 25% industry cap, volatility-scaled sizing; test drawdown de-risking against always-invested | Max drawdown, recovery time, turnover, net return after costs |

Price/volume is the base model (section 1). Quality and catalysts are the next
data purchases, because they are the most likely to be *different* information.
Sentiment and flow are tested as conditioning variables, not stand-alone signals.

## 3. Retain, simplify, freeze

| Retain | Simplify | Freeze / retire |
|---|---|---|
| Raw L1 recorder and archive (execution research), collection monitor, pinned deployment, cost model, research daily store (QMT + BaoStock delisted), event-study harness, backfill | One phone/desktop dashboard; MACD shown as a description, not a forecast; one recent-strength variable instead of five | Daily retraining of 60 s ML; MACD as a buy trigger or confidence input; the momentum-weighted market-alpha baseline (replace in research, then walk-forward); Level-2 purchase |

## 4. Architecture (unchanged in shape, partly built)

```
data/        research_daily (built: QMT 2014-, BaoStock delisted, epoch-dated), PIT fundamentals
             and events (next), raw L1 (execution research)
research/    factor library -> evaluation harness (IC, deciles, clustered event studies, joint
             regressions; built) -> walk-forward portfolio backtester with A-share rules (next)
             -> pre-registration + experiment registry (started: research/preregistration)
decision/    after close: ranks -> target portfolio -> order list for HUMAN approval -> paper ledger
monitoring/  collection health (deployed), factor IC drift, portfolio risk, attribution
interface/   one phone-friendly dashboard
broker/      LAST and only with your approval: separate process, hard limits, manual confirmation
```

## 5. Validation strategy

| Stage | Gate |
|---|---|
| 0 Data | ≥ 99% of trading days complete; epoch-dated bars; delisted names present; point-in-time announcement dates |
| 1 Single factor | Pre-registered; NW t > 3; sign stable in ≥ 70% of years; survives size/industry neutralisation |
| 2 Combination | Walk-forward on 2015–2021; holdout 2022+ evaluated once; deflated Sharpe > 0 |
| 3 Portfolio | Net of costs and capacity; beats CSI 500/1000, the equal-weight universe and the best single factor |
| 4 Paper trading | ≥ 60 trading days with the frozen model, realised within expected bands |
| 5 Small capital | Only with your explicit decision; hard limits |

Lessons from this cycle, now part of the protocol: date every bar from an epoch
timestamp, never from a formatted index; cross-check a sample against a second
source; check that a partition of the universe nets to zero; write the
pre-registration first and log every change after the first run.

## 6. Priorities (next 4–8 weeks)

1. **Collection** (needs you): AC power settings script, phone alerts, BACKFILL_TICKS every two weeks; 10 consecutive sessions ≥ 95% coverage. An always-on mini PC is the structural fix.
2. **Replace the baseline in research**: pre-register a composite of low volatility, low abnormal volume, small/illiquid, one recent-strength variable and distance-to-high; walk-forward 2015–21, holdout 2022–26; costs and capacity (20–40 names, ≥ 50m CNY daily amount).
3. **Portfolio backtester** with T+1, limits, suspensions, lot sizes, the cost model and risk caps; paper ledger and daily target list on the phone.
4. **Point-in-time fundamentals and catalysts** (the Tushare Pro purchase is your decision); quality and event studies with the same harness.
5. **Sentiment/flow as regimes**; execution study (open vs VWAP vs limit) on the 5-minute store.
6. Only then: theme sleeves (power grid, optics, semiconductors, AI infrastructure), tested inside the same model against the whole market.

## 7. If I built it from scratch with your resources

Start where a retail investor can plausibly win, **days to weeks, cross-sectional,
long-only**, and build the point-in-time data first (done for prices; fundamentals
and events next). Use one evaluation harness with pre-registration and a single
holdout. Prefer a few independent, simple factors over many correlated
indicators. Treat portfolio construction and risk as part of the model. Run
operations on an always-on machine monitored from the cloud. Test your themes and
favourite indicators as hypotheses, as MACD was here, and keep what survives. No
live trading until a frozen model has 60+ days of paper results that match its
backtest.

Realistic expectation: a carefully built long-only model may add a modest excess
return over a small-cap benchmark, with drawdowns dominated by the market and by
crowded-factor crashes (for example early 2024). The process exists to find that
out cheaply, on paper, before money depends on it.
