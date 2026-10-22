# Component registry

Status of every Python module, as of the October 2026 hardening cycle.
`python tools/component_registry.py` prints which entry point loads each module;
`--check` (run in the test suite) fails if a module is missing from this file.

**Status legend**

| Status | Meaning |
|---|---|
| PRODUCTION | Runs in an installed background service or the deployed desktop/phone UI. |
| PRODUCTION-OPS | Operational tooling (install, health, reports, backfill). Not a signal. |
| RESEARCH | Research pipeline or audit. May run automatically but never drives a live signal. |
| EXPERIMENTAL | Displayed or computed, but **no evidence of predictive value**. |
| DORMANT | Kept, not running (e.g. requires Level-2 permission). |
| LEGACY-CHAIN | Old version still imported by a newer wrapper; do not edit without the chain tests. |
| DEPRECATED | Not imported by anything active; kept for history. |

Evidence summary used below: the phone direction model (mobile-v10) and the
ML 60-second models show **no edge after realistic costs** on the recorded data
(see `docs/NEXT_GENERATION_ROADMAP.md` §2). They remain EXPERIMENTAL.

## Background services (deploy/services.json)

| Service | Entry | Status | Notes |
|---|---|---|---|
| bridge | `qmt_cloud_bridge` | PRODUCTION | QMT -> Supabase live cache for the watched symbol; computes the V18 1-minute direction (always WATCH without real Level-2). |
| training_recorder | `qmt_l1_training_recorder_v1` | PRODUCTION | 8-stock basket, 5 s samples + 60 s labels. Wraps the LEGACY-CHAIN `qmt_l2_training_recorder_v6` -> `qmt_l2_training_recorder_v5` -> `qmt_l2_training_recorder_v4` -> `qmt_l2_training_recorder_v3` -> `qmt_l2_training_recorder_v2`. |
| research_recorder | `qmt_research_recorder_v7` | PRODUCTION (data) / RESEARCH (model) | Raw L1 snapshot archive (`AStockData/raw`) is the system's primary data asset; also forward-tests the V5B rule model. Chain: `v7` -> `qmt_research_recorder_v6` -> `qmt_research_recorder_v5` -> `qmt_research_recorder_v4b` -> `qmt_research_recorder_v4` -> `qmt_research_recorder_v3`. |
| autotrain | `l1_ml_autotrain_daemon_v2` (+ `l1_ml_autotrain_daemon_v1`) | RESEARCH | Lunch/after-close retraining of V4R (champion), V5R, V6 in subprocesses. No deployment. |
| v6_shadow | `l1_v6_shadow_runner_v1` | RESEARCH | Prospective, frozen-per-day V6 scoring settled on observed bids. |
| collection_monitor | `collection_monitor` | PRODUCTION-OPS | Ingestion/process/power/integrity monitoring and alerts. |

## modules/

| Module | Status | Role |
|---|---|---|
| `alerts` | PRODUCTION-OPS | Local alert log, optional webhook/toast. |
| `auth` | PRODUCTION | Streamlit password gate. |
| `big_orders` | DEPRECATED | Not imported anywhere. |
| `cloud_bridge` | PRODUCTION | Supabase REST client; secret-key or least-privilege device mode. |
| `collection_health` | PRODUCTION | Exchange-time freshness summary in recorder status. |
| `collection_runtime` | PRODUCTION | Keep-awake request on AC power; loop/feed continuity. |
| `costs` | PRODUCTION-OPS | A-share cost model for research metrics. |
| `data_sources` | PRODUCTION | Desktop UI snapshots/K-lines (Eastmoney/Tencent/BaoStock). |
| `direction_v18` | EXPERIMENTAL | V18 1-minute direction; requires real Level-2, otherwise WATCH. |
| `event_evidence` | PRODUCTION | Strict source verification for catalysts (no verified feed yet, so scores are 0). |
| `event_radar` | EXPERIMENTAL | Theme/news clue mapping for the desktop UI; not evidence. |
| `external_level2` | DORMANT | Third-party Level-2 adapter (binary quarantined). |
| `external_level2_v2` | DORMANT | As above. |
| `feed_health` | PRODUCTION | Exchange-timestamp freshness. |
| `level2_engine` | DORMANT (runs, returns empty) | Level-2 analytics; no confirmed L2 permission. |
| `macd_calibration_v5` | RESEARCH | Multi-timeframe MACD context for the research recorder. |
| `market_alpha` | RESEARCH | Daily cross-sectional features/labels/rank baseline. |
| `market_clock` | PRODUCTION | Shanghai clock and continuous-session test. |
| `mobile_model_v10` | RESEARCH | Parity-tested Python port of the phone model for offline evaluation. |
| `paths` | PRODUCTION | Stable runtime/data/config locations outside versioned code. |
| `power_events` | PRODUCTION-OPS | Windows standby/shutdown history and power-button/lid settings. |
| `prediction_journal` | EXPERIMENTAL | V18 1-minute journal (empty in L1 mode). |
| `qishi` | EXPERIMENTAL | Hand-weighted daily "起势" trend score for the desktop UI. |
| `qmt_level2` | DORMANT (runs, returns empty) | QMT Level-2 subscription manager. |
| `qmt_live` | PRODUCTION | QMT tick normalisation. |
| `research_forward_model` | LEGACY-CHAIN | Imported by `qmt_research_recorder_v3`. |
| `research_forward_model_v4` | LEGACY-CHAIN | Base of the V5B research model. |
| `research_forward_model_v4b` | LEGACY-CHAIN | Persistence gate. |
| `research_forward_model_v5` | RESEARCH / EXPERIMENTAL | V5B rule model forward-tested by the research recorder. |
| `runtime_guard` | PRODUCTION | Single-instance socket locks. |
| `setup_vwap` | EXPERIMENTAL | Desktop "Setup" grade (hand-set weights). |
| `short_direction` | EXPERIMENTAL | V17 point score; base of V18. |
| `signals` | EXPERIMENTAL | Desktop buy/hold advice; position ranges marked unvalidated. |
| `trading_calendar` | PRODUCTION-OPS | QMT trading days and session phases. |
| `ui_blocks` | PRODUCTION | Desktop chart helpers. |
| `utils` | PRODUCTION | Formatting helpers. |

## services/ (non-service modules)

| Module | Status | Role |
|---|---|---|
| `bootstrap_bridge_config` | PRODUCTION-OPS | Restores `~/.a_stock_qmt/secrets.toml` (no Desktop/OneDrive scanning). |
| `bridge_doctor` | PRODUCTION-OPS | Bridge connectivity diagnostics. |
| `check_cloud_config` | PRODUCTION-OPS | Validates bridge configuration. |
| `collection_report` | PRODUCTION-OPS | Coverage by day/symbol/phase with gap attribution. |
| `imacd_research_audit_v1` | RESEARCH | iMACD audit. |
| `l1_ml_autotrain_daemon_v3` | DEPRECATED | Superseded by v2 launcher (naming predates versions). |
| `l2_ml_autotrain_daemon` | DORMANT | Level-2 auto-trainer. |
| `l2_ml_autotrain_daemon_v3` | DORMANT | As above. |
| `l2_training_status_v2` | DORMANT | Level-2 training status. |
| `learning_progress` | RESEARCH | Data freshness gate for retraining. |
| `macd_event_study` | RESEARCH | Daily MACD event study (n=111, not significant). |
| `market_alpha_data` | RESEARCH | QMT daily cache preparation. |
| `market_alpha_research` | RESEARCH | Daily baseline ranking and rank IC. |
| `monitor_health` | PRODUCTION-OPS | One-shot health probe for START_MONITOR. |
| `opening_core_audit_v1` | RESEARCH | Opening-hour audit. |
| `qmt_capability_probe` | PRODUCTION-OPS | QMT feature probe. |
| `qmt_history_backtest_probe_v1` | RESEARCH | QMT history availability probe. |
| `qmt_l1_60s_walkforward_v1` | RESEARCH | Historical replay; its feature builder is reused by the backfill. |
| `qmt_l1_60s_walkforward_v2` | RESEARCH | Walk-forward V2. |
| `qmt_l1_60s_walkforward_v3` | RESEARCH | Walk-forward V3 (V4R/V5R/V6). |
| `qmt_l2_training_recorder_v7` | DORMANT | External Level-2 recorder. |
| `qmt_research_recorder` | DEPRECATED | V1 research recorder (not in the active chain). |
| `qmt_tick_backfill` | PRODUCTION-OPS | After-close historical tick backfill into a separate archive. |
| `qmt_walkforward_null_compat_v2` | RESEARCH | Compatibility shim for walk-forward V2. |
| `qmt_walkforward_pandas_compat` | RESEARCH | Compatibility shim for walk-forward V1. |
| `research_validation_report` | RESEARCH | 60 s validation report. |
| `run_imacd_audit_v1` | RESEARCH | Runs the iMACD chain from the installed release (no downloads). |
| `setup_local_config` | PRODUCTION-OPS | One-time local configuration wizard. |
| `test_external_level2` | DORMANT | External Level-2 smoke test. |
| `train_l1_60s_model_v1` | LEGACY-CHAIN | Feature list base for V3+. |
| `train_l1_60s_model_v2` | LEGACY-CHAIN | Wrapper used by V3. |
| `train_l1_60s_model_v3` | LEGACY-CHAIN | Data loading/feature expansion for all L1 trainers. |
| `train_l1_60s_model_v4` | RESEARCH | Asymmetric heads core (realistic-cost metrics). |
| `train_l1_60s_model_v4r` | RESEARCH / EXPERIMENTAL | "Champion" — no edge on the only full test day. |
| `train_l1_60s_model_v4rx_observed_label` | RESEARCH | Observed-label variant (audit). |
| `train_l1_60s_model_v5_challenger` | RESEARCH | Robust challenger. |
| `train_l1_60s_model_v5r` | RESEARCH | V5 runner. |
| `train_l1_60s_model_v6_exec_aligned` | RESEARCH | Execution-aligned challenger used by the shadow runner. |
| `train_l2_60s_model_v2` | DORMANT | Level-2 trainer. |
| `train_l2_60s_model_v3` | LEGACY-CHAIN | Shared day-holdout split (coverage-gated) used by L1 trainers. |
| `train_l2_60s_model_v4` | DORMANT | Level-2 trainer (imported by `train_l1_60s_model_v1`). |
| `train_l2_60s_model_v5` | DORMANT | Level-2 trainer (imported by `train_l1_60s_model_v1`). |
| `v18_doctor` | PRODUCTION-OPS | V18 diagnostics. |
| `v18_selftest` | PRODUCTION-OPS | Deterministic V18 self-test (preflight). |
| `v4r_cross_sectional_rank_audit_v1` | RESEARCH | Audit. |
| `v4r_direction_asymmetry_audit_v1` | RESEARCH | Audit. |
| `v4r_imacd_filter_audit_v1` | RESEARCH | Audit. |
| `v4r_meta_label_audit_v1` | RESEARCH | Audit. |
| `v4r_meta_label_audit_v2` | RESEARCH | Audit. |
| `v4r_meta_label_audit_v2_exec_metrics` | RESEARCH | Audit. |
| `selftest_event_evidence`, `selftest_imacd_research_v1`, `selftest_l1_trainer_v3`, `selftest_l1_trainer_v4`, `selftest_l1_trainer_v4r`, `selftest_l1_trainer_v4rx_observed_label`, `selftest_l1_trainer_v5`, `selftest_l1_trainer_v6`, `selftest_l1_v6_shadow_runner_v1`, `selftest_monitoring_integrity`, `selftest_opening_core_audit_v1`, `selftest_qmt_l1_walkforward_v1`, `selftest_qmt_l1_walkforward_v2`, `selftest_qmt_l1_walkforward_v3`, `selftest_v4r_cross_sectional_rank_audit_v1`, `selftest_v4r_direction_asymmetry_audit_v1`, `selftest_v4r_imacd_filter_audit_v1`, `selftest_v4r_meta_label_audit_v1`, `selftest_v4r_meta_label_audit_v2`, `selftest_v4r_meta_label_audit_v2_exec_metrics` | TEST | Self-tests. `selftest_l1_trainer_v4` already failed on `main` before this cycle (single-class synthetic head); it is not in CI. |

## Edge functions and UI

| Component | Status | Notes |
|---|---|---|
| `supabase/functions/astock-mobile` | PRODUCTION (API) / EXPERIMENTAL (direction) | Phone API; direction model mobile-v10 has no demonstrated edge. |
| `supabase/functions/astock-micro` | PRODUCTION | Tick-flow micro panel (estimates, not Level-2). |
| `supabase/functions/_shared/security.ts` | PRODUCTION | Shared auth, keys, rate limit. |
| `docs/index.html` | PRODUCTION | Phone UI (GitHub Pages). |
| `app_1.py` -> `app_v18_live.py` | PRODUCTION | Streamlit desktop UI. |
| `deprecated/apps/*` | DEPRECATED | V17/V18 dashboards. |
