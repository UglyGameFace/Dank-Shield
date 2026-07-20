# ACTIVE TASK

## DS-CONFIG-HISTORY-001 — Native configuration backup + version history

**Status:** IN PROGRESS — CORE VALIDATION + SETUP UI PENDING
**Branch:** `feature/config-backup-version-history`
**Base:** current `main` after merged PR #106

## Single Active Task Lock

Do not switch to unrelated implementation work until this task reaches Definition of Done or the owner explicitly force-switches tasks.

## Hard Architecture Rules

- No monkey patches.
- No startup guards as the implementation path.
- Do not reactivate the dormant startup-guard loader.
- No new parallel configuration source of truth.
- `guild_configs` remains the live authoritative configuration.
- Backups/history live in a separate durable history table.
- Restore must never cross guild boundaries.
- Restore must preserve the current state first so rollback is reversible.
- Behavioral tests only; no new source-shape/static tests.
- Dashboard work is out of scope for this task.

## Scope

Build the first production configuration recovery layer:

- automatic configuration snapshots after successful guild config changes;
- durable per-guild version history;
- bounded retention so history cannot grow forever;
- one-click/manual backup support;
- safe restore of a selected historical version;
- automatic pre-restore safety backup;
- restore audit metadata and cache invalidation;
- owner-facing history/restore UI inside the existing `/dank setup` flow;
- no new top-level public command family.

## Root Cause / Gap

Dank Shield stores live per-guild configuration in Supabase, but there is no durable revision history. A bad setup change, accidental overwrite, or destructive configuration edit currently has no native rollback path.

The repository also has more than one legitimate native config writer. Instrumenting one Python writer would leave gaps. Automatic versioning therefore belongs at the database boundary, where every successful row change can be captured regardless of which native feature performed the write.

## Implementation So Far

- Added `supabase/migrations/20260720_guild_config_version_history.sql`.
- Added `public.guild_config_versions` with RLS enabled and no public policies.
- Added an automatic trigger for canonical `guild_configs` and legacy `guild_config` tables when present.
- Trigger ignores timestamp-only changes.
- Trigger dedupes only consecutive identical snapshots, so changing away from a state and later returning to it still creates a real history event.
- Trigger retains the newest 50 versions per guild.
- Added `stoney_verify/config_history.py` as the native bot-side history/restore owner.
- Added manual backup support.
- Added per-guild history list/get APIs.
- Added restore with:
  - guild ownership validation;
  - pre-restore safety backup;
  - schema-tolerant field restore;
  - lifecycle/system field exclusion;
  - write-audit metadata;
  - guild config cache invalidation.
- Added functional config diff helper that ignores write-audit metadata.
- Added behavioral tests for manual backup, functional diffing, safe restore, pre-restore backup, and cross-guild refusal.

## Validation

Pending:

- First GitHub Actions pass on the core implementation.
- Migration review for SQL correctness/idempotency.
- Owner-facing `/dank setup` Backups & History UI.
- Explicit restore confirmation UX.
- Full regression/compile/audits on final head.
- Final diff/conflict inspection.
- Final review-thread inspection.

## Cleanup / Conflict Inspection

- No startup guard added.
- No monkey patch added.
- No config-writer patch added.
- Existing `guild_configs` remains authoritative.
- New history table stores snapshots only; it does not become a runtime config source.
- Dashboard repo is untouched.

## Blockers

None known yet. Core CI and setup UI remain.

## Backlog After This Task

1. Reusable configuration templates.
2. Multi-server configuration sync.
3. Cross-server analytics.
4. Global moderation / shared security profiles.

## Definition of Done

- [x] Separate durable config history table exists via migration.
- [x] Successful config changes are automatically versioned at the DB boundary.
- [x] Consecutive duplicate/timestamp-only noise is suppressed without hiding legitimate returns to an older state.
- [x] History retention is bounded per guild.
- [x] Manual backup API exists.
- [x] Restore refuses cross-guild snapshots.
- [x] Restore creates a pre-restore safety backup.
- [x] Restore excludes lifecycle/system identity fields and invalidates runtime cache.
- [x] Behavioral core tests exist.
- [ ] Core compile/tests/audits pass.
- [ ] Existing `/dank setup` exposes Backups & History without a new top-level command.
- [ ] Restore requires explicit confirmation.
- [ ] Final regression/compile/audits pass on final head.
- [ ] Final diff contains only task-related permanent code/tests/task record.
- [ ] Final review-thread inspection is clean.
- [ ] Merge/deploy requires explicit user approval.
