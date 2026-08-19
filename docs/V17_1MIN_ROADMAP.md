# V17 1-Minute Direction Engine

## Non-negotiable product requirement

The **future 1-minute direction** card remains in the product. Once enough live data is available it must show a directional lean (`UP` / `DOWN`) even when the signal is weak.

The engine must never present `90% condition agreement` as `90% historical accuracy`.

## Research target

Target for the high-confidence subset:

- horizon: 60 seconds
- metric: directional precision / hit rate
- target: >= 90%
- report coverage together with precision
- validate on forward / out-of-sample observations
- do not claim the target is achieved until the local prediction journal has enough matured samples

A useful model is allowed to be selective. We prefer fewer high-confidence alerts with high precision to forcing every minute into a fake 90% accuracy number.

## Three layers

1. **Swing layer** — daily trend, AI Qishi, risk, event/fundamental context.
2. **Setup/VWAP layer** — whether the current location is worth acting on.
3. **60-second microstructure layer** — timing confirmation using QMT Level-2.

The 60-second layer is a timing/confirmation layer, not the sole reason to open a swing position.

## QMT Level-2 features

When entitlement is available the local bridge subscribes to:

- `l2quote`
- `l2transaction`
- `l2order`
- `l2quoteaux`
- `l2orderqueue`

The bridge reduces raw Level-2 locally into compact features before uploading to Supabase:

- active buy / sell share over 60 seconds
- new buy / sell order imbalance
- buy-side / sell-side cancellation imbalance
- total bid / offer imbalance
- best queue imbalance
- 5/10-level depth pressure
- microprice bias
- short VWAP position
- 10/30/60-second price structure
- thousand-lot / ten-thousand-lot true transaction counts when available

If Level-2 is unavailable, the dashboard keeps working with snapshot estimates, but high-confidence Level-2 validation is disabled.

## Automatic validation

`modules/prediction_journal.py` writes a local SQLite database:

`runtime/one_minute_predictions.sqlite3`

Every live 1-minute prediction is recorded in 10-second buckets and automatically labelled after 60 seconds. The bridge publishes rolling validation statistics with the live cache so we can distinguish:

- all predictions
- high-confidence predictions
- high-confidence predictions made with true Level-2

This dataset is the starting point for later calibration / LightGBM training. Until a trained model beats the rules engine out-of-sample, the rules engine remains the production baseline.

## Acceptance gate before calling it "90%"

Do not call the system 90% accurate unless all are true:

1. true Level-2 is confirmed during trading hours;
2. there are enough matured high-confidence samples (initial gate: >= 200; stronger gate: >= 1,000 across different stocks/regimes);
3. high-confidence precision is >= 90% out-of-sample;
4. coverage is reported;
5. results are not dominated by one stock, one day, or one market regime;
6. the metric still holds after transaction-cost/slippage-aware evaluation for actionable alerts.
