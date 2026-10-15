# Dank Shield Discloud: update the EXISTING app without deleting it

**Target app ID:** `stoneyverify` (`TYPE=site`, `MAIN=main.py`, 1495 MiB).
**Production GitHub branch still selected in Discloud:** `aaa-cinema-pre-pregateway-bind@0401552e859afbed7c17845366f87752b638235c` (PR #541).
**Main includes the full production history:** `aaa-cinema-pre-pregateway-bind` is an ancestor of `main`; no force push or repository replacement is required.

## Verified deployment mechanism

Discloud's official [deploy-action](https://github.com/discloud/deploy-action) sends
`PUT /app/{app_id}/commit` against an EXISTING app ID, **not**
`POST /upload` and never `DELETE`. The [Discloud CLI](https://github.com/discloud/cli)
also exposes `discloud app commit <app>`. Updating the existing app's CODE
from a SHA on `main` therefore does **not** require changing the
Dashboard's previously connected GitHub branch or deleting the bot.

The action's `env` input **writes over the host .env**. It is intentionally
absent from the manual workflow. The `.discloudignore` excludes local .env,
local `data`, tests, and Git metadata from source ZIP uploads.

**What is NOT established:** public Discloud documentation does not promise
that every host-only file survives a ZIP code commit, nor that all existing
environment entries are preserved. Do not treat this route as a backup or
guarantee of persistence. Code commit may restart the bot / web listener.

## Required safety evidence before a LIVE commit

1. Preserve Discloud's dashboard-managed environment variable **values** in
   private encrypted storage, even when there is no physical `.env` file.
   Export the existing Discloud application backup to a device/storage **off
   Discloud**. Inspect the archive and preserve the original ZIP. The archive
   contains `data/` JSON/SQLite session state and may contain huge Cinema
   torrent downloads. An extracted file list does NOT prove that all backup
   bytes are valid or restorable.
2. Discloud's existing-app `PUT /app/stoneyverify/commit` can update code
   without an app DELETE/UPLOAD, but what happens to *host-only* files absent
   from the ZIP is not guaranteed. The **verified_recovery** path requires
   independently tested file persistence and environment recovery. If those
   are not possible, an explicitly authorized **owner_accepts_unverified_recovery**
   path can proceed with an off-host backup reference and exact manual
   acknowledgement, accepting that files/stream progress/credentials may be
   lost and may NOT be recoverable. That is a conscious risk exception, NOT
   verified recovery. Neither `.discloudignore` nor an off-host copy guarantees
   runtime data survival.
3. On the **correct production Supabase project**, confirm migration from
   PR #540 and effective service_role access exist:
   `public.record_member_activity_batch(uuid,jsonb)`,
   `public.ack_member_activity_batches(uuid[])` and
   `public.member_activity_batch_receipts`.
   This must be verified read-only on the actual production project, not
   inferred from the Git repository or another connected Supabase project.
   Current `main` calls these functions; missing RPCs fail member activity
   tracking closed. SQL migration validity and restoration must be reviewed.
4. Confirm `DANK_CINEMA_ROLLING_ENABLED` is absent or `false` at cutover.
   The external memory-only worker is NOT provisioned by merged PR #571.
   Do not enable rolling mode in a first code deployment.
5. Confirm capacity/RAM and maintenance timing. Discloud has already had
   an app crash/rate-limit incident and the target process is constrained
   to 1495 MiB. Verify all required current SHA CI checks before cutover.

## Quick main release after the initial cutover

Use [Discloud quick main deploy](https://github.com/UglyGameFace/Dank-Shield/actions/workflows/discloud-quick-main-deploy.yml)
after the exact-main push CI passes. It only asks for a real off-host backup
identifier and fresh per-run owner acceptance of unverified-recovery risk.
The main SHA comes from GitHub, and the quick workflow calls the **same**
hardened existing-app workflow rather than introducing another deployer.

First set the repository Actions variable
`DANK_PRODUCTION_MEMBER_RPC_VERIFIED=true` **only after verifying production
member-activity RPC privileges**; missing/false fails closed. The existing
`DISCLOUD_TOKEN` GitHub secret and `discloud-production` approval still apply.
See [quick release instructions](DISCLOUD_MAIN_QUICK_RELEASE.md).

**This is not unattended deployment.** Until live file persistence, environment
restoration and independently tested backups are proven, every update still
needs an explicit owner risk acknowledgment. Keep old Discloud Auto Deploy
OFF; the dashboard may continue showing the obsolete linked branch.

## Manual GitHub Actions path (no delete)

The workflow `.github/workflows/discloud-existing-app-manual-commit.yml`
is intentionally **workflow_dispatch only**, never `push`. Once merged
and visible on the default branch, GitHub web/mobile → Actions → **Discloud
existing-app code update (manual, guarded)** → Run workflow → select
`main`.

Run `preflight` first with **the full current main SHA**. This performs
read-only repository/manifest checks. It does NOT verify backups, actual
Discloud persistence, Supabase RPCs, or live DB state.

Configure `DISCLOUD_TOKEN` as a GitHub Actions **secret** (never put it in
chat, source, or workflow input), and configure the protected GitHub environment
`discloud-production`. For a deploy, select `deploy_existing_app` and enter
the **current** 40-character `main` SHA after its canonical push CI is green.
The deploy job checks current `main` and that exact SHA's canonical CI again,
in case `main` advanced during approval.

Choose **one** recovery policy:

- `verified_recovery` (default): give a nonsecret identifier for a backup that
  has been independently restored, confirm production activity RPCs, and tick
  both file-persistence and environment-recovery checkboxes **only if tested**.
- `owner_accepts_unverified_recovery`: the owner has actually saved the app
  backup off Discloud and saved dashboard environment values privately, but
  cannot prove restoration or host-only retention. Give a nonsecret reference
  to the saved backup (for example `bkp-stoneyverify-20261014-phone` **only if
  it identifies the real saved archive**), confirm verified production activity
  RPCs, and enter **exactly**
  `I_ACCEPT_STONEYVERIFY_DATA_LOSS` in `owner_risk_acknowledgement`. Keep
  unverified recovery checkboxes **OFF**. This path may irreversibly lose
  untracked files, incomplete downloads, local session progress, and possibly
  dashboard variable state. A GitHub success result is not a restore guarantee.

Neither path creates a backup, verifies a ZIP's contents, provisions Cinema
workers, guarantees current Discloud files survive, or bypasses the exact-main
CI gate. Confirm `DANK_CINEMA_ROLLING_ENABLED` remains absent/`false` before
the first deployment. Do not delete `stoneyverify` or its saved ZIP.

The deployment action is pinned to SHA
`fae7024653d941a19daa2b6f56d2ae208e008c54` and its app ID is
`stoneyverify`. It **updates that app in place**. It does not create a new
app, rotate keys, install the Cinema sidecar, or trigger any database DDL.

On completion independently verify the actual deployed commit/code
surface, bot login, website health, Discord rate-limit stability, activity
RPC health, file inventory, voice/ticket features, and signed Cinema
streaming before declaring success. If any regression occurs, use the
backed-up previous code and separately restored app data; note that restoring
old code does not automatically roll back database changes.

Auto Deploy on the old production branch can still later overwrite a manually
committed newer revision if that old branch is pushed again. Do not push
that branch during the migration window; confirm the Discloud integration's
auto-deploy behavior separately.

## Why the production branch has not updated

PR #545 reconciled the complete `aaa-cinema-pre-pregateway-bind` history
into `main`. Later PRs including #569–#571 target `main`, but the old
production branch itself never advanced beyond PR #541. This is normal
GitHub ancestry behavior; it is not evidence that Discloud skipped a push
to a branch it watches.

**No action in this runbook requires deleting the existing app.**
