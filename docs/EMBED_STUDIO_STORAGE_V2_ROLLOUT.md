# Embed Studio Storage v2: guarded deployment and rollback

This change installs **dedicated private record storage** for existing Embed Studio
drafts, templates, brand themes, media links and published-message tracking.
It does **not** store image/GIF bytes or send scheduled Discord messages.

## Why this exists
Legacy `embed_studio_v1` is one guild-settings JSON object (up to 256 KiB)
duplicated in the Supabase guild configuration compatibility columns on writes.
This design can amplify writes and exceed future PostgreSQL patch boundaries.
V2 stores each item in `dank_embed_studio_items` and a tiny revision row in
`dank_embed_studio_libraries`. Concurrent writes use an atomic version check,
and only individually changed records receive physical updates.

## Safety conditions
- This migration is specific to the **new** Supabase project with ref
  `viamriafkbqhquvrsnua`. The deployed bot must use that exact HTTPS host.
- The new adapter defaults **OFF**. A repo merge alone does not activate it.
- The SQL migration is additive, service-role-only with RLS, and never drops
  or rewrites legacy guild configuration records.
- The adapter checks the stored legacy snapshot on every read. If legacy
  editors have changed the old record after migration, v2 blocks instead of
  guessing which version should win.
- Until the storage reader and editor have been canary-tested, do not change
  the `DANK_EMBED_STUDIO_V2_ENABLED` environment flag on a live shard.

## Deployment checklist (Android-friendly GitHub + Discloud)
1. Pass the new **Embed Studio v2 PostgreSQL Contract** against disposable
   PostgreSQL, focused Studio regressions, and full repository CI.
2. Independently back up the existing guild configuration and make sure the
   restored copy can be read. Don't treat an untested backup as rollback.
3. Confirm the bot's `SUPABASE_URL` is the **new** pinned project. Inspect
   actual deployed runtime, not just GitHub workflow secrets.
4. Apply `supabase_fresh/migrations/202610200028_dankshield_embed_studio_v2.sql`
   to the new candidate only after confirming environment and schema ownership.
   Confirm RLS, service-role RPC grants, and a read-only table inventory.
5. Stop concurrent writes for **one specific test guild** before migration.
   Run `python -m tools.stage_embed_studio_v2 --guild-id GUILD_ID --dry-run`
   on the private, configured operator environment. It prints counts only.
6. If that guild has legacy Studio records, deliberately run the same tool
   with `--apply --confirm SEED_GUILD_ID`. This is an additive copy with
   per-section parity verification. It refuses an existing v2 destination.
   If no legacy records exist, no seed is required.
7. Only after version parity, turn on `DANK_EMBED_STUDIO_V2_ENABLED=1`
   in the **single canary bot process** using that new Supabase endpoint.
   Verify reconnect recovery, draft autosave, cross-moderator conflicts,
   published role-panel edits, template/theme/media CRUD and mobile UX.
8. For fleet activation, per-guild migration and a verified common runtime
   configuration are required first. Never enable a shard whose guilds have
   unsynchronized legacy records.

## Rollback
Disable the feature flag only **after freezing v2 writes**. Any Studio content
created after enabling v2 will still exist in the dedicated tables but **will
not automatically exist in the legacy blob**. Before allowing edits via the
legacy path, export/reconcile these records explicitly or leave the Studio
read-only; do not silently overwrite new revisions.

## Boundaries and future work
The canonical editor keeps its existing UX, item-count limits and model
validators for this foundation PR. V2 currently validates a 256 KiB combined
operation and transmits a bounded logical snapshot but stores entries in
individually versioned Postgres rows. A later audited change may increase
per-section limits with server-side pagination and item-level RPCs, without
another new persistence system. Persistent uploaded artwork belongs in
object storage, **not** these JSONB records. Advanced scheduled publishing
requires indexed due claims, permission rechecks and idempotent delivery.
