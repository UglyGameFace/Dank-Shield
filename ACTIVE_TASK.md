# ACTIVE TASK

## DS-SPAMGUARD-STARTUP-001 — Make SpamGuard default/startup truth live in production

**Status:** IN PROGRESS — VALIDATION PENDING  
**Branch:** `fix/spamguard-live-startup-state-reporting`  
**Base:** current `main` after merged PR #99 / PR #101

## Single Active Task Lock

Do not switch to unrelated implementation work until this task reaches Definition of Done or the owner explicitly force-switches tasks.

## Scope

- Keep SpamGuard enabled by default for brand-new guilds and missing `guild_security_settings` rows.
- Preserve explicit persisted enabled/disabled owner choices.
- Keep database read failures distinct from a legitimate default-enabled state.
- Ensure the four startup labels actually run on the real production boot path:
  - `DEFAULT ENABLED`
  - `PERSISTED ENABLED`
  - `PERSISTED DISABLED`
  - `DATABASE LOAD ERROR`
- Replace SpamGuard source-shape regression checks with behavioral coverage.
- Do not enable the dormant bulk startup-guard loader.
- Do not touch `main.py`, `sitecustomize.py`, or `usercustomize.py`.

## Root Cause

PR #101 correctly introduced the authoritative `SPAM_GUARD_DEFAULT_ENABLED = True` policy and correct persistence behavior, but its startup-state reporter lived only in `startup_guards/spam_guard_default_state_guard.py` and was listed in `_STARTUP_GUARDS`.

The repository boot contract explicitly states that the bulk startup-guard loader is dormant, and `main.py` does not call `load_all_startup_guards()`. Therefore the reporter's `on_ready` listener was not guaranteed to be registered in production even though its label-unit tests passed.

The same PR also added `tests/test_spam_guard_default_on_bootstrap_static.py`, which reads source files and asserts code shape despite the repository rule requiring behavioral tests.

## Execution Path Confirmed

`main.py` → `stoney_verify.app` → `stoney_verify.events` → `stoney_verify.spam_guard` → `stoney_verify.spam_guard_defaults`

That makes the SpamGuard defaults import path a guaranteed live SpamGuard runtime path without enabling the dormant guard registry.

## Changes

- Added `stoney_verify/spam_guard_startup_state.py` as the native SpamGuard-owned startup-state reporter.
- Wired reporter registration through the authoritative SpamGuard defaults import path used by the live runtime.
- Converted `startup_guards/spam_guard_default_state_guard.py` into a compatibility shim so legacy imports keep working without registering a duplicate listener.
- Added warm-cache provenance handling so an already-cached persisted OFF state is not mislabeled on startup.
- Expanded behavioral tests for:
  - all four startup labels;
  - actual `bot` `on_ready` listener registration;
  - once-per-guild/process reporting;
  - warm persisted OFF cache classification;
  - runtime/setup default-enabled behavior;
  - explicit disabled behavior;
  - normal plain setup choices selecting SpamGuard by default.
- Removed `tests/test_spam_guard_default_on_bootstrap_static.py` and replaced it with behavioral coverage.

## Validation

Pending:

- Python compile validation.
- Targeted SpamGuard tests.
- Full `pytest tests/` regression suite.
- Standalone tools/audits required by CI.
- `git diff --check` equivalent / final diff inspection.
- GitHub Actions on the PR head.

## Cleanup / Conflict Inspection

- No new startup guard was added.
- Dormant `load_all_startup_guards()` remains dormant.
- No monkey patch was added.
- `main.py` unchanged.
- `sitecustomize.py` unchanged.
- `usercustomize.py` unchanged.
- Old startup-guard module is retained only as a compatibility import shim.
- Source-shape SpamGuard bootstrap test removed rather than duplicated.

## Blockers

None known yet. Validation is still required before this task can be called complete.

## Backlog

Locked while this task is active. Healthchecks heartbeat timing was configured separately at 60 seconds and is not part of this code change.

## Definition of Done

- [x] Authoritative SpamGuard product default remains ON.
- [x] Missing/new settings rows retain default-enabled behavior.
- [x] Explicit persisted OFF remains OFF.
- [x] Startup reporter is attached through a real production import path.
- [x] Four required startup-state labels remain behaviorally covered.
- [x] Warm persisted cache cannot mislabel explicit OFF as a default/error state.
- [x] No duplicate startup listener is introduced by the compatibility guard.
- [x] Static SpamGuard bootstrap source-shape test removed/replaced behaviorally.
- [ ] Targeted tests pass.
- [ ] Full regression/compile/audits pass.
- [ ] Final diff contains only task-related permanent code/tests/task record.
- [ ] GitHub Actions pass on final PR head.
