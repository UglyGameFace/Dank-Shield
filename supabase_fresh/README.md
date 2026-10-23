# DankShield fresh Supabase migration (separate production candidate)

**Target:** new dedicated organization `Dank Shield`, project `viamriafkbqhquvrsnua`, US East.
**Source:** prior shared Supabase project `hyncpbvqyjojxhjzcdzg`, now legacy/rollback.
**Safety:** this document describes the **historical bootstrap** completed before
Dank Shield began writing to the dedicated project. Do not replay the old
`supabase/migrations` chain against the live project.
Future production SQL must be committed under `supabase_live/migrations/`
and deployed with the hard-targeted workflow.

## Phase 1, verified 2026-10-20

- `migrations/202610200001_dankshield_fresh_critical_core.sql`: 3 service-role-only
  RLS tables and the original atomic sparse guild config + compare-and-swap RPC.
  No hard-coded beta servers, no old invite event ledger, no historical rows.
- Applied successfully to **new project only** using its separate Supabase connection.
- Full source/destination PostgreSQL `to_jsonb(row)::text` row digests match for:
  - `guild_configs`: 12 records, 52 columns
  - `guild_security_settings`: 11 records, 23 columns
  - `guild_security_actor_reputation`: 11 records, 14 columns
- New database is a **snapshot**. Live writes to the old database continue.
- Checked all 3 RLS tables, no anon/authenticated table access, restricted service-role
  execution of `patch_dank_guild_config`. An informational advisory about RLS with
  no policies is intentional because these tables are backend/service-role only.
- Atomic CAS accepted a fresh expected value and rejected a stale one in an
  explicit transaction that was rolled back. No smoke-test config persisted.

## Before cutover

1. Review all live Supabase table and RPC usage, including verification, roles,
   tickets, activity truth, Cinema, Community Hub, and moderation.
2. Stage only necessary current data and selected important historical evidence,
   preserving precise original JSON and checksums; do not replay every old
   migration or copy unrelated game data.
3. Migrate dependent schema, indexes, RLS, triggers, and RPC grants with
   tests against the current bot's callers.
4. Keep invite-block enforcement replay-safe even if historical invite events
   are not retained. Do **not** silently remove its deduplication dependency.
5. Reconcile data changed after each source snapshot; compare final source and
   destination manifests. Confirm no pending verification/session token is
   erroneously reused. Test permissions and rollback.
6. Verify quota, outbound request budgets and rate limits before changing
   credentials. A new Free organization is not unlimited bandwidth.
7. Only after owner-authorized canary: switch the one bot runtime using new
   Supabase credentials, observe startup and guild operations, and preserve
   the previous database for rollback.

**Historical note:** The original bootstrap had no cutover. The live Discloud bot was subsequently verified using the dedicated DankShield project. The old project remains untouched here; production migration ownership is documented in `docs/RELEASE_GOVERNANCE.md`.
