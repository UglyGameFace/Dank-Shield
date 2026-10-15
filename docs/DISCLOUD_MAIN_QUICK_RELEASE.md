# One-approval updates from GitHub `main` to the existing Discloud app

**Production application:** `stoneyverify`. **Deployment authority:** GitHub
`main` through a single guarded reusable workflow. Discloud's dashboard may
continue to display its old GitHub branch. Leave the old-branch **Auto Deploy
OFF**; it does not manage this workflow.

## What this changes

`.github/workflows/discloud-quick-main-deploy.yml` offers a compact
owner-only **Run workflow** form:

1. `off_host_backup_receipt` is a private, **nonsecret ID** of an actual
   off-Discloud backup the owner checked for this deployment. This does not
   verify archive recoverability or freshness by itself.
2. `owner_risk_acknowledgement` requires the owner to type the exact phrase
   `I_ACCEPT_STONEYVERIFY_DATA_LOSS` **each deployment**. It cannot be
   stored in a variable, assumed from the last release, or bypassed.

The workflow gets the full immutable `main` commit SHA from GitHub itself.
It invokes the **existing**
`.github/workflows/discloud-existing-app-manual-commit.yml` via
`workflow_call`, not an independent deployment implementation. The called
workflow still requires:

- `refs/heads/main`, exact supplied SHA, unmodified `stoneyverify` manifest,
  no mutable files or private environment files in the deploy ZIP.
- `main` not changed during approval; successful **canonical push CI** for
  that exact SHA, not just PR tests.
- Real production member-activity RPC verification.
- Official SHA-pinned action updating only the existing Discloud app,
  without writing its dashboard environment variables.
- The `discloud-production` environment protections and the same
  serialized non-cancelling deployment concurrency.
- Explicit fresh owner acknowledgment of possible unrecoverable data loss.

**One-time setup:** in GitHub repository Settings → Secrets and variables →
Actions → **Variables**, create a **repository variable** named
`DANK_PRODUCTION_MEMBER_RPC_VERIFIED` with value exactly `true`, but
**only** once you've separately verified
`record_member_activity_batch(uuid,jsonb)`,
`ack_member_activity_batches(uuid[])`, and the receipt table on the
*actual production Supabase project* with correct service-role execution
privileges. Delete/change the variable to `false` if the prerequisite
becomes false. The quick workflow **fails closed** if missing or false.
Do not put database passwords, Discloud tokens, or backup secrets in this
variable.

`DISCLOUD_TOKEN` must remain a GitHub secret available to the
`discloud-production` deploy environment, as with the already-working
manual workflow.

## How to run it (Android/GitHub web)

1. Wait for exact-main **Dank Shield CI** to complete successfully.
2. Open [Discloud quick main deploy](https://github.com/UglyGameFace/Dank-Shield/actions/workflows/discloud-quick-main-deploy.yml)
   → **Run workflow** → choose **main**.
3. Enter the backup ID for a copy you **actually** saved off Discloud, then
   type the owner risk phrase, review the consequence, and run. If the source
   advanced or CI is not green, the call is rejected.
4. When done, verify bot login, `/dank home`, saved member/community
   settings, Cinema JSON health, authenticated Feed → Play, and **the saved
   data that must survive**. Do not interpret GitHub green as a live
   acceptance or storage-retention guarantee.

You no longer need to copy a 40-character SHA, toggle five checkboxes,
or select a recovery policy for routine owner-accepted-risk releases.
The full original manual workflow remains available for verified recovery
and read-only preflight.

## What is *not* automated yet

A fully unattended push-to-production workflow is **deliberately NOT
enabled**. Current Discloud code-commit retention for host-only state and
dashboard-managed environment recovery remain unverified. The phone backup
can be useful but is not a demonstrated restore or a guarantee that newer
files survive. Preserving `data/` JSON, SQLite, active Cinema sessions and
other cross-server state is not the same as retaining old torrent movies.

Cinema already has bounded cleanup for idle/unleased sessions and orphaned
cache directories; that behavior must still pass production storage
acceptance tests. The large-movie streaming/cache budget limitation is a
different unresolved issue. No automatic deletion of cache or user data
is introduced by the quick workflow.

**Future unattended deployment** should require a tested off-host rotating
backup, verified environment and file recovery, non-destructive app update,
and independent post-deploy probes + rollback/stop rules. Do not turn it on
merely by hardcoding the current risk acknowledgement.

## Next stabilization slice: tickets

The existing member-visible ticket channel **topic** contains internal
`owner_id`, `ghost`, `category`, `ticket_number` fields. Ticket creation,
recovery, sync and legacy readers rely on that topic contract. Fix it
coherently, migrate existing tickets safely and test staff/member visibility.
This belongs in its own stabilization PR after this deployment workflow.
