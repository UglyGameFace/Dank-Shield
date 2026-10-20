# Phase 6: Dedicated Dank Shield Community Hub schema/RPC parity

**Only new offline project:** `viamriafkbqhquvrsnua`. Old shared Supabase remains live with the production bot. This is the sole active Dank Shield migration task, not an independent Community Hub UX project.

## Scope verified against source code

The original Community Hub schema spans four canonical source migrations, in this order:

1. `supabase/migrations/20260925193000_community_hub.sql`
2. `supabase/migrations/20260926044000_community_hub_matchmaking_loop.sql`
3. `supabase/migrations/20260926054000_community_hub_hublink.sql`
4. `supabase/migrations/20261018010000_community_hub_external_the100.sql`

The dedicated migration `202610200016_dankshield_community_hub.sql` replays the **16 tables and 24 final RPC definitions** from these sources. The only deliberate SQL adaptations are removing the source's outer transaction statements, switching 24 backend-only `SECURITY DEFINER` functions to `SECURITY INVOKER` and using a pinned `pg_catalog,public,pg_temp` search path. The existing source restricts functions and tables to `service_role`; the invoking bot already uses this backend role. We tested actual calls with `SET LOCAL ROLE service_role`.

**Index follow-up:** Supabase's performance advisor identified three FK reverse lookup indexes missing from the canonical Hub source. Versioned migration `202610200017_dankshield_community_hub_fk_indexes.sql` adds indexes on managed resources' `session_id`, reports' `session_id`, and sessions' `parent_session_id`; verified in rollback-only SQL. Advisor now reports no unindexed FKs, only new unused-index INFO notices. This is a measured source-schema improvement, not an unsolicited schema redesign.

The rollback-only smoke creates a session, replays its idempotency key, publishes and joins a session, verifies cross-guild isolation, generates a HubLink digest and confirms the external The100 provider flag is off by default. No synthetic rows remain. Existing 5–30 guild bot paths are unchanged; no new shards or workers.

## Important missing source data

An installed schema alone **does not migrate** Hub settings, current active sessions, partner links, privacy preferences, notifications, opted-in matchmaking availability, managed Discord resource provenance or event state. Do not seed fake generic defaults for real guilds, because that could accidentally enable room creation, reset per-guild limits or delete/strand real rooms.

The Aiven historical originals staging code documents `dank_community_hub_settings` among its table sources, but a verified **current source snapshot/digest, all Hub tables' individual counts, and recent writes** have not been reconciled to this new project. The live bot must continue using old Supabase until each authoritative table is reviewed and imported or explicitly cleared according to safe feature semantics.

### Preserving runtime contracts at cutover
- Map configured channels, limits and features per guild; verify no accidental default enablement.
- Treat managed Discord channels/threads as real resources: never auto-delete without durable ownership and source matching.
- Handle active sessions with continuity/recovery, not silent abandonment.
- Preserve opt-ins, privacy exclusions and partner authorization where still valid.
- Verify sensitive report/personal-data retention independently; never apply blanket TTL to moderation evidence.
- Feature and PostgREST/RPC tests must run under the real service-role connection, and Discord canary/rollback checks are mandatory.

### Other migration blockers

AntiNuke confirmed-actor reputation and security settings already have 11 source-verified candidate records each and should **not** be recreated or re-seeded. The runtime `modlog.py` additionally reads `warns` but its original column contract/data cannot be safely reproduced from repository migrations; treat warn-count parity as an explicit fail-closed source audit blocker rather than substituting a fake empty warnings table. Cinema/profile providers, broader moderation logs, source write catch-up, backups and load/egress tests are also incomplete.

### Branch hygiene

One branch `migration/dankshield-community-hub-20261020`. Merge only after exact-head CI. The guarded daily cleanup applies to finished migration branches; current Discloud, recovery and backups must remain protected. This PR does not delete branches or source records.
