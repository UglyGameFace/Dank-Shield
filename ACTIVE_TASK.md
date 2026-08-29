# ACTIVE TASK

## DS-DESIGN-029 — Repair Dank Design editor state authority

**Status:** ACTIVE — ROOT CAUSE CONFIRMED / IMPLEMENTING
**Branch:** `fix/dank-design-editor-state-authority`
**Base:** `6f02f644b40f175da91190340a83c3d4ee81854c` (merged PR #183)
**Started:** 2026-08-28

## Reported production failure

Dank Design does not reliably honor manual Category Editor / Channel Editor choices. A user can configure one channel or category, preview/apply it, and see older whole-server design choices win again instead of the explicit item-level choice.

## Root cause / execution path findings

- [x] The intended precedence is saved channel override → saved category rule → saved global rule → local auto-detection. PR #92 explicitly established that saved owner-approved rules always win.
- [x] `build_design_plan()` still resolves that precedence correctly.
- [x] `_save_exact_lock()` persists a selected category/channel rule before preview.
- [x] `DesignPreviewView.apply()` applies the saved preview items and does not itself rebuild the whole-server plan.
- [x] A later `server_design_strict_layout_guard` violates the saved-rule contract by normalizing every Gothic/Fraktur saved global/category/channel lock on every config load/save. Explicit user-selected separators such as legacy/full/heavy bars can therefore be silently rewritten to `pipe_spaced` before the preview is built.
- [x] Custom Format currently initializes unsaved editor drafts from category-local majority/server style, so changing one dimension can unintentionally carry old/local style values into the new explicit rule.
- [x] Manual exact-editor selector changes leave `exact_match=False`, allowing smart semantic suppression instead of treating the user's explicit choice as authoritative.
- [x] Exact-format previews incorrectly reuse `StyleChangePreviewView`, which can expose whole-server separator-repair controls on an item-scoped editor preview.
- [x] All preview workflows share one mutable `_PENDING` slot per guild/user with no preview identity, so a later preview can replace the data behind an older Apply button.
- [x] Category frame application is inconsistent with the UI: strength 4 is labeled Recommended but the engine currently enables category frames only at strengths 3 and 5.

## Implementation scope

- [ ] Stop runtime Gothic normalization from mutating saved owner-approved global/category/channel locks.
- [ ] Seed Channel Editor Custom Format from the selected channel's current live style; keep Server Style as an explicit opt-in reset/suggestion.
- [ ] Treat manual exact-editor changes as exact user intent.
- [ ] Use the generic reviewed-plan Apply view for exact category/channel previews, not Style Change issue controls.
- [ ] Bind Apply to the exact preview/session that produced it so one preview cannot hijack another.
- [ ] Make strength 4 category-frame behavior match the UI contract.
- [ ] Add runtime/static regressions for each failure path above.

## Validation required

- [ ] Targeted Dank Design regression tests green.
- [ ] Existing category-aware/strict-layout/exact-editor tests green.
- [ ] Python compile/static coverage green.
- [ ] Full unit test suite green.
- [ ] Applicable standalone `tools/test_*.py` guards green.
- [ ] Full Dank Shield CI green on exact final PR head.
- [ ] Final diff/dead-reference/temporary-artifact/review-thread inspection complete.

## Cleanup / compatibility

- Preserve names-only safety, rollback snapshots, protected-name behavior, channel → category → global precedence, and local auto-detection.
- Do not alter permissions, overwrites, roles, topics, order, tickets, verification, slowmode, NSFW, archive state, or category placement.
- Remove any temporary patch/apply workflow used to land the source changes before completion.

## Definition of Done

A user editing one channel or category must have that explicit choice remain authoritative through save, preview, and apply; saved item-level rules must never be silently rewritten by a whole-server theme normalizer; stale/parallel previews must not be able to apply another preview's state; recommended category styling must behave consistently with the UI; and the exact final PR head must pass targeted plus full regression validation with no temporary implementation artifacts left behind.
