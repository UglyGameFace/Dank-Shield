# Dank Shield: staff metrics parity blocker (2026-10-20)

**New dedicated Supabase project only:** `viamriafkbqhquvrsnua`; existing old Supabase/Discloud runtime untouched. This is a scoped continuation of the sole active migration.

## Why

`stoney_verify/workers/metrics_sync_worker.py::_sync_staff_metrics` writes to `staff_metrics` with `on_conflict="guild_id,staff_id"`, while `stoney_verify/tickets_new/workspace_service.py` reads its staff ticket KPI columns. The dedicated candidate did **not** contain this table; worker writes and staff ticket views would fail or degrade after a premature cutover.

## New contract

`202610200014_dankshield_staff_metrics.sql` installs the minimal current runtime read/write columns and tenant-scoped unique key. Nonnegative ticket statistics, activity timestamp, indexed per-guild lookup, RLS and service-role-only privileges. The bot code and its 5-minute worker cadence remain unchanged, preserving 5–30-guild baseline behavior. This **does not make metrics worker 500K ready**; later replace unnecessary full resyncs with event-driven changes.

Rollback-only `tests/staff_metrics_smoke.sql` verifies 30 staff within one guild, same staff ID in a distinct guild, conflict update, staff reporting KPI persistence, RLS and grants. No synthetic IDs persist after the test.

## Remaining blocker: original source parity

The old/shared source `staff_metrics` schema and row data have **not** been directly inspected from Supabase. This new contract is derived from both live Python consumers, not a claim that original database columns and historical statistics are fully migrated. Inspect the stored Aiven staging/source snapshot for exact source columns and keys, verify any existing row digests, import idempotently and prove independent guild-level parity before production migration. Never replace historical staff work metrics with zeros.

Additional missing moderation, invite shielding, Community Hub, Cinema and profile tables/RPCs remain. Complete these and source delta/canary/rollback gates before changing Discloud credentials.

Branch hygiene: use only this one active `migration/dankshield-*` PR branch; after owner merge, the new exact-merged-head workflow can retire it overnight. No backup or production heads deleted.
