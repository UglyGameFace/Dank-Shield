# ACTIVE TASK

## DS-DESIGN-033 — Fix separator, category-editor, and reset/unlock correctness

**Status:** IN PROGRESS — ROOT CAUSE CONFIRMED, IMPLEMENTATION NEXT
**Branch:** `fix/ds-design-033-editor-separator-reset-correctness`
**Base:** `656ee13d02e54614c9f6f7a34d69008f2a0943e1` (`main`, merged DS-DESIGN-032)
**Started:** 2026-09-05

## Outcome required

Make Dank Design behave exactly like its UI says: a selected channel separator must actually become the saved desired separator, category/channel editors must preview the correct native scoped plan, and Reset/Unlock must remove the authority the user expects instead of leaving hidden overlapping rules active.

## User-reported failures

- Channel separators selected in Dank Design are not working/sticking correctly.
- Category Editor behavior is incorrect.
- Locks do not appear to lift when trying to reset/unlock them.

## Root causes confirmed

- [x] **Separator-only Apply changes live names but does not save the selected separator into the authoritative design settings/rules.** Its pending payload stores the old `options` unchanged, so a later saved-design preview can propose the old separator again.
- [x] **Saved lock generation can ignore a user-selected separator.** `_current_format_lock()` derives `separator_id` from the theme's `channel_separator` instead of an explicit saved `options["separator_id"]`; `_sync_enabled_global_lock()` rebuilds an enabled global lock from that helper and can therefore restore the theme separator.
- [x] **Category/Channel `Preview Fixes` still depends on a retired magic flag.** `_preview_scope()` sets `__use_live_majority_layout=True` and then calls legacy `build_design_plan()` directly. DS-DESIGN-032 removed the runtime guard that used to make that flag meaningful, so the scoped editor is not using the native plan service it now claims to use.
- [x] **Reset/Unlock is fragmented across overlapping authorities.** Removing one category/channel/manual/protection row can leave another rule for the same item active, while the UI calls the action simply “Unlock.”
- [x] **`Clear All Locks` does not clear name-level `protection_rules`.** It clears global/category/channel/manual/exact-item protection state, but protection overrides by normalized name can remain active after a user-facing reset.

## Execution path under repair

- [x] `/dank home` → Server Design → Design Entire Server → Change Separators Only.
- [x] separator selector → preview → consolidated Apply.
- [x] Edit One Category / Channel → Category Editor / Channel Editor → Preview Fixes.
- [x] Custom Format → exact category/channel saved rules.
- [x] Saved Rules & Protection → Layout Rules → Unlock / Clean → individual reset / clear all.
- [ ] Native plan-service scoped planning and persistence integration.
- [ ] Regression tests for separator persistence, scoped category preview, and complete reset semantics.

## Planned changes

- [ ] Persist a reviewed separator-only Apply as the new separator component of the server draft and applicable saved style locks without changing font, category frame, permissions, order, or unrelated settings.
- [ ] Make `_current_format_lock()` honor an explicit saved separator rather than silently falling back to the theme separator.
- [ ] Route Category/Channel `Preview Fixes` through the native `server_design_plan_service` and then scope/filter the resulting plan.
- [ ] Add an obvious **Reset This Item** operation that removes all exact/category/channel/manual-name/exact-protection authority for the selected item in one action.
- [ ] Make **Reset All Design Overrides** actually reset all saved override layers, including name-level protection overrides, while preserving the ordinary server draft unless the UI explicitly says otherwise.
- [ ] Make result screens state exactly what remains authoritative after a reset.
- [ ] Add focused behavioral tests before merging.

## Validation required

- [ ] Selected separator survives a later saved-design preview and bot restart/persistence reload path.
- [ ] Existing category/channel style locks retain their font/frame/icon settings while adopting a deliberate separator-only update where intended.
- [ ] Category Editor preview uses native scoped planning and never relies on `__use_live_majority_layout` runtime magic.
- [ ] Reset This Item leaves no narrower rule for that item unless the user deliberately keeps one.
- [ ] Reset All Design Overrides removes all saved override layers advertised by the UI.
- [ ] Focused Dank Design tests/audits green.
- [ ] Full repository CI green on exact final head.
- [ ] Final diff scoped and conflict/credential-prefix checks clean.

## Scope protection

No unrelated Community Tools, moderation, tickets, verification, profiles, welcome cards, or hosting/runtime work in this task.

## Next step

Implement the persistence/authority fixes first, then the scoped editor and reset semantics, and prove each reported failure with regression tests before calling the task complete.
