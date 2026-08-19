# V18 Final — Setup × VWAP × Level-2

## Production hierarchy

1. **Swing context**: daily trend, Qishi, MACD, event/risk context.
2. **Setup grade**: A/B/C/D from four mostly independent families: swing context, VWAP location, order flow, catalyst/risk.
3. **VWAP location**: intraday cumulative VWAP, distance, recent slope, reclaim/loss state.
4. **60-second timing**: always shows a direction when live data are ready; true Level-2 raises or lowers confidence.
5. **Validation**: every live 60-second prediction is recorded on the ROG and labelled after the horizon.

## What 90% means

- `condition_agreement` is a real-time agreement score, **not accuracy**.
- `true_l2_high_conf_accuracy_pct` is the measured historical hit rate of matured, true-Level-2, high-confidence predictions.
- The UI must not call the model "90% accurate" until the measured out-of-sample statistic actually reaches that level with enough samples.

## Level-2 inputs

The runtime attempts these XtData periods and degrades gracefully when one is unavailable:

- `l2transaction`
- `l2order`
- `l2quoteaux`
- `l2transactioncount`
- `l2orderqueue`

The direction engine uses price path, true aggressive trade flow, large-order flow, composite L2 state, short VWAP and microprice. Daily setup context is deliberately not allowed to dominate the 60-second engine.

## Local data moat

`runtime/one_minute_predictions.sqlite3` stores:

- symbol/time/entry price
- predicted UP/DOWN
- condition agreement and confidence tier
- true-L2 flag
- feature snapshot used at prediction time
- 60-second future price/return
- actual direction and correctness

This is the dataset used for later walk-forward model training and calibration.

## One-time Windows deployment

1. Download the production `main` ZIP after V18 is merged.
2. Extract to a clean folder such as `C:\AStockQMT`.
3. Open and log into Guosheng QMT.
4. Double-click `install_cloud_bridge_startup.bat`.
5. Double-click `check_v18_final.bat`.

The installer copies the bridge to `%LOCALAPPDATA%\AStockQMT`, stops an older bridge process, creates the Windows Startup launcher, starts the new bridge, and writes logs/data under the stable runtime directory.

## Current limitations / next upgrades

1. The first setup weights are fixed engineering baselines; recalibrate only after enough out-of-sample data.
2. One watched symbol at a time limits dataset breadth. The next major moat upgrade is a background multi-symbol recorder.
3. Prediction labels currently use the first available price after +60s and exclude a tiny flat band; later add bid/ask-aware executable labels and transaction costs.
4. Add walk-forward calibration (LightGBM/logistic baseline + probability calibration) only after a meaningful dataset exists.
5. Split validation by market regime, time-of-day, liquidity, price-limit proximity, and stock bucket to detect hidden failure modes.
6. Add A-share T+1-aware decision policy: 1-minute DOWN is primarily a risk/reduction signal for existing holdings, not a same-day round-trip strategy.
