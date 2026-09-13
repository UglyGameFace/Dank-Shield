# ACTIVE TASK

## DS-CLEANUP-001 — Canonical schema authority consolidation

**Status:** IMPLEMENTATION COMPLETE — validated; final record-only head must remain green before merge
**Branch:** `cleanup/schema-authority-consolidation`
**Base:** `91bce1e3a72b16e079f99febb6519b3144c36955` (`main`)
**PR:** #205

## Outcome

Committed files under `supabase/migrations/` are now the schema-mutation authority for this area. Runtime startup performs read-only readiness checks and migration guidance only.

## Root cause

Schema ownership was split across committed migrations, runtime DDL in startup guards, import-time migration registration, a standalone SQL file, and direct-database repair guidance. That made fresh installs, upgrades, and diagnostics depend on different authorities.

## Changes

- converted `auto_schema_bootstrap.py` and `operation_queue_schema_guard.py` to read-only diagnostics;
- neutralized import-time mutation in `ticket_category_schema_bootstrap_guard.py`;
- added `20260913154500_canonical_runtime_schema_authority.sql`;
- added `20260913160000_reconcile_guild_member_role_state_constraint.sql`;
- removed obsolete `supabase/2026-05-08_runtime_stability_schema.sql`;
- removed the runtime `psycopg` dependency and obsolete direct-database bootstrap guidance;
- updated ticket-panel and doctor guidance to committed-migration-only repair instructions;
- moved affected schema assertions to migration-owned tests;
- added Schema Authority SQL CI for fresh, replay, legacy-partial, duplicate-ticket, and role-state compatibility paths;
- corrected stale ticket guardrail tests to point at the canonical migration owner.

## Validation

Validated implementation head: `b94950beb09ae84d6a2c6b2184991e9bfb41301a`.

All 13 applicable PR workflows completed successfully on that exact implementation head, including Dank Shield CI, Schema Authority SQL, Ticket Counter SQL, Ticket Panel Single Owner, Ticket Panel Doctor Sanity, Ticket Category Menu Sanity, Ticket Category Repair SQL, Ticket Category Rich Recovery SQL, Ticket Owner Emergency Override, Application Command Size Diagnostics, Profile Runtime Diagnostics, DS Backlog 027 Validation, and Dank Design Regression CI.

The prior full-suite attempt produced `1367 passed / 2 failed`; both failures were stale test-ownership assertions. After those assertions were pointed at the canonical migration, the exact implementation head completed green.

This commit changes only this task record. No runtime code, migration SQL, test behavior, dependency, or workflow logic changes here. The resulting record-only head must still finish green before PR #205 is marked ready.

## Final scope / conflicts

- final implementation diff before this record update was 19 task-scoped files;
- no unresolved review threads;
- branch was 0 commits behind the inspected base;
- AntiNuke and unrelated product behavior were untouched;
- broader startup monkey-patch cleanup remains a separate Phase 2 task;
- `member_joins` re-invite semantics remain backlogged rather than being silently redesigned here.

## Deployment note

Production migration remains an explicit deployment step through the existing Supabase migration pipeline. Runtime no longer applies schema changes itself. The earlier hosted Supabase Preview comment predates the later migration additions, so repository SQL/CI validation is the current evidence for this PR.

## Next step

Let the record-only final head complete CI. If green, update PR #205 validation notes and mark it ready for review. Merge remains a separate explicit action.
