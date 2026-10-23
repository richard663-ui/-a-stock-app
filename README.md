# A-share QMT monitoring and research system

Personal research and monitoring system for A-shares: a Windows PC with MiniQMT
records market data, Supabase relays status to a phone page (`docs/index.html`),
and offline research tools evaluate models. **No automated trading.**

| Read this | For |
|---|---|
| [docs/OPERATIONS.md](docs/OPERATIONS.md) | Nightly routine, power settings, alerts, install/rollback, after-close jobs |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | How the parts fit together |
| [docs/COMPONENTS.md](docs/COMPONENTS.md) | Every module and service, its status and dependencies |
| [docs/SECURITY.md](docs/SECURITY.md) | Security model and the credential migration runbook |
| [docs/DATA_COLLECTION_DIAGNOSIS_2026-10.md](docs/DATA_COLLECTION_DIAGNOSIS_2026-10.md) | Why Sep–Oct 2026 sessions were lost and what was fixed |
| [docs/NEXT_GENERATION_ROADMAP.md](docs/NEXT_GENERATION_ROADMAP.md) | Research assessment and next-version design |
| [research/results/MACD_FINDINGS.md](research/results/MACD_FINDINGS.md) | Pre-registered MACD study (daily 5–20 days and intraday execution) |
| [research/preregistration/](research/preregistration/) | Study protocols written before results, with change logs |
| [deprecated/README.md](deprecated/README.md) | Retired installers and apps, and how to restore them |

Install a reviewed commit on the PC (outside 09:05–15:05 Shanghai):

```bat
powershell -NoProfile -ExecutionPolicy Bypass -File deploy\install_runtime.ps1 -Commit <40-hex commit>
```

Tests: `python -m unittest discover -s tests -p "test_*.py"` and
`python tools\check_repo_hygiene.py`.
