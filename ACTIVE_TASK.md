# ACTIVE TASK

## DS-TICKETS-017 — Explicit ticket category setup and existing-guild repair

**Status:** IMPLEMENTED — EXACT-HEAD CI PENDING
**Branch:** `fix/ticket-category-setup-selection`
**Pull request:** `#163`

## Scope

Repair the user-facing Create Ticket category menu and setup lifecycle so:

- duplicate category labels cannot appear,
- the full managed catalog remains available without enabling every option,
- ticket-enabled setup includes an explicit category-selection step,
- existing guilds with the old excessive/duplicate managed setup are forced to review the new selection,
- owner-created custom categories survive migration,
- every live picker and routing path uses the same enabled-only category truth.

## Root cause findings

1. The old managed catalog migration enabled every built-in row for every guild.
2. The legacy picker could layer the full default catalog over old managed starter rows.
3. The native ticket panel could bootstrap the full catalog into an empty guild.
4. Setup's recommended-category action treated catalog availability as live enablement.
5. Separate COD Services and Game Services startup guards mutated the same catalog, setup, intake, and form sources.
6. Different picker paths used different canonicalization and duplicate rules.
7. Existing guilds had no durable version/required marker proving that an owner had reviewed the managed category set.

## Implemented changes

- Added `managed_category_service.py` as the canonical catalog, alias, enabled-state, dedupe, migration-state, and selection service.
- Added a 16-option managed catalog while keeping only **Report a Member**, **Appeal**, and **Support** as the safe temporary starter.
- Added exact canonical deduplication that preserves unknown custom rows.
- Added a setup multi-select for managed choices and a **Use Custom Choices Only** path.
- Connected the category selector to the original guided `/dank setup` flow whenever Tickets are enabled.
- Added a setup-required blocker and invalidated previous setup completion until an admin saves the new choice.
- Added migration `20260802042000_ticket_category_setup_selection.sql` for existing and new guilds.
- Existing bad all-enabled managed menus are reduced to the safe starter and forced into setup review.
- Existing custom menus keep their custom rows and default while managed choices remain off until explicitly selected.
- New guilds receive the full disabled catalog, the safe starter, and a setup-required marker.
- Added automatic direct-DSN startup registration so the committed migration is applied by the existing schema bootstrap on deployment.
- Patched the clean panel, legacy compatibility panel, native ticket router, setup manager, and form/intake paths to use one category truth.
- Made COD Services and Game Services distinct native managed templates with distinct forms.

## Cleanup and conflict inspection

- Deleted obsolete `ticket_category_cod_services_guard.py`.
- Deleted obsolete `ticket_category_game_services_guard.py`.
- Removed both obsolete guards from startup registration.
- Folded intermediate SQL drafts into one final timestamped migration.
- Removed invalid/duplicate migration filenames.
- Kept ticket-number allocation and the Create Ticket single-owner guard untouched.
- PR changed files are limited to ticket category setup, migration, tests, audits, and this task record.
- PR is currently mergeable with no unresolved review threads.

## Validation

- [x] Focused Ticket Category Menu Sanity workflow passes on a prior exact head.
- [x] Ticket Category Selection SQL workflow passes, including idempotent double application.
- [x] Existing all-enabled guild repair is covered by PostgreSQL smoke tests.
- [x] Existing custom-menu preservation and custom-only confirmation are covered by PostgreSQL smoke tests.
- [x] New-guild safe starter and setup-required trigger are covered by PostgreSQL smoke tests.
- [x] Guided setup routing into the category selector is behavior-tested.
- [x] Member-visible exact deduplication and enabled-only filtering are behavior-tested.
- [x] Automatic migration registration is behavior-tested.
- [x] COD/Game Services form separation is behavior-tested.
- [ ] Latest exact-head Ticket Category Menu Sanity passes.
- [ ] Latest exact-head Ticket Category Selection SQL passes.
- [ ] Latest exact-head full Dank Shield CI passes.
- [ ] Latest exact-head Application Command Size Diagnostics passes.
- [ ] Latest exact-head Profile Runtime Diagnostics passes.
- [ ] Review feedback remains fully resolved on the final head.
- [ ] PR merges using the exact tested head SHA.
- [ ] Production deployment/restart applies the migration.
- [ ] Live setup and Create Ticket menu are verified after deployment.

## Current blockers

- Waiting for the final exact-head GitHub Actions run after regression and task-record updates.
- Production/live verification cannot occur until the tested PR is merged and the Discloud process deploys or restarts.

## Backlog

No unrelated implementation work is active. Any new issue remains queued until DS-TICKETS-017 passes its Definition of Done.

## Previous completed task

DS-TICKETS-016 made the Create Ticket interaction single-owner, preserved native category/number ownership, passed all checks, and merged as `c522a93f55e34e61b515e45df7f6e7da0c94c880`.

## Single Active Task Lock

Do not begin another unrelated repair until DS-TICKETS-017 is merged, deployed, and live-verified.
