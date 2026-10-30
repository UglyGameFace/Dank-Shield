# Dank Shield dedicated Supabase quota and missing-schema repair (2026-10-30)

## Scope and observed evidence

Target: dedicated Supabase project `viamriafkbqhquvrsnua` only, not the former shared source or the Discloud PostgreSQL archive. At inspection the database was ACTIVE_HEALTHY, approximately 96 MB. The screenshot's **log ingest 0.74/1 GB** does **not** establish the cause of a previous-cycle quota warning. The earlier 2026-10-28 [egress report](DANKSHIELD_SUPABASE_EGRESS_20261028.md) documented 6.72/5 GB of uncached egress and large repeated guild configuration payloads; PR #758 reduced those payloads.

The Oct 30 24-hour API log check returned approximately 95,197 gateway entries. Frequent paths: `bot_commands` ~27,753; `get_dank_guild_config_compact` ~15,821; activity heartbeat ~12,933. Missing live schema generated at least 7,356 HTTP 404 responses across trusted advert / Atlas / bot-status endpoints. These are **request counts**, not a billed-byte measurement. Database query statistics are cumulative, not 24-hour counts.

## Live additive schema repair (completed and read-back verified)

On Oct 30, a single BEGIN/ROLLBACK preflight was run for all four existing, versioned `supabase/migrations/` files, followed by an atomic BEGIN/COMMIT on the dedicated project with a PostgREST schema reload:

- `20261026083000_community_hub_public_lfg_recruitment.sql`
- `20261026170500_trusted_advert_retention.sql`
- `20261026193000_trusted_advert_bulk_collapse.sql`
- `20261026194500_lfg_atlas_optin_publishing.sql`

A new dedicated migration, `supabase_fresh/migrations/20261030183000_dankshield_status_heartbeat.sql`, was separately rollback-tested, installed, and schema cache reloaded. It restores only the private `bot_status_heartbeats` row used by `public_status_reporter.py`.

Read-back: new advert, collapse, and Atlas tables each started with **0 rows**; the three claim functions exist and returned empty results when invoked on the empty queues; session public opt-in column exists; the service role can run the claims and anonymous users cannot. No preexisting rows deleted, rewritten, or backfilled. The migration defaults new outward-public consent settings to false and does not enqueue any external posts or Discord deletions. Underlying old project and Discloud Postgres were not modified. Four preexisting migrations must remain in version control as authoritative source definitions; avoid replaying unrelated old schema migrations.

## Runtime fixes proposed in the companion PR

- Run all synchronous command queue database fetch/claim/complete/failure calls in worker threads so network hangs cannot directly block Discord's async gateway.
- Change idle command queue polling from 3 seconds to 5 seconds: an idle process drops from about 28,800 to 17,280 polls/day (~40%), at the cost of up to 2 seconds extra dashboard pickup delay. After a command is found, drain quickly. Suppress storage-outage retry storms with a 30-second cooldown. This is a near-term compromise, **not** a true event-driven queue or a promise of instant command pickup.
- Wake a bulk preview-collapse worker immediately when staff authorizes a local job, while backing off empty queue polls to at most five minutes; the durable cross-process fallback remains in place. This feature is not time-critical like live moderation.
- Keep member-activity tracker proof and time-sensitive 4:20 claims unchanged until continuity and timing tests permit a batching redesign.

## Release acceptance and quota proof required

1. Verify exact PR-head CI, unit tests, permissions, no unrelated diff or secrets.
2. Verify installed schema and real API errors, plus worker functionality after Discloud deploy. Confirm the hosted release SHA, not just GitHub main. Respect existing off-host backup, rollback and deployment gates; do not bypass them.
3. Sample 1-hour and 24-hour comparable post-change windows: number of 404s by path, command pickup latency, Discord gateway latency, and **unified uncached egress by service in GB**. Logs sometimes omit response sizes; request counts do not equal billed egress.
4. Exercise owner-only advert pair consent, revocation and 25h retention; Atlas posting must remain opt-in and valid post removal must retain the private deletion token; verify status heartbeat appears after a runtime cycle.
5. Validate verification, member truth/cleanup fail-closed behavior, staff tickets, Community Hub and Share Router. Keep migration parity and historical data reconciliation separate.

The current Free plan 5 GB egress over approximately 31 days means average steady use must be below ~0.16 GB/day (with additional growth margin). The already-billed overage is not retroactively removed by these changes. This document is an **implementation checkpoint**, not production acceptance.
