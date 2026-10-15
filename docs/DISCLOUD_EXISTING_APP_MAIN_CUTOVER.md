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

1. Export production `.env` and current Discloud app backup via a **secure
   off-host destination**, not the shared 10 GB volume. Keep tokens private.
   Verify a sample restore and record a nonsecret receipt ID. Protect any
   local database, tickets, attachments, stream state and other durable files.
   The reported ~13 GB `data/torrent_streams` cannot be assumed preserved or
   counted as guaranteed within a plan until Discloud confirms quota behavior.
2. Verify with Discloud support or a noncritical controlled app test exactly
   what its `PUT /app/stoneyverify/commit` retains for files **not in the ZIP**.
   If untracked files may be cleared, DO NOT COMMIT until the full required
   data is backed up and recoverable. Neither a `.discloudignore` nor an
   off-host backup alone changes the server's retention semantics.
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

## Manual GitHub Actions path (no delete)

The workflow `.github/workflows/discloud-existing-app-manual-commit.yml`
is intentionally **workflow_dispatch only**, never `push`. Once merged
and visible on the default branch, GitHub web/mobile → Actions → **Discloud
existing-app code update (manual, guarded)** → Run workflow → select
`main`.

Run `preflight` first with **the full current main SHA**. This performs
read-only repository/manifest checks. It does NOT verify backups, actual
Discloud persistence, Supabase RPCs, or live DB state.

Only after completing all evidence gates, configure `DISCLOUD_TOKEN` as
a GitHub Actions **secret** (never put it in chat, source, or workflow input),
and configure a protected GitHub environment `discloud-production` with
manual reviewers. Select `deploy_existing_app`, provide the exact accepted
SHA and a nonsecret independent backup receipt identifier, and truthfully
confirm the three gates.

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
