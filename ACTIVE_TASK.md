# ACTIVE TASK

## DS-TICKETS-015 — Make ticket numbering permanently durable

**Status:** COMPLETE — MERGED, DEPLOYED, AND VERIFIED IN PRODUCTION

## Production commits

- PR #157 — durable Supabase counter migration: `5e14bd27e2ae2c0d730e885b8825144a89f7db9c`
- PR #158 — bootstrap/migration single-source hotfix: `d24ce7e0f0c5c2da45ecee3bc3464dfbcfbbda62`

Discloud accepted both production commits successfully.

## Guarantees now enforced

- One durable counter per Discord guild.
- Historical ticket rows seed or raise the counter.
- Existing counters are never lowered.
- Number reservation is atomic and database-owned.
- Deleted, renamed, archived, or moved Discord channels cannot cause number reuse.
- Historical `#0218` continues at `#0219`, then `#0220`.
- Fresh guilds begin at `#0001`.
- Guild IDs are trimmed before counter ownership, preventing whitespace variants from creating separate counters.
- Only the Supabase service role can use the counter table/RPC.
- Runtime bootstrap executes the complete sorted counter-migration chain instead of redefining or downgrading the RPC.
- CI applies the complete chain twice so future migrations remain authoritative and replay-safe.

## Validation completed

- [x] Exact-head full Dank Shield CI passed.
- [x] Exact-head Application Command Size Diagnostics passed.
- [x] Ticket Counter SQL passed with PostgreSQL 16.
- [x] Claim-first security and managed-category SQL checks passed.
- [x] All Qodo correctness threads were resolved and outdated before merge.
- [x] Production migration history shows `20260731141000` on both local and remote sides.
- [x] Ordinary linked `supabase db push --dry-run` returned `Remote database is up to date.`
- [x] Production schema dump contains `ticket_counters` and the normalized `reserve_ticket_number(text)` RPC.
- [x] Temporary read-only production verifier was removed after proof capture.

## Previous task

DS-TICKETS-014 claim-first ticket handling was merged through PR #147 as production commit `7dac630d962add1afac0b4cfbacc57e02704642f` and accepted by Discloud.

## Single Active Task Lock

DS-TICKETS-015 is closed. The next unrelated repair may begin only after this cleanup PR is merged.
