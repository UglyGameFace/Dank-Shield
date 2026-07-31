# ACTIVE TASK

## DS-TICKETS-015 — Make ticket numbering permanently durable

**Status:** IMPLEMENTED — EXACT-HEAD CI AND PRODUCTION MIGRATION PENDING
**Branch:** `fix/ticket-counter-durable-migration`
**PR:** #157

## Previous task completed

DS-TICKETS-014 claim-first ticket handling was merged through PR #147 as production commit `7dac630d962add1afac0b4cfbacc57e02704642f`.

Evidence:

- Exact-head Dank Shield CI passed.
- Exact-head Application Command Size Diagnostics passed.
- Exact-head Profile Runtime Diagnostics passed.
- Permanent focused claim-first security job passed.
- Discloud accepted the merged production commit successfully.
- No post-merge failure notification was found.

A human Discord smoke remains useful for visual confirmation of permission overwrites and user-facing responses, but the implementation, CI, merge, and deployment gates are complete.

## Current problem

Ticket numbers are permanent production identifiers. They must not reset to `#0001` when ticket channels are deleted, archived, renamed, or moved.

The Python runtime already uses a persistent allocator and refuses unsafe Discord-only fallback, but the underlying `ticket_counters` table and `reserve_ticket_number(text)` RPC were still partly owned by optional startup bootstrap instead of the permanent Supabase migration chain.

## Required behavior

- One durable counter per guild.
- Historical ticket rows seed or raise the counter.
- Existing counters are never lowered.
- Number reservation is atomic.
- Deleted Discord channels cannot cause number reuse.
- A historical `#0218` continues at `#0219`, then `#0220`.
- Fresh guilds begin at `#0001`.
- Only the Supabase service role can read/write the counter or execute the reservation RPC.
- Duplicate historical rows are preserved for audit; migrations never delete history.

## Implemented

- Added `supabase/migrations/20260731141000_ticket_counter_durability.sql`.
- Added permanent table/RPC ownership to the Supabase migration chain.
- Added upward-only history backfill.
- Added RLS, privilege revocation, and service-role grants.
- Added safe historical indexes and conditional uniqueness enforcement.
- Added `.github/workflows/ticket-counter-sql.yml` using PostgreSQL 16.
- Added `tests/test_ticket_counter_migration.py`.

## Validation

- [x] Migration applies twice without error.
- [x] Historical `218` backfills correctly.
- [x] First reservation returns `219`.
- [x] Second reservation returns `220`.
- [x] Fresh guild reservation returns `1`.
- [x] Migration reruns do not lower a reserved counter.
- [x] RLS is enabled.
- [x] `anon` and `authenticated` cannot execute the RPC.
- [x] `service_role` can execute the RPC.
- [x] Claim-first security CI passed on the branch.
- [x] Managed category PostgreSQL smoke test passed.
- [x] Application Command Size Diagnostics passed.
- [ ] Exact-head full Dank Shield CI passes.
- [ ] Review feedback is clear.
- [ ] PR #157 merges with the tested head SHA.
- [ ] Automatic production Supabase migration run succeeds.
- [ ] Final linked `supabase db push --dry-run` reports production up to date.

## Single Active Task Lock

Do not begin an unrelated repair until PR #157 is merged and the production migration is verified.
