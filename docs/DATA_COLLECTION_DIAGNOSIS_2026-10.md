# Data collection diagnosis, September–October 2026

Evidence: `services/collection_report.py` over 2026-09-01 .. 2026-10-22 (QMT
trading calendar), the recorder logs, and the Windows System event log
(Kernel-Power 41/506/507, Kernel-General 12/13). Read-only analysis.

## Result

**1 of 32 trading days had GOOD coverage (≥95%)** (Oct 21). Four were partial
(Sep 2, 3, 4, 7) and 27 lost (missing or under 1%). The recorder code did not
crash on any of them; the computer was not running it.

| Main cause of the lost day (event log) | Days | Dates |
|---|---|---|
| PC died or was forced off while asleep (unclean restart, Kernel-Power 41) | 12 | Sep 8–11, Sep 15–17, Sep 28, Sep 30, Oct 14–16 |
| Standby started with the **power button** (user-initiated; apps are suspended) | 3 | Sep 29, Oct 19, Oct 22 (pressed Oct 21 20:34 local) |
| Idle-timeout standby **before** the keep-awake recorder existed (installed Oct 20 22:36 local) | 1 | Oct 20 |
| Standby with other reason codes (e.g. 55) | 4 | Sep 21–22, Oct 12–13 |
| No standby or shutdown logged: PC awake but recorder/QMT not running (e.g. reboot without login, QMT logged out) | 7 | Sep 1, 14, 18, 23, 24, Oct 8–9 |

## Mechanism

- The laptop uses **Modern Standby** (S0 low-power idle). In the OEM "Silent"
  power plan, **lid close, power button and sleep button are set to Sleep on AC**.
- The recorder's `SetThreadExecutionState(ES_SYSTEM_REQUIRED)` request works
  against idle timeout: on Oct 21 the screen went off ("Idle Timeout") and the
  session was still fully recorded. Windows does not let applications veto
  **user-initiated** standby (power button, lid), so those stop every process.
- Services start from the Startup folder, i.e. only after a Windows logon, and
  QMT needs an interactive login, so a reboot stops collection until someone logs in.
- The session runs 02:15–08:00 in UTC+1, while the user sleeps, so nobody notices.

## Secondary findings

- The first ~60 s after 09:15, 09:30 and 13:00 have no training samples by
  design (60-second feature windows rebuild after each break). The raw snapshot
  archive is complete there. Auction coverage is therefore measured on raw
  snapshots (82.5% on Oct 21).
- Several symbols get few auction training samples because their snapshots
  update rarely and stale (>12 s) quotes are rejected. That's correct behaviour.
- The mobile prospective ledger (`mobile_prediction_log_v1`) has 0 rows: it only
  records while the phone app is open during the session, and it never is.
- Process heartbeats ("RUNNING") said nothing about whether data arrived.

## Fixes in this cycle

| Fix | Effect |
|---|---|
| `collection_monitor` service | Separates process health from ingestion; alerts when no samples/raw snapshots arrive in a phase, on stale QMT quotes, missing processes, MiniQMT down, battery power, failed integrity |
| Suspension incidents | When the monitor resumes it reports the lost session minutes and the standby/shutdown cause |
| Readiness checks (04:00, 08:40, 12:40) | Warn before the session about power-button/lid settings, battery, missing services, QMT |
| Cloud watchdog (migration 300, pending approval) | Detects a sleeping/off PC from Supabase and pushes a phone alert |
| `collection_report` | Coverage by trading day x symbol x phase, gaps, duplicates, out-of-session rows, suspected stale runs, exchange-age statistics, gap causes |
| Recorder stores exchange timestamp and quote age per sample | Stale or mis-timestamped quotes measurable from 2026-10-23 on |
| Trading calendar from QMT | Holidays are not reported as outages; unknown future days are labelled |
| `qmt_tick_backfill` | Recovers missed sessions from QMT history into a separate, labelled archive |

## What still requires the user or real sessions

1. Power-button/lid settings on AC (`docs/OPERATIONS.md` §2) and not pressing the
   power button at night. No software fix exists for user-initiated standby.
2. Phone alert channel (`alerts.toml`) and, after approval, the cloud watchdog.
3. Validation: coverage must be ≥95% on the next 10 sessions. The daily 15:10
   report and alerts show whether that is happening.
4. A dedicated always-on machine (mini PC or cloud Windows VM with QMT) is the
   structural fix if the laptop is also used as a laptop.
