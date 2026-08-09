# ACTIVE TASK

## DS-PURGE-025 — Restore direct Dank Shield purge commands

**Status:** IN PROGRESS — IMPLEMENTED, VALIDATING
**Branch:** `fix/ds-purge-025-restore-direct-purge`
**Base:** `main` at `7c30736e941bf2fd9a9390d9719acfa59990e0fa`
**PR:** #179 (draft until exact-head validation is green)
**Started:** 2026-08-09

## Scope

Restore obvious Discord slash-command access to Dank Shield's two existing purge systems without reintroducing the large legacy command tree or creating duplicate destructive logic.

Target direct surface:

- `/dank purge messages` — channel purge plus preview/confirmed user-message purge, including raw user IDs and whole-server scope.
- `/dank purge members` — strict inactive verified/resident member purge with fresh evidence scan and final confirmation.

The newer menu paths remain available and must use the same canonical engines.

## Root cause findings

1. Inactive-member purge was originally exposed as `/dank members purge-all`; the implementation still exists in `public_members_cleanup_group.py`.
2. Message/user purge still exists in `public_cleanup_group.py` as the canonical `cleanup_purge` handler.
3. PR #144 intentionally replaced visible `/dank members ...` children with the Member Command Center, burying `purge-all` behind Activity & Cleanup → Purge Eligible.
4. PR #178 intentionally reduced final `/dank` children to only `home` and `upload`; startup compaction therefore removes `/dank cleanup`, including its visible purge command.
5. The pre-sync cleanup guard consumes `PUBLIC_DANK_CHILDREN`, so adding an unapproved shortcut anywhere else would be deleted before Discord sync.
6. The correct repair is one approved compact `/dank purge` facade, installed after final compaction and delegated to the existing canonical purge handlers.

## Changes

- [x] Add `public_direct_purge.py` with only two thin routes: `messages` and `members`.
- [x] Delegate message purge to canonical `public_cleanup_group.cleanup_purge`.
- [x] Delegate inactive-member purge to canonical `public_members_cleanup_group.members_purge_all`.
- [x] Reinstall `/dank purge` after every final command-surface compaction/reassertion.
- [x] Re-check final `/dank` payload and fail closed if the restored family exceeds the existing safety limit.
- [x] Update canonical `/dank` child contract to `home`, `purge`, `upload` so pre-sync cleanup preserves it.
- [x] Add regression coverage proving final reassertion preserves purge and the facade does not duplicate deletion/scanning engines.
- [x] Update payload and public-command audits for the approved purge exception.
- [x] Update the pre-existing live command-tree regression to recognize the intentional `purge` child.

## Validation

- [ ] Targeted direct-purge tests — covered by exact-head full suite; rerun pending after stale expectation fix.
- [ ] Command-surface reassertion tests — covered by exact-head full suite; rerun pending after stale expectation fix.
- [x] Application Command Size Diagnostics run 625 passed on implementation head; runtime snapshot measured final `/dank` payload at 2825/7600.
- [ ] `/dank` payload standalone diagnostic — exact-head CI rerun pending.
- [ ] Public command-surface audit — exact-head CI rerun pending.
- [ ] Public command-friction audit — exact-head CI rerun pending.
- [x] Python compile/static validation passed on implementation head.
- [ ] Full repository unit suite — first run found exactly one stale test expectation (`tests/test_welcome_card_live_command_tree.py` expected only `home`,`upload`); 1014 tests passed and that expectation is now corrected. Exact-head rerun pending.
- [ ] Standalone repository checks required by CI — exact-head CI rerun pending.
- [x] Initial branch diff/conflict inspection: PR is mergeable, based on current `main`, with no review threads or requested changes.
- [ ] Final exact-head diff/conflict/duplicate-implementation inspection after CI rerun.

## Cleanup status

- No second message-deletion implementation added.
- No second inactivity scanner/member-removal implementation added.
- Legacy `/dank cleanup` and `/dank members` groups remain hidden by the compact surface; only the small purge facade is restored.
- Menu access remains intact for users who prefer guided controls.
- No temporary patch/applier/workflow files were added.

## Blockers

None known. Exact-head CI rerun is required after correcting the one stale command-tree test expectation.

## Backlog

None added from this conversation.

## Definition of Done

DS-PURGE-025 is complete only when `/dank purge messages` and `/dank purge members` survive final compaction and pre-sync cleanup, both delegate to the existing canonical purge engines with their original permission/preview/confirmation/safety behavior, the final command payload remains under the repository safety limit, targeted and regression tests pass, compile/static/full-suite/standalone audits are green, and final conflict/duplicate/dead-code inspection is clean. Merge/deploy are separate actions and require explicit authorization.
