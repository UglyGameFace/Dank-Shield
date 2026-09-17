# ACTIVE TASK

## DS-SEC-CHAN-UPDATE-FALSE-POSITIVE — Stop benign channel edits from triggering AntiNuke

**Outcome target:** Ordinary Discord channel edits such as renaming a channel must remain normal logging activity and must not enter Dank Shield's destructive AntiNuke/owner-compromise path. Explicit permission-overwrite mutations and genuinely destructive structural actions must remain protected.

**Status:** FINAL EXACT-HEAD VALIDATION

**Branch:** `fix/antinuke-benign-channel-update`
**Base main:** `98290563f7244af198c4384a595cb66cdc76e0e3`
**PR:** #252
**Previous integrated task:** PR #250 merged at `98290563f7244af198c4384a595cb66cdc76e0e3`; its exact PR head workflows passed and the merge commit became current `main`, releasing the previous contextual-repair lock.

## User-reported production regression

Renaming an ordinary channel triggered:

- `🚨 AntiNuke Owner-Compromise Warning`
- detected action `Channel settings mutation`
- threshold `5s window • channel_update 1/1`

The guild owner had only renamed a channel. No permission overwrite, deletion, or other destructive action occurred.

## Root cause

1. `anti_nuke_guardian_runtime._ACTIONS` classified the generic Discord `channel_update` audit action as destructive AntiNuke evidence and mapped it to the canonical `channel_update` counter.
2. Discord uses generic `channel_update` for ordinary channel-setting edits such as names/topics, while permission overwrite changes have distinct `overwrite_create`, `overwrite_update`, and `overwrite_delete` audit actions that Dank Shield already handles separately.
3. The guardian audit listener therefore routed a benign rename through `_process()` into the canonical destructive-event engine.
4. `anti_nuke_lockdown_runtime` intentionally forces the owner-compromise path to first strike, so the misclassified event became `channel_update 1/1` and immediately produced the warning.
5. Raising thresholds or exempting the guild owner would weaken real protection and would treat the symptom rather than the classifier bug.

## Execution path

`on_audit_log_entry_create`
→ guardian action-name classification
→ generic `channel_update` lookup in `_ACTIONS`
→ `_process(...)`
→ `anti_nuke._process_claimed_destructive_event(...)`
→ owner-compromise wrapper
→ owner first-strike override
→ incident post

The native channel-update fallback is not the live cause: the production gateway runtime retires the old generic overwrite listener, and the guardian gateway fallback already requires an actual overwrite difference and resolves only explicit overwrite audit actions.

## Changes

- removed generic `channel_update` from the guardian destructive audit map
- retained explicit `overwrite_create`, `overwrite_update`, and `overwrite_delete` protection unchanged
- retained the canonical `channel_update` counter/slow-burn key because real overwrite and protected guild-update paths intentionally reuse it
- retained owner first-strike behavior for genuinely protected actions
- added `tests/test_antinuke_channel_update_false_positive.py` covering benign generic channel updates, explicit overwrite enforcement, and preservation of the shared overwrite counter
- no threshold changes, owner whitelist, trust broadening, new runtime patch, duplicate enforcement path, or unrelated logging changes

## Validation results

Implementation head `623e87274eab1f72e373e87459b01334b6056d88` passed the first full validation wave:

- committed-diff whitespace check: PASS
- Python compile: PASS
- full unit test suite: PASS, including the new channel-update regressions
- standalone tool checks: PASS
- public setup/command/invite/safety/design/role/event-boundary audits: PASS
- required `Claim-first ticket security`: PASS
- required `Managed category SQL smoke test`: PASS
- `Application Command Size Diagnostics`: PASS
- `Dank Design Regression CI`: PASS
- `Ticket Owner Emergency Override`: PASS
- `Profile Runtime Diagnostics`: PASS
- `Dank Shield CI`: PASS
- PR #252 diff inspected: exactly 3 files (`ACTIVE_TASK.md`, guardian classifier, focused regression file); no unrelated runtime changes
- PR #252 is mergeable; only automated Supabase comment states the PR has no `supabase` directory changes
- current `main` remained `98290563f7244af198c4384a595cb66cdc76e0e3`, so the implementation wave was 0 behind base

This bookkeeping commit changes the PR head, so all required checks must pass again on the exact new head before merge.

## Cleanup / conflict inspection

- generic `channel_update` no longer has a destructive guardian action spec
- explicit overwrite audit actions remain the authoritative permission-overwrite path
- canonical `channel_update` counter remains available for real overwrite/security activity
- owner-compromise first-strike logic remains intact for genuinely protected actions
- no threshold workaround or duplicate compatibility shim was introduced
- no unrelated production code was touched

## Validation gate remaining

- every triggered workflow succeeds on this exact final bookkeeping head
- final branch remains 0 behind current `main`
- PR remains mergeable with no unresolved review/thread issue
- merge only this exact validated head
- verify resulting merge commit is current `main`
- verify post-merge CI/deployment/status checks before releasing the task lock

## Backlog after this regression closes

1. Protection contextual repair adoption
2. remaining VC-specific repair cleanup
3. Embed / Status contextual repair adoption
4. admin-only `/dank tickettool-check` contextual repair adoption
5. `/dank protection` remaining non-invite picker/guard cleanup
6. `/dank design` picker migration
7. admin-only legacy setup picker cleanup

## Next step

Run the exact-final-head validation wave created by this bookkeeping update. If every required and companion workflow is green, mark PR #252 ready, merge only that verified SHA, then verify merged `main` and deployment/status checks before releasing this task lock.
