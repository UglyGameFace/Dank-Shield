# ACTIVE TASK

## DS-FIX-PERMISSION-REPAIR-UX — Make permission repair usable, truthful, and non-stalling

**Outcome:** Dank Shield permission repair should show only actionable information, keep selected options visibly selected, report only changes that actually succeeded, and never leave Discord stuck on an indefinite “thinking” state.

**Status:** IN PROGRESS — root causes confirmed; implementation branch active

**Branch:** `fix/permission-repair-ui-flow`
**Base main:** `653debf88186f8eb2123db5eda52eee5f779b977`

## Scope

- `stoney_verify/setup_permission_repair_services.py`
- `stoney_verify/permission_repair.py`
- `stoney_verify/setup_activity_access.py`
- `stoney_verify/startup_guards/setup_health_defer_guard.py`
- retire `stoney_verify/startup_guards/setup_permission_repair_preview_clarity_guard.py`
- focused permission-repair regression coverage

No ticket schema, moderation, verification, anti-nuke, command ownership, or unrelated setup redesign belongs in this task.

## Findings / root cause

1. The broad permission-repair screen always runs a full Advanced diagnostic audit and appends its blockers, warnings, and passing checks to the normal repair preview. That produces a wall of information that is not the repair action itself.
2. The same broad repair path merges whole-server activity-coverage targets into ordinary setup repair. Servers with many channels therefore show dozens of unrelated targets and “bot lacks Manage Channels” rows.
3. `setup_permission_repair_services.preview_or_apply()` records a target in `changed` before the Discord write succeeds. On apply failure it can therefore describe a failed write as changed.
4. Apply flows defer with `thinking=True` and then send a follow-up instead of editing the deferred original response. Discord keeps the original “Dank Shield is thinking…” placeholder alive indefinitely.
5. The selected-target Fix Access view recreates static select menus after every choice. The underlying state changes, but the rebuilt controls reset their visible labels to General / Recommended minimum / Choose a channel, so mobile users cannot tell what is selected.
6. `setup_permission_repair_preview_clarity_guard` still monkey-patches the legacy result embed even though the native repair service is the canonical UI owner. This creates conflicting display ownership.

## Execution path to preserve

- Setup Security / Logs routes stay on the native `setup_permission_repair_services` owner.
- Activity coverage remains read-only in `setup_activity_access` and may deliberately opt into activity-access repair without forcing that whole-server scan into normal setup repair.
- Selected-target repair continues to change only Dank Shield’s own overwrite, preserve unrelated visibility, require explicit confirmation before clearing explicit denies, and keep undo snapshots.
- Existing operation-queue serialization remains authoritative for mutation safety.

## Planned changes

- make normal setup repair scoped to configured/exact-name setup targets only
- keep activity-coverage repair opt-in from the activity access screen
- replace the diagnostic dump with a concise repair summary and small actionable examples
- report only writes that actually succeeded
- remove the Advanced diagnostic audit from normal repair preview/apply
- acknowledge component actions with deferred message updates and edit the original response instead of leaving thinking placeholders
- make selected target / feature / repair mode / category-children state visibly persistent
- retire the obsolete preview-clarity monkey patch
- add behavior-level regressions for state persistence, successful-write truth, scope separation, and non-thinking response flow

## Validation / results

Not run yet.

## Cleanup / conflicts

Not complete yet.

## Blockers / risks

- Live Discloud runtime revision cannot be proven from GitHub alone. Repository validation can prove the fix on the PR head, but the host still needs to run that revision before the live Discord behavior changes.

## Backlog

- unrelated existing setup/startup-guard cleanup remains separate
- operation-queue lock-retention debt remains separate

## Next step

Implement the smallest complete permission-repair ownership fix, add focused regressions, run the repository validation gates, then inspect the final diff for duplicate/obsolete logic and accidental unrelated changes.
