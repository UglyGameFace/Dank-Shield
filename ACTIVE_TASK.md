# ACTIVE TASK

## DS-DESIGN-029 — Repair Dank Design editor state authority

**Status:** VALIDATING — IMPLEMENTATION + FOCUSED REGRESSIONS GREEN
**Branch:** `fix/dank-design-editor-state-authority`
**PR:** #185
**Base:** `6f02f644b40f175da91190340a83c3d4ee81854c` (merged PR #183)
**Started:** 2026-08-28

## Outcome required

A manual Category Editor / Channel Editor choice must remain authoritative through save, preview, and apply. Older whole-server design choices must not silently replace an explicit item-level rule.

## Root cause / execution path

- [x] Intended precedence confirmed: saved channel override → saved category rule → saved global rule → local auto-detection. PR #92 explicitly established that saved owner-approved rules always win.
- [x] `build_design_plan()` / effective option resolution still honor that precedence.
- [x] `_save_exact_lock()` persists the selected category/channel rule before preview.
- [x] `DesignPreviewView.apply()` applies the reviewed plan and does not intentionally rebuild the whole-server design.
- [x] Primary reversion root cause confirmed: the later `server_design_strict_layout_guard` monkey-patched design option load/save and rewrote persisted Gothic/Fraktur global/category/channel locks to the Gothic default separator. Explicit `bar_full`, `bar_heavy`, and other saved choices could therefore become `pipe_spaced` before preview/apply.
- [x] Unsaved Custom Format drafts were seeded from server/category majority rather than the selected item's live style, allowing old majority values to hitchhike into a new exact rule.
- [x] Manual selector changes could retain `exact_match=False`, allowing Smart Fix suppression to override explicit user intent.
- [x] Exact item previews reused `StyleChangePreviewView`, a whole-server separator-repair view with unrelated controls.
- [x] Exact previews shared mutable `_PENDING` state without binding Apply to the preview that created the button.
- [x] Strength 4 is labeled Recommended but category frames were enabled only at strengths 3 and 5.
- [x] Exact editor Separator Examples next/previous/back callbacks defined a guarded action but never executed it, then referenced `guild` outside that action.

## Changes

- [x] Removed obsolete persisted Gothic-lock normalization / command option load-save monkey patch. Gothic Clean's current default remains normalized only at the theme-definition layer.
- [x] Saved owner-approved channel/category/global rules are preserved instead of rewritten.
- [x] Unsaved exact editors seed from the selected item's current live style; **Server Style** remains the explicit opt-in majority reset.
- [x] Manual exact-editor changes force exact intent.
- [x] Exact category/channel previews use the generic reviewed-plan Apply view.
- [x] Exact Apply is bound to the preview timestamp and rejects an obsolete Apply button after a newer preview replaces pending state.
- [x] Strength 4 applies the selected category frame consistently with the UI contract.
- [x] Separator Examples paging/back navigation now executes inside `_guard_design_action` with valid guild/editor state.
- [x] Added `tests/test_dank_design_editor_state_authority.py` covering the repaired authority paths.

## Validation / results

- [x] Affected Python modules compile in focused gates.
- [x] Focused editor-authority + category-aware + strict-layout + exact-editor suites pass.
- [x] Final exact-editor navigation focused gate: **26 tests passed**.
- [x] `git diff --check` passed before committing the navigation repair.
- [x] Temporary patch/apply workflows removed from the branch after successful source commits.
- [x] Pre-PR diff limited to Dank Design implementation, strict-layout compatibility cleanup, regressions, and this task record.
- [ ] Full unit test suite green on exact final PR head.
- [ ] Applicable standalone `tools/test_*.py` guards green on exact final PR head.
- [ ] Full Dank Shield CI and auxiliary PR workflows green on exact final PR head.
- [ ] Final PR diff / review threads / comments / commit status inspected after CI.

## Cleanup / compatibility

- [x] Obsolete persisted-lock normalizer and its dead persistence hook removed rather than left as a no-op shim.
- [x] Names-only safety preserved. No intended changes to permissions, overwrites, roles, topics, order, tickets, verification, slowmode, NSFW, archive state, or category placement.
- [x] Rollback snapshots, protected-name behavior, and channel → category → global precedence preserved.
- [ ] Final dead-reference/conflict-artifact inspection after final PR head settles.

## Backlog outside current Apply-reversion fix

- Direct Rename immediately renames Discord but does not create/update an exact saved style rule. A later saved-design preview can therefore restyle that renamed item. This is a separate UX/ownership behavior because Direct Rename has no reviewed Apply step; do not broaden this PR unless required for the current authority fix.
- `_initial_editor_lock()` currently computes majority inference and `_live_target_exact_lock()` computes it again. This is redundant work, not a correctness blocker; optimize separately if worthwhile.

## Definition of Done

A user editing one channel or category has that explicit rule remain authoritative through save, preview, and apply; saved item-level rules are never silently rewritten by a whole-server theme normalizer; stale preview buttons cannot apply newer pending state; exact editor navigation works; Recommended category styling matches the UI; temporary implementation machinery is absent; and the exact final PR head passes targeted plus full repository validation and final diff/review inspection.
