# ACTIVE TASK

## DS-TICKETS-017 — Explicit ticket category setup and existing-guild repair

**Status:** IMPLEMENTED — FINAL EXACT-HEAD CI PENDING
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
7. The legacy TicketTool parity picker retained broad substring matching after the canonical managed-category service was added; `Bug Report` could therefore collapse into `report` and recreate a duplicate option.
8. Existing guilds had no durable version/required marker proving that an owner had reviewed the managed category set.

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
- Made the legacy TicketTool parity picker delegate category identity to `managed_category_service.canonical_category_key` instead of maintaining a second broad canonicalizer.
- Made COD Services and Game Services distinct native managed templates with distinct forms.

## Cleanup and conflict inspection

- Deleted obsolete `ticket_category_cod_services_guard.py`.
- Deleted obsolete `ticket_category_game_services_guard.py`.
- Removed both obsolete guards from startup registration.
- Removed the parity picker's redundant alias table and broad substring canonicalization rules.
- Folded intermediate SQL drafts into one final timestamped migration.
- Removed invalid/duplicate migration filenames.
- Kept ticket-number allocation and the Create Ticket single-owner guard untouched.
- PR changed files are limited to ticket category setup, migration, tests, audits, and this task record.
- Branch is 0 commits behind `main`.
- PR is mergeable with no unresolved review threads.

## Validation

- [x] Ticket Category Menu Sanity passed on implementation head `22b5568e190aadc9691a93edf84c9d87d5b4d0cb`.
- [x] Ticket Category Selection SQL passed on implementation head, including idempotent double application.
- [x] Application Command Size Diagnostics passed on implementation head.
- [x] Managed category SQL smoke test passed inside Dank Shield CI.
- [x] Claim-first ticket security regression suite passed inside Dank Shield CI.
- [x] Python compilation and committed-diff whitespace checks passed inside Dank Shield CI.
- [x] Existing all-enabled guild repair is covered by PostgreSQL smoke tests.
- [x] Existing custom-menu preservation and custom-only confirmation are covered by PostgreSQL smoke tests.
- [x] New-guild safe starter and setup-required trigger are covered by PostgreSQL smoke tests.
- [x] Guided setup routing into the category selector is behavior-tested.
- [x] Member-visible exact deduplication and enabled-only filtering are behavior-tested.
- [x] Automatic migration registration is behavior-tested.
- [x] COD/Game Services form separation is behavior-tested.
- [x] Review feedback is fully resolved on the implementation head.
- [ ] Final exact-head full Dank Shield CI passes after this task-record-only commit.
- [ ] Final exact-head Profile Runtime Diagnostics passes after this task-record-only commit.
- [ ] PR merges using the exact tested head SHA.
- [ ] Production deployment/restart applies the migration.
- [ ] Live setup and Create Ticket menu are verified after deployment.

## Current blockers

- The final exact-head GitHub Actions run must finish after this task-record-only commit.
- Production/live verification cannot occur until the tested PR is merged and the Discloud process deploys or restarts.

## Backlog

### DS-MEDIA-001 — Klipy direct GIF unfurl listener

After DS-TICKETS-017 reaches its Definition of Done, inspect the existing message-event ownership path and add one moderation-safe listener that recognizes verified `klipy.com` page URLs, resolves the page's direct GIF asset without arbitrary-host fetching, and replies with the direct media or a rich embed. Include bounded requests, redirect/host validation, caching, bot-loop prevention, permission/error handling, and focused listener/parser/security regressions.

No unrelated implementation work is active. DS-MEDIA-001 remains queued until DS-TICKETS-017 is merged, deployed, and live-verified, unless the user explicitly uses the documented force-switch instruction.

## Previous completed task

DS-TICKETS-016 made the Create Ticket interaction single-owner, preserved native category/number ownership, passed all checks, and merged as `c522a93f55e34e61b515e45df7f6e7da0c94c880`.

## Single Active Task Lock

Do not begin another unrelated repair until DS-TICKETS-017 is merged, deployed, and live-verified.
