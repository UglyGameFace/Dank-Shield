# ACTIVE TASK

## DS-CONFIG-HISTORY-001 — Native configuration backup + version history

**Status:** IN PROGRESS — DATABASE PREVIEW VALIDATION PENDING
**Branch:** `feature/config-backup-version-history`
**PR:** #107 (draft)
**Base:** `main` after merged PR #106

## Single Active Task Lock

Do not switch to unrelated implementation work until this task reaches Definition of Done or the owner explicitly force-switches tasks.

## Hard Architecture Rules

- No monkey patches.
- No startup guards as the implementation path.
- Do not reactivate the dormant startup-guard loader.
- No parallel live configuration source of truth.
- Existing live tables remain authoritative; history stores snapshots only.
- Restore must never cross guild boundaries or canonical/legacy config-table boundaries.
- Every restore must preserve the current state first so rollback remains reversible.
- Ticket-choice restore must be atomic; a partial ticket-menu restore is unacceptable.
- Behavioral tests only; no new source-shape/static tests.
- Dashboard work is out of scope.
- Merge and production migration/deployment require explicit owner approval.

## Scope

Build the first production configuration recovery layer for the active bot configuration domains:

1. **Core Settings**
   - canonical `guild_configs` or the active legacy `guild_config` fallback;
   - feature settings, timers, saved Discord role/channel/category IDs, protection settings, and other guild config stored there.

2. **Ticket Choices**
   - active `ticket_categories` rows;
   - category-specific configuration stored on those rows, including compatible category-stored form configuration.

Deliver:

- automatic configuration snapshots after successful changes;
- durable per-guild version history;
- migration-time baselines for existing guilds;
- empty Ticket Choices baselines for configured guilds with no categories;
- bounded retention of the newest 50 versions per guild and configuration domain;
- manual backup sets covering Core Settings plus Ticket Choices when available;
- safe restore of one selected historical version;
- automatic pre-restore safety backup;
- explicit two-step restore confirmation;
- owner-facing Backups & History UI under `/dank setup` → More Options → Other Settings;
- no new top-level public command family.

This task does **not** claim to clone/recreate a Discord server or version every historical/dormant database table. Discord roles/channels are never deleted, recreated, or renamed by configuration restore.

## Root Cause / Gap

Dank Shield had durable live per-guild configuration in Supabase but no revision history. A bad setup edit, accidental overwrite, or destructive ticket-choice change had no native rollback path.

The repository has multiple legitimate native config write paths. Instrumenting only one Python writer would miss changes, so automatic history belongs at the database boundary.

A scope audit also confirmed that `guild_configs` is not the whole active configuration surface: ticket choices live in the separate guild-scoped `ticket_categories` table. The recovery feature therefore versions Core Settings and Ticket Choices as separate domains instead of falsely presenting a single-row backup as complete configuration recovery.

## Implementation

### Durable history migration

- Added `supabase/migrations/20260720_guild_config_version_history.sql`.
- Added `public.guild_config_versions` with RLS enabled and no public policies.
- Added `config_table` domain/source isolation.
- Added indexes for guild/domain/time history lookup.
- Added migration baselines for:
  - existing canonical `guild_configs` rows;
  - existing legacy `guild_config` rows;
  - existing `ticket_categories` sets;
  - configured guilds whose Ticket Choices are currently empty.
- Added functional snapshot normalization so timestamps and config-write audit metadata do not generate fake versions.
- Consecutive functional duplicates are suppressed without hiding a legitimate later return to an older configuration.
- Automatic history is retained to 50 versions per guild + configuration domain.

### Core Settings history

- Automatic DB trigger captures successful canonical or legacy guild config changes.
- Canonical and legacy histories cannot cross-restore.
- Restore validates guild ownership.
- Restore saves a pre-restore safety backup first.
- Restore excludes lifecycle/system identity fields.
- Restore adds write/restore audit metadata.
- Guild config cache is invalidated after restore.

### Ticket Choices history

- Automatic DB trigger snapshots the complete current `ticket_categories` set after category insert/update/delete.
- Functional dedupe ignores category row UUIDs and lifecycle timestamps.
- Snapshot bundles preserve compatible category-stored configuration fields dynamically.
- Added `restore_ticket_categories_snapshot(text, jsonb)` as a service-role-only database function.
- Ticket-choice restore is one atomic DB transaction:
  - validates guild IDs and slugs first;
  - suppresses only intermediate row-trigger history inside the restore transaction;
  - removes choices absent from the selected snapshot;
  - updates matching slugs without replacing their current stable IDs;
  - inserts missing historical choices using compatible table columns/defaults;
  - rolls back the entire mutation if any operation fails.
- Python writes one pre-restore safety snapshot and one clean restored-state history entry with actor/reason metadata.

### Bot service and UI

- Added `stoney_verify/config_history.py` as the native history/backup/restore owner.
- Added `stoney_verify/config_history_ui.py` as the owner-facing setup UI.
- Added **Backups & History** under the existing Other Settings hub while preserving the mobile two-components-per-row layout.
- History clearly labels versions as **Core Settings** or **Ticket Choices**.
- Manual **Create Backup** saves both available domains as one user action.
- Version detail shows functional differences from current state.
- **Restore This Version** is non-destructive and only opens confirmation.
- Only **Confirm Restore** invokes restore.
- UI explicitly states that restore does not delete/recreate/rename Discord roles or channels.

## Behavioral Coverage

- Core functional diff ignores write-audit metadata but detects real setting changes.
- Ticket-choice diff ignores UUID/timestamp noise but detects real category changes.
- Manual backup captures Core Settings and Ticket Choices.
- Core restore creates a safety backup, excludes system/lifecycle identity fields, adds restore metadata, and clears cache.
- Cross-guild core restore is refused.
- Cross-table core restore is refused.
- Ticket-choice snapshot rows from another guild are refused.
- Ticket-choice restore uses one atomic RPC with the complete saved row set rather than individual REST mutations.
- Ticket-choice restore writes pre-restore and final restored-state history records.
- UI labels configuration domains correctly.
- Restore detail → confirmation is required before the restore service runs.
- Confirmation UX remains mobile-compact and offers only Confirm Restore / Cancel.
- Setup Other Settings route behavior is covered.

## Validation

Completed on implementation heads:

- Dank Shield CI run 476: SUCCESS on initial core service/migration-adjacent Python.
- Dank Shield CI run 478: SUCCESS after native history UI addition.
- Exact setup-wiring head `50966e2404902a52a200c44112300f9a1786533e`:
  - Dank Shield CI 479: SUCCESS;
  - Setup Check Inference Sanity 124: SUCCESS.
- Hardened core/table-isolation head `9f6f8c91def74061d5f77238892bb1ea166b147c`:
  - Dank Shield CI 482: SUCCESS;
  - Setup Check Inference Sanity 127: SUCCESS.
- Ticket-choice domain extension head `b111c6d79484c3081cead95fac67967711f8eeaf`:
  - Dank Shield CI 487: SUCCESS;
  - Setup Check Inference Sanity 132: SUCCESS.
- Atomic ticket-choice restore head `050f81a95ada806688de2a0e785473b9bbaaeb0d`:
  - Dank Shield CI 490: SUCCESS;
  - Setup Check Inference Sanity 135: SUCCESS.
- PR #107 inline review threads: none.
- Compare against `main`: ahead 20, behind 0; exactly eight task-related paths before this task-record update.

Local Termux targeted tests remain blocked by the local environment's incompatible/broken `supabase` import (`cannot import name 'Client' from 'supabase'`). Clean GitHub CI does not reproduce that environment issue; production code is not being changed to work around it.

## Database Preview Validation

Supabase's first PR preview did **not** validate the migration. Its PR bot reported:

`failed to clone repo: to: invalid path: "\\"`

The bot also noted that modified existing migration files are not reapplied automatically. PR #107 was therefore safely closed and reopened while still draft/unmerged to force a fresh preview attempt against the current migration.

Current blocker: wait for and inspect the retriggered Supabase preview result. Do not mark ready for review until the current migration has either been validated by Supabase preview or the preview infrastructure failure is clearly isolated and an equivalent safe migration-validation path is completed.

## Cleanup / Conflict Inspection

- No startup guard added.
- No monkey patch added.
- No config-writer patch added.
- No dashboard changes.
- No new public command family.
- Existing live configuration tables remain authoritative.
- History table is snapshot/recovery storage only.
- Ticket-choice restore is atomic at the database mutation layer.
- Dormant startup-guard ticket-form code is not used by this implementation.

## Blockers

1. Re-triggered Supabase preview/migration validation result is pending.
2. Final task-record-only head must pass Dank Shield CI + Setup Check Inference.
3. Final diff/conflict/review-thread inspection must be repeated on the exact final head.
4. Merge and production migration/deployment require explicit owner approval.

## Backlog After This Task

1. Reusable configuration templates.
2. Multi-server configuration sync.
3. Cross-server analytics.
4. Global moderation / shared security profiles.

## Definition of Done

- [x] Separate durable history table exists via migration.
- [x] Core Settings changes are automatically versioned at the DB boundary.
- [x] Active Ticket Choices changes are automatically versioned at the DB boundary.
- [x] Existing guilds receive baseline history, including empty Ticket Choices where applicable.
- [x] Functional duplicate/audit-only noise is suppressed without hiding real returns to older states.
- [x] Retention is bounded per guild + domain.
- [x] Manual backup covers both available active configuration domains.
- [x] Core restore refuses cross-guild/cross-table snapshots.
- [x] Ticket-choice restore refuses cross-guild rows.
- [x] Every restore preserves the current state first.
- [x] Ticket-choice mutation restore is atomic.
- [x] Restore confirmation is explicitly two-step.
- [x] Existing `/dank setup` exposes Backups & History without a new top-level command.
- [x] Behavioral coverage exists for service, restore safety, domain labeling, and setup routing.
- [x] Full Python regression/compile/audits passed on the implementation head.
- [ ] Current Supabase migration preview (or equivalent safe DB validation) passes.
- [ ] Final task-record-only head GitHub CI + Setup Check Inference pass.
- [ ] Final diff contains only task-related permanent code/tests/task record.
- [ ] Final review-thread inspection is clean on the exact final head.
- [ ] Merge/deploy requires explicit owner approval.
