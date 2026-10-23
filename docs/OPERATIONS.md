# Operations runbook

All times are Shanghai time unless stated. The A-share session (09:15–15:00
Shanghai) is 02:15–08:00 in UTC+1 and 01:15–07:00 in UTC+0, so the PC records
**overnight** in Europe.

## 1. Every trading night (before you go to sleep)

1. Laptop **plugged in** (the keep-awake request only works on AC power).
2. QMT terminal logged in, market data connected.
3. **Do not close the lid and do not press the power button.** Lock with **Win+L**;
   the screen may turn off. A power-button or lid standby suspends every recorder
   (this caused the Oct 19–22 losses).
4. Optional: double-click **查看盯盘状态** — it shows the pinned version, code
   integrity, the collection monitor verdict and any power-setting risk.

After a reboot nothing records until you **log in to Windows and to QMT**.

## 2. Stop the laptop from sleeping on AC power (one-time, you run this)

On 2026-10-23 the session was lost to an **idle-timeout** standby: the PC
entered deep sleep within 3 minutes although the recorder held its keep-awake
request. On this Modern Standby laptop the keep-awake request is not enough.
The AC sleep timeout must be "never".

There are five power plans (Performance, Balanced, Silent, Turbo and **Razer
Cortex Power Plan**, which is active and sleeps after 10 min on AC). Razer Cortex
and Armoury Crate switch plans by themselves, so the script changes every plan.
In a normal (non-admin) PowerShell window:

```bat
powershell -NoProfile -ExecutionPolicy Bypass -File %LOCALAPPDATA%\AStockQMT\current\tools\set_collection_power_settings.ps1 -Apply
```

It saves the current values to `%USERPROFILE%\.a_stock_qmt\power_backup_<time>.json`
and sets **on AC power only**: sleep after = never, hibernate after = never, lid,
power button and sleep button = do nothing. Battery behaviour is unchanged, the
screen can still turn off, and Win+L still locks. Without `-Apply` the script only
lists the values. To undo, run it with `-Restore <backup file>` (it prints the exact
command). Also consider disabling Razer Cortex's power-plan switching ("Boost"
settings) or uninstalling it if you don't use it.

The collection monitor checks the active plan every 30 minutes and warns in its
readiness alert (04:00, 08:40, 12:40) if any of these is back to sleep.

## 3. Alerts on your phone

**Local monitor (works now).** Create `%USERPROFILE%\.a_stock_qmt\alerts.toml`:

```toml
[webhook]
url = "https://ntfy.sh/<long-random-topic-only-you-know>"
format = "ntfy"          # ntfy | bark | serverchan | json
min_severity = "WARN"

[toast]
enabled = true
```

Install the ntfy app (or Bark / ServerChan) and subscribe to the same topic. The
topic name acts as a password: make it long and random, never commit it.
Readiness alerts arrive at 04:00 Shanghai (the evening before in Europe), 08:40
and 12:40; incidents, interruptions and the 15:10 coverage summary as they occur.

**Cloud watchdog (after migration 300 is approved).** A sleeping PC cannot report
its own outage. Once applied, store the same URL in Supabase Vault:
`select vault.create_secret('https://ntfy.sh/<topic>', 'astock_alert_webhook_url');`
`select vault.create_secret('ntfy', 'astock_alert_webhook_format');`
The database then pushes one alert per hour while the PC heartbeat is stale
during a session.

## 4. Install, upgrade, roll back

```bat
:: show what is installed and whether it verifies
powershell -NoProfile -ExecutionPolicy Bypass -File %LOCALAPPDATA%\AStockQMT\current\deploy\install_runtime.ps1 -Status
:: install a reviewed commit (outside 09:05-15:05 Shanghai)
powershell -NoProfile -ExecutionPolicy Bypass -File <repo>\deploy\install_runtime.ps1 -Commit <40-hex> [-Source <local clone>]
:: roll back to the previous pinned release (allowed any time)
%LOCALAPPDATA%\AStockQMT\ROLLBACK_RUNTIME.cmd
```

The installer stages the commit, verifies it, runs the test suite with the
services' Python, and only then stops services, switches `current`, rewrites the
watchdogs/Startup launchers and restarts. Install logs:
`%LOCALAPPDATA%\AStockQMT\runtime\install_logs\`. Full return to the old flat
layout: `deploy\restore_pre_pinning_runtime.ps1 -BackupDir <backup folder>`.

## 5. Health and coverage

| Question | Where |
|---|---|
| Is it recording right now? | `%USERPROFILE%\AStockData\reports\collection\live_status.json`, or 查看盯盘状态 |
| What alerts fired? | `...\reports\collection\alerts.jsonl` |
| How complete was a day? | `...\reports\coverage\summary_daily_<date>.md` (written 15:10) |
| Any range | `python services\collection_report.py --start 2026-09-01 --end 2026-10-23` |

Coverage is measured from the databases (samples and raw snapshots actually
written), never from process heartbeats. Gaps are attributed to Windows
standby (with its reason), shutdown/power loss, or "PC awake but not recording".

## 6. After-close jobs (never during market hours)

```bat
cd /d %LOCALAPPDATA%\AStockQMT\current
set PY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe
:: recover missed sessions from QMT history into a separate archive
%PY% services\qmt_tick_backfill.py --start 2026-09-08 --end 2026-10-22 --build-samples
:: complete the full-market daily cache, then the research ranking
%LOCALAPPDATA%\AStockQMT\PREPARE_MARKET_ALPHA.cmd
:: single-factor evidence (IC, ICIR, Newey-West t, deciles)
%PY% research\tools\factor_ic_study.py
:: offline evaluation of the phone model on recorded/backfilled ticks
%PY% research\tools\evaluate_mobile_v10.py --sources raw backfill
```

## 7. Troubleshooting

| Symptom | Cause / action |
|---|---|
| `integrity FAILED - service not started` in a log | Code under `current` differs from the reviewed commit. Reinstall the commit or roll back; do not edit files in place. |
| `MINIQMT` / `EXCHANGE_FEED` alert | QMT logged out or network down. Log in again; collection resumes automatically. |
| `SUSPENDED_...` alert with "Power Button" | The PC was put to standby. See §1–2. |
| `PC off or died (unclean restart)` in coverage | Battery drained or forced off while asleep; keep on AC power. |
| `TRAINER_FILES_MISSING` | Incomplete release; reinstall. Nothing is downloaded at runtime. |
| Phone login "尝试次数过多" | Rate limit after repeated wrong codes; wait the stated minutes. |
