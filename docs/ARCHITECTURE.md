# Architecture (after the October 2026 hardening cycle)

## Production entry points

| Where | Entry point | What it does |
|---|---|---|
| Windows PC (always on during sessions) | `%LOCALAPPDATA%\AStockQMT\current` -> six watchdog-supervised services from `deploy/services.json` | Market-data capture, research recording, research training, shadow evaluation, collection monitoring |
| Windows desktop shortcuts | `启动盯盘` / `查看盯盘状态` -> `current\services\start_monitor.ps1` | Start missing services, show health |
| Supabase Edge Functions | `astock-mobile`, `astock-micro` (+ `_shared/security.ts`) | Phone API: login, live state, learning status, evaluation |
| GitHub Pages | `docs/index.html` | Phone UI |
| Streamlit Cloud | `app_1.py` -> `app_v18_live.py` | Desktop UI |
| Manual, after close | `PREPARE_MARKET_ALPHA.cmd`, `services/qmt_tick_backfill.py`, `services/collection_report.py` | Daily cross-section data, historical tick backfill, coverage reports |

## Process and data flow

```
 MiniQMT (broker terminal, logged in by the user)
   │  xtdata L1 snapshots (3 s), daily bars, history          (no trading API is used anywhere)
   ├── bridge ────────────────► Supabase qmt_live_cache / qmt_l2_cache ◄── astock-mobile ◄── phone
   ├── training_recorder ─────► AStockData/training/<day>/l2_training.sqlite3  (5 s samples + 60 s labels)
   ├── research_recorder ─────► AStockData/raw/<day>/ticks.sqlite3             (raw snapshot archive)
   │                            + forward test of the V5B rule model
   ├── autotrain (11:35, 15:10) ► AStockData/models/l1_60s/...  (research models, never deployed)
   ├── v6_shadow ─────────────► prospective V6 scores -> Supabase ml_shadow_samples_v1
   └── collection_monitor ────► AStockData/reports/collection/{live_status.json, alerts.jsonl}
                                 + optional phone push, + Supabase collection_health_v1 (after migration)
 Supabase pg_cron watchdog (after migration) ── heartbeat stale during session ──► phone push
```

## Deployment layout (pinned releases)

```
%LOCALAPPDATA%\AStockQMT\
  releases\<40-hex commit>\   exact tree of one reviewed commit + .astock_manifest.json (SHA-256 of every file)
  current  ──junction──►       the installed release
  bin\verify_install.py        checks current against its manifest before every service (re)start
  install_state.json           current commit, previous commits (rollback targets)
  runtime\                     logs, AStock*Watchdog.cmd, integrity_status.json
  legacy_flat_<stamp>\         the pre-pinning flat runtime (restorable)
  quarantine\                  isolated third-party binaries (txtool.exe)
%APPDATA%\...\Startup\AStock*.vbs   start the watchdogs at Windows logon
%USERPROFILE%\AStockData\           all market data, models and reports (never touched by installs)
%USERPROFILE%\.a_stock_qmt\         secrets.toml, alerts.toml, watchlists, shadow state (private)
```

Installs only accept a full commit id, run the test suite against the staged
tree before stopping anything, and are refused during Shanghai market hours.
Rollback (`ROLLBACK_RUNTIME.cmd`) is allowed any time.

## Which models are active

| Model | Where it shows | Status |
|---|---|---|
| mobile-v10 rule score (60 s / 120 s) | Phone "未来1/2分钟" cards | EXPERIMENTAL: offline replay over 10 days shows no edge (UP hit 33.9% vs base 34.5%; about -15 bp per call after costs) |
| V18 1-minute direction | Desktop | EXPERIMENTAL; without real Level-2 it always returns WATCH |
| V5B research rules | Research recorder forward test | RESEARCH |
| V4R / V5R / V6 ML (logistic, HGB) | Learning status, shadow runner | RESEARCH; V4R had ~0 bp gross edge on the only full test day |
| Market-alpha 5/20-day baseline (0.35/0.35/0.15/0.15) | Research CSVs only | RESEARCH; preliminary IC suggests its momentum terms have the wrong sign |
| Desktop 起势 / Setup grade / buy grades | Desktop | EXPERIMENTAL hand-set rules; position ranges labelled unvalidated |

No model is wired to any order API. There is no automated trading.

## Storage

| Data | Location | Notes |
|---|---|---|
| Raw L1 snapshots | `AStockData/raw/<day>/ticks.sqlite3` | Primary asset; exchange timestamps, 5-level book |
| Training samples | `AStockData/training/<day>/l2_training.sqlite3` | Derived; from 2026-10-23 rows also store exchange timestamp and quote age |
| Historical backfill | `AStockData/backfill/qmt_history`, `backfill/training_replay` | Separate source, never mixed into the above |
| Daily bars | `AStockData/market_alpha/daily/*.csv` | QMT front-ratio adjusted |
| Coverage / health | `AStockData/reports/coverage`, `reports/collection` | Generated |
| Cloud | Supabase tables (RLS on, service-role or device-user access) | Live caches, status, research reports |
