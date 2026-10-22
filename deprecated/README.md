# Deprecated components (kept for history, not for use)

Retired in the October 2026 hardening cycle. Nothing here is installed or run.
Everything remains in git history, and the tag `archive/pre-hardening-2026-10`
marks the last commit where these files were live.

## Why the root scripts were retired

16 of the 27 scripts downloaded Python from the moving `main` branch on GitHub
and installed it into `%LOCALAPPDATA%\AStockQMT`, the same machine where the live
QMT brokerage terminal is logged in. They also rewrote the Startup launchers.
Running any of them would bypass the pinned, hash-verified deployment and could
downgrade the recorders. They are renamed `*.bat.txt` so they cannot run by
double-click.

Deployment now goes through `deploy\install_runtime.ps1 -Commit <40-hex>`
(see `docs/OPERATIONS.md`). Local diagnostics have direct Python equivalents:

| Retired script | Replacement |
|---|---|
| `update_*`, `install_*`, `hotfix_*`, `repair_*`, `apply_*` | `deploy\install_runtime.ps1 -Commit <id>` (or `-Rollback`) |
| `start_cloud_bridge.bat`, `install_cloud_bridge_startup.bat` | the installer writes all watchdogs and Startup launchers |
| `check_bridge_health.bat`, `check_v18_final.bat` | `python services/bridge_doctor.py`, `python services/v18_selftest.py` |
| `check_research_recorder.bat`, `run_l2_training_status.bat` | `CHECK_MONITOR.cmd` and `python services/collection_report.py` |
| `run_60s_validation_report.bat` | `python services/research_validation_report.py` |
| `run_macd_event_study.bat` | `python services/macd_event_study.py` |
| `run_qmt_capability_probe.bat` | `python services/qmt_capability_probe.py` |
| `run_qmt_history_backtest_probe.bat`, `run_qmt_l1_60s_walkforward.bat` | `python services/qmt_history_backtest_probe_v1.py`, `python services/qmt_l1_60s_walkforward_v3.py` (after close only) |
| `train_l2_60s_model.bat` | Level-2 training is dormant until L2 permission is confirmed |
| `start_v17.bat` | V17 dashboard retired; use `app_1.py` / `app_v18_live.py` |
| `prepare_external_level2api.bat` | see "External Level-2 provider" below |

Run Python commands from the installed release so they use reviewed code:
`cd %LOCALAPPDATA%\AStockQMT\current` then
`%LOCALAPPDATA%\Programs\Python\Python312\python.exe services\...`.

## Retired apps

`apps/app_v17.py`, `apps/app_v18.py`, `apps/app_v18_final.py` were earlier
Streamlit dashboards. The deployed desktop UI is `app_1.py` -> `app_v18_live.py`
(unchanged) and the phone UI is `docs/index.html`.

## External Level-2 provider (not deleted)

`prepare_external_level2api.bat` downloaded an unsigned Windows executable
(`txtool.exe`) from a third-party repository's `master` branch and protobuf
definitions, then ran the executable with vendor credentials. The Python
adapter (`modules/external_level2.py`, `modules/external_level2_v2.py`,
`services/qmt_l2_training_recorder_v7.py`) stays in the repository. It is not
used by any running service. On the PC the binary was moved to
`%LOCALAPPDATA%\AStockQMT\quarantine\` (see `docs/SECURITY.md` for restore steps).
To re-enable Level-2 safely, use a vendor-signed release with a recorded
SHA-256, run it under a separate Windows account, and pin it like any other
release.
