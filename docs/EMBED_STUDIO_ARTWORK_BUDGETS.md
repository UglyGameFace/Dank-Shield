# Embed Studio artwork budgets and capacity audit

## Why this change exists
A saved image link is almost free, but its uploaded image/GIF occupies real
Supabase Storage bytes. The existing 50-link limit and 1 MiB metadata ceiling
are **not database capacity measurements**, and neither prevents concurrent
uploads from exhausting a shared project storage allowance.

This PR adds a service-role-only allocation guard around managed uploads,
separating **per-guild** and **project-wide** physical artwork budgets.

## Owner configuration (required for managed uploads)
Keep `DANK_EMBED_STUDIO_UPLOADS_ENABLED` OFF until the private bucket, the
Storage v2 migration and this migration pass canary verification. Configure:

- `DANK_STUDIO_ARTWORK_GUILD_MIB`: maximum managed artwork bytes per guild,
  expressed as whole MiB, at least 6
- `DANK_STUDIO_ARTWORK_GLOBAL_MIB`: project-wide managed artwork allowance,
  also in whole MiB; must be greater than or equal to the guild allowance

**These are owner-configurable safety budgets, not claims about database or
Discloud capacity.** Determine them from verified object-storage usage, other
project workloads, and the provider plan before enabling uploads. No default
positive allowance is assumed. On a Free plan, leave headroom rather than
allocating the complete advertised allowance to this feature.

Example for a *hypothetical* 1 GiB free-storage project with verified headroom:
`GUILD_MIB=64`, `GLOBAL_MIB=640`. These are example values, not settings
automatically applied to your service. They can be changed if capacity changes.

## Upload path
1. The existing Discord native mobile file modal checks image content and
   dimensions. It does not depend on the filename's extension.
2. The bot generates a unique guild-prefixed path and atomically **reserves
   bytes** in Postgres *before* contacting Supabase Storage. Both per-server
   and global limits include already committed assets and other outstanding
   reservations. Competing uploads share one transaction lock.
3. Only after a successful upload does the bot create the durable metadata
   record and remove the reservation in a **single SQL transaction**.
4. If an upload/finalization fails, it tries to remove **only that newly
   generated object**. It releases the reservation only when Storage confirms
   removal. An ambiguous removal leaves a conservative hold for review.
5. Saving the resulting link into the Studio Media Library uses the existing
   CAS retry. Failure tries to remove only the new uploaded object. Old posts
   and previously saved artwork remain unaffected.

## Usage and cleanup audit
The Media Library now reports used MiB against configured per-server and
global allowances, plus the count of uncertain uploads. A failed read does
not hide the library or fabricate zero usage.

In the bot's private configured operator environment, run:

`python -m tools.audit_embed_studio_artwork --guild-id YOUR_GUILD_ID`

It prints only aggregate byte counts and the number of reservations older
than 30 minutes. It **never deletes files** or outputs service-role keys,
asset URLs, message bodies or member IDs. Old reservations are *candidates*
for investigation, not proof of unused files. Compare them manually with
the real Storage bucket and published embeds before releasing anything.

Managed image objects are public for durable Discord embed rendering; the
upload and metadata write paths remain service-role-only. A moderator removing
a link from their library does not erase that public image.

## Rollout and limitations
Apply additive migration
`supabase_fresh/migrations/202610200034_dankshield_embed_studio_asset_budgets.sql`
**only after #716–#721 schemas and storage ownership are verified**. No
migration, feature flag, or configuration value is changed merely by merging.
The existing system stays functional with managed uploads disabled.

The SQL global-budget guard serializes managed upload reservations so they
cannot oversubscribe quota. Aggregate checks are intentionally conservative
for initial canary loads. Fleet-scale upload throughput will eventually
benefit from counter-backed allocation instead of scanning all existing
metadata on each reserve. Do not claim 10,000-guild throughput from a
single Postgres smoke test.

## Acceptance
- Quota exhaustion blocks Storage calls, including overlapping moderator
  requests, without modifying old assets
- Atomic reserve/finalize and rollback behavior passes ephemeral PG17 tests
- Offline Storage leaves a conservative allocation until reconciliation
- Existing role panels, forms, scheduled announcements and link sharing work
- Public roles cannot query quotas, metadata or invoke allocation functions
- Android UI shows usage without leaking information between guilds
- Discloud/Supabase live headroom and backups verified *before* activation
