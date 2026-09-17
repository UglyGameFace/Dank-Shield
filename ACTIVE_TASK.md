# ACTIVE TASK

## DS-SEC-CHAN-UPDATE-FALSE-POSITIVE — Stop benign channel edits from triggering AntiNuke

**Outcome target:** Ordinary Discord channel edits such as renaming a channel must remain normal logging activity and must not enter Dank Shield's destructive AntiNuke/owner-compromise path. Explicit permission-overwrite mutations and genuinely destructive structural actions must remain protected.

**Status:** IMPLEMENTATION / VALIDATION

**Branch:** `fix/antinuke-benign-channel-update`
**Base main:** `98290563f7244af198c4384a595cb66cdc76e0e3`
**Previous integrated task:** PR #250 merged at `98290563f7244af198c4384a595cb66cdc76e0e3`; its exact PR head workflows passed and the merge commit became current `main`, releasing the previous contextual-repair lock.

## User-reported production regression

Renaming an ordinary channel triggered:

- `🚨 AntiNuke Owner-Compromise Warning`
- detected action `Channel settings mutation`
- threshold `5s window • channel_update 1/1`

The guild owner had only renamed a channel. No permission overwrite, deletion, or other destructive action occurred.

## Root cause

1. `anti_nuke_guardian_runtime._ACTIONS` classifies the generic Discord `channel_update` audit action as destructive AntiNuke evidence and maps it to the canonical `channel_update` counter.
2. Discord uses generic `channel_update` for ordinary channel-setting edits such as names/topics, while permission overwrite changes have distinct `overwrite_create`, `overwrite_update`, and `overwrite_delete` audit actions that Dank Shield already handles separately.
3. The guardian audit listener therefore routes a benign rename through `_process()` into the canonical destructive-event engine.
4. `anti_nuke_lockdown_runtime` intentionally forces the owner-compromise path to first strike, so the misclassified event becomes `channel_update 1/1` and immediately produces the warning.
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

## Implementation scope

- remove generic `channel_update` from the guardian destructive audit map
- retain explicit `overwrite_create`, `overwrite_update`, and `overwrite_delete` protection unchanged
- retain the canonical `channel_update` counter/slow-burn key because real overwrite and protected guild-update paths intentionally reuse it
- retain owner first-strike behavior for genuinely protected actions
- add focused regression coverage proving benign generic channel updates are ignored while explicit overwrite updates still reach AntiNuke
- no threshold changes, owner whitelist, trust broadening, new runtime patch, or duplicate enforcement path

## Validation gate

- focused false-positive regressions pass
- existing AntiNuke tests pass
- full unit suite passes
- Python compile and repository standalone audits pass
- every triggered PR workflow succeeds on the exact final head
- final branch is 0 behind current `main`
- changed-file scope is limited to the guardian classifier, focused regression coverage, and task bookkeeping
- no duplicate generic `channel_update` destructive owner remains in the affected production path
- no unresolved review/thread issue
- exact validated head is merged
- resulting merge commit is verified as current `main` and deployment/status checks are reviewed

## Cleanup / conflict inspection

Pending implementation and exact-head validation. Preserve explicit overwrite enforcement, canonical shared counters, owner-compromise protection, and unrelated logging behavior.

## Backlog after this regression closes

1. Protection contextual repair adoption
2. remaining VC-specific repair cleanup
3. Embed / Status contextual repair adoption
4. admin-only `/dank tickettool-check` contextual repair adoption
5. `/dank protection` remaining non-invite picker/guard cleanup
6. `/dank design` picker migration
7. admin-only legacy setup picker cleanup

## Next step

Implement the classifier correction and focused regressions, inspect the exact diff, open the corrective PR, run the full exact-head validation wave, merge only the validated head, then verify the resulting `main` commit and deployment status before releasing this task lock.
