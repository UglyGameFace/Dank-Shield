# ACTIVE TASK

## DS-LIVE-STATS-002 — Make quarantine biohazard label permanent

**Status:** READY FOR REVIEW — NOT MERGED — NOT DEPLOYED
**Branch:** `fix/quarantine-biohazard-stat-label`
**PR:** #104
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

Implementation head `a0d47f11ffd5693860789af79316accf23aac353` passed GitHub Actions `Dank Shield CI` run 461:

- Committed diff whitespace check: PASS.
- Python compile: PASS.
- Full `pytest tests/` suite: PASS, including the biohazard display and old-lock migration behavior.
- Standalone tool checks: PASS.
- Public setup/isolation audit: PASS.
- Canonical command-surface audit: PASS.
- Command/startup-friction audit: PASS.
- Public invite-permission audit: PASS.
- Setup-safety audit: PASS.
- Dank Design Smart Auto-Detect audit: PASS.
- Role-truth ownership audit: PASS.
- Event-boundary ownership audit: PASS.

The final task-record-only head created by this update must also remain green before merge approval.

## Cleanup / Conflict Inspection

- Reuses the existing `security_stats.py` owner.
- No new stats subsystem.
- No new counters or database fields.
- No changes to ticket/member logic.
- `main.py`, `sitecustomize.py`, and `usercustomize.py` remain untouched.
- Implementation diff is two changed lines in the stats owner plus focused behavioral test updates; task record is the only other changed file.

## Blockers

- No known technical blocker remains.
- Merge into `main` requires explicit user approval.
- Manual deployment is not authorized and has not occurred.

## Definition of Done

- [x] Canonical quarantine label is `☣️ Quarantined`.
- [x] New/fresh stats displays use the biohazard label.
- [x] Existing saved old-lock quarantine channels migrate on refresh.
- [x] No duplicate quarantine channel is created when the saved channel ID is valid.
- [x] Full regression/compile/audits pass on the implementation head.
- [x] Final diff contains only task-related files.
- [ ] Final-head GitHub Actions pass.
- [ ] Merge/deploy requires explicit user approval.
