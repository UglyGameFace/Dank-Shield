# ACTIVE TASK

## DS-LIVE-STATS-002 — Make quarantine biohazard label permanent

**Status:** IN PROGRESS — VALIDATION PENDING
**Branch:** `fix/quarantine-biohazard-stat-label`
**Base:** current `main` after merged PR #103

## Single Active Task Lock

Do not switch to unrelated implementation work until this task reaches Definition of Done or the owner explicitly force-switches tasks.

## Scope

- Make the live quarantine stat use `☣️ Quarantined` permanently.
- Preserve all other live stats behavior from merged PR #103.
- Ensure an existing saved `🔒 Quarantined` channel is renamed to the new canonical biohazard label on refresh.
- Do not create a duplicate quarantine stats channel.

## Root Cause

The quarantine channel name was still generated from the hardcoded canonical label `🔒 Quarantined`. A manual Discord rename to `☣️ Quarantined` would therefore be reverted by the next normal stats refresh.

## Changes

- Changed the canonical quarantine stat prefix to `☣️ Quarantined:`.
- Changed generated quarantine channel names to use the biohazard emoji.
- Updated behavioral display expectations.
- Kept an old `🔒 Quarantined` channel in the migration test and now verifies refresh converts it to `☣️ Quarantined` rather than leaving the old label.

## Validation

Pending:

- Committed diff whitespace check.
- Python compile.
- Full `pytest tests/` suite.
- Standalone tool checks.
- Production audits.
- Final diff and review-thread inspection.
- Final-head GitHub Actions.

## Cleanup / Conflict Inspection

- Reuses the existing `security_stats.py` owner.
- No new stats subsystem.
- No new counters or database fields.
- No changes to ticket/member logic.
- `main.py`, `sitecustomize.py`, and `usercustomize.py` remain untouched.

## Blockers

None known yet. Validation is still required.

## Definition of Done

- [x] Canonical quarantine label is `☣️ Quarantined`.
- [x] New/fresh stats displays use the biohazard label.
- [x] Existing saved old-lock quarantine channels migrate on refresh.
- [x] No duplicate quarantine channel is created when the saved channel ID is valid.
- [ ] Full regression/compile/audits pass.
- [ ] Final diff contains only task-related files.
- [ ] Final-head GitHub Actions pass.
- [ ] Merge/deploy requires explicit user approval.
