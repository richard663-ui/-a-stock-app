# Security model and credential migration

## What is protected, and from what

The Windows PC runs a **logged-in live brokerage terminal** (MiniQMT). Any code
that runs there could use the broker session. The priorities are therefore:

1. Only reviewed, pinned code runs on that PC.
2. Credentials stay out of the public repository and can be revoked individually.
3. The database accepts only what each client needs.

## What changed in October 2026

| Before | After |
|---|---|
| 16 installers and 2 Python services downloaded code from the moving `main` branch and ran it | No runtime downloads (CI-enforced). Installs accept only a full commit id; every file is SHA-256-verified before each service start |
| Old installers rewrote Startup launchers | Retired to `deprecated/` as non-executable `.txt` |
| Unsigned `txtool.exe` from a third-party repo's `master` installed next to the runtime | Quarantined (`%LOCALAPPDATA%\AStockQMT\quarantine\`, SHA-256 `91C96612...FA16`) |
| Phone password PBKDF2 salt/hash committed in `auth.ts`; sessions signed with the service-role key; no login rate limit; error details returned | Hash, salt and session key are Edge Function secrets; versioned sessions; 600k PBKDF2; rate limit (5/15 min per client, 30/h global); generic errors |
| Bridge setup scanned Desktop/OneDrive for any `secrets.toml` | Explicit path only |
| anon/authenticated roles held ALL grants (incl. TRUNCATE) on 8 tables | Migration 100 revokes them (pending approval) |
| One service-role key everywhere, legacy JWT (stops working end of 2026) | Code supports per-component `sb_secret_` keys and a least-privilege device user (migrations 400) |
| CI actions on moving tags, default write token | Actions pinned to commit SHAs, read-only token, hygiene scanner |

**Limits.** The integrity check stops drift and accidental or tool-driven
changes. It cannot stop malware already running as your Windows user, which
could alter both code and manifest. That risk is reduced by never running
unreviewed scripts and by protecting the GitHub account (below).

## Credential migration runbook (in this order)

Each step is independent and reversible. Steps marked **YOU** need your
account; everything else is prepared in the branch.

### A. New phone access code and session key (logs the phone out once)

1. **YOU**, on the PC: `python tools\make_mobile_secrets.py` — choose a NEW
   code (the old hash is in public git history and must be treated as exposed).
2. **YOU**: Supabase Dashboard -> Edge Functions -> Secrets: add the five printed
   values (`ASTOCK_PASSWORD_HASH`, `ASTOCK_PASSWORD_SALT`,
   `ASTOCK_PASSWORD_ITERATIONS`, `ASTOCK_SESSION_SECRET`, `ASTOCK_SESSION_VERSION`).
3. Apply migration `20261023000200_login_rate_limit.sql` (approval required).
4. Deploy `astock-mobile` and `astock-micro` (they share `_shared/security.ts`).
5. Log in on the phone with the new code. To log every device out later, bump
   `ASTOCK_SESSION_VERSION`.

Until step 4, the deployed functions keep working with the old code.

### B. Per-component secret keys (before the legacy keys stop at end of 2026)

1. **YOU**: Dashboard -> Settings -> API Keys -> "Publishable and secret API keys":
   create the keys, then add a secret key named `windows-bridge`.
2. **YOU**: in `%USERPROFILE%\.a_stock_qmt\secrets.toml` replace
   `SUPABASE_SERVICE_KEY` with the `windows-bridge` key. The bridge already sends
   `sb_secret_` keys correctly. Restart the services (START_MONITOR or reboot)
   and check that `l2_recorder_status_v1` keeps updating.
3. Edge Functions read `SUPABASE_SECRET_KEYS` automatically after redeploy (A.4).
4. Streamlit Cloud (if kept): give it its own secret key, rotate `APP_PASSWORD`.
5. **YOU**, after a quiet day: disable the legacy anon/service_role keys
   (Settings -> API Keys -> Legacy; re-enable if something was missed).

### C. Least-privilege device login for the PC (recommended after B)

1. Apply migrations 100 (grants) and 400 (device policies) (approval required).
2. **YOU**: Dashboard -> Authentication -> Users -> Add user (long random
   password, auto-confirm). Then tag it (SQL editor):
   `update auth.users set raw_app_meta_data = raw_app_meta_data || '{"astock_bridge_id":"family-qmt-01"}' where email = '<device email>';`
3. **YOU**: in `secrets.toml` remove `SUPABASE_SERVICE_KEY` and add
   `SUPABASE_PUBLISHABLE_KEY`, `BRIDGE_DEVICE_EMAIL`, `BRIDGE_DEVICE_PASSWORD`.
   Restart services; verify heartbeats; then revoke the `windows-bridge` key.
   A leaked device credential can then only write its own cache/research rows:
   no reads of other tables, no deletes, no schema changes.

## GitHub account checklist (YOU)

- Two-factor authentication on the account that owns the repository.
- Branch protection on `main`: require a pull request, block force-pushes and deletion.
- Review Settings -> Applications, personal access tokens and deploy keys,
  including any granted to AI coding assistants. Use fine-grained tokens limited
  to this repository, with an expiry, and without admin rights.
- Actions -> General -> Workflow permissions: "Read repository contents".

**Making the repository private** removes public visibility of the strategy and
the infrastructure layout. It does not undo past exposure (forks and the old
hash may exist), so credential rotation (A) is still required. GitHub Pages on a
private repository needs a paid plan (GitHub Pro). Otherwise the phone page
(`docs/index.html`) has to move to another static host. Nothing in this branch
depends on the visibility choice.

## Other observations

- The QMT terminal is installed inside the OneDrive-synced Desktop
  (`OneDrive\Desktop\新建文件夹\...`), so broker client files are uploaded to OneDrive.
  Consider moving it to `C:\QMT\`.
- `txtool.exe` restore (only if you trust the recorded hash):
  `move "%LOCALAPPDATA%\AStockQMT\quarantine\level2api_txtool_20261023\txtool.exe.quarantined" "%LOCALAPPDATA%\AStockQMT\vendor\level2api\cli\txtool.exe"`.
  For external Level-2 later: use a vendor-signed release with a recorded
  SHA-256 and run it under a separate Windows user.
