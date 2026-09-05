# ACTIVE TASK

## DS-DESIGN-033 — Fix separator, editor, reset correctness and remove redundant Dank Design paths

**Status:** IN PROGRESS — CORRECTNESS FIXES LANDED, FULL OWNERSHIP/REDUNDANCY AUDIT ACTIVE
**Branch:** `fix/ds-design-033-editor-separator-reset-correctness`
**Base:** `656ee13d02e54614c9f6f7a34d69008f2a0943e1` (`main`, merged DS-DESIGN-032)
**Started:** 2026-09-05

## Outcome required

Make Dank Design behave exactly like its UI says and leave one understandable execution path for each job. A selected separator must become the saved desired separator, category/channel editors must preview the correct native scoped plan, Reset must remove the authority the user expects, and dead compatibility/runtime-patch-era code must not remain around as a second apparent owner.

## User-reported failures

- Channel separators selected in Dank Design are not working/sticking correctly.
- Category Editor behavior is incorrect.
- Locks do not appear to lift when trying to reset/remove them.
- Full Dank Design pass requested for redundant code after the correctness repair.

## Root causes confirmed

- [x] **Separator-only Apply changed live names without saving the selected separator into authoritative design settings/rules.**
- [x] **Saved lock generation could ignore a user-selected separator.** `_current_format_lock()` could derive `separator_id` from the theme and revive the old separator.
- [x] **Category/Channel `Preview Fixes` depended on retired runtime magic.** The editor still set `__use_live_majority_layout=True` instead of explicitly using the native scoped plan service.
- [x] **Reset/remove behavior was fragmented across overlapping authorities.** Removing one row could leave another exact/category/global/protection authority active while the UI implied the item was unlocked.
- [x] **Reset All did not clear normalized-name `protection_rules`.**
- [x] **Separator-only planning did not correctly honor exact-item protection/cumulative protection modes.**
- [x] **Exact manual names could immediately fight a newly applied separator.**
- [x] **Category Editor could repair children while preserving the selected category header even when its saved design required a category-name repair.**
- [x] **Retired design startup guards/shims still physically exist after DS-DESIGN-032.** They are no longer supposed to own runtime behavior, but their presence creates maintenance ambiguity and must be classified by actual references before removal.

## Execution path under repair/audit

- [x] `/dank home` → Server Design → Design Entire Server → Change Separators Only.
- [x] separator selector → preview → consolidated Apply → persist separator authority.
- [x] Edit One Category / Channel → Preview Fixes → native scoped planner.
- [x] Custom Format → exact category/channel saved rules.
- [x] Saved Rules & Protection → remove one rule / reset item / reset all.
- [x] Native plan-service scoped planning and confidence integration.
- [x] Regression coverage for separator persistence, scoped preview, category-header repair, and complete reset semantics.
- [ ] Classify every Dank Design command/service/guard/helper as active owner, compatibility-only, duplicate-but-needed, or dead.
- [ ] Remove dead runtime-patch-era design guards/shims and temporary patch machinery when references prove they are unnecessary.
- [ ] Verify legacy backend contains only still-reachable compatibility/editor primitives or documented migration debt, not a competing public workflow.

## Changes landed so far

- [x] Added `server_design_rule_service.py` as the pure saved-rule/separator/reset authority.
- [x] Separator-only Apply now saves the chosen separator transactionally and updates exact-name rows touched by that reviewed batch.
- [x] Explicit saved separators now beat theme defaults when locks are built/synchronized.
- [x] Category/Channel `Preview Fixes` now routes through native scoped planning.
- [x] Added `Reset This Category` / `Reset This Channel` and complete Reset All semantics.
- [x] Corrected separator protection semantics and exact-item protection lookup.
- [x] Added scoped category-header repair behavior.
- [x] Replaced misleading one-rule “Unlock” result semantics with explicit remove/reset language in the active UI path.
- [x] Added DS-DESIGN-033 focused regressions and extended dedicated Design CI.

## Redundancy audit targets

- `public_design_studio_v2.py` — public workflow owner.
- `public_design_studio.py` — compatibility/editor/backend module; audit every surviving public-looking view/registration path.
- `public_design_bridge.py`, `public_design_group.py`, `public_design_enhancements.py` — registration/compatibility ownership.
- `server_design_plan_service.py`, `server_design_rule_service.py`, `server_design_apply_service.py`, majority/confidence/studio services — service authority overlap.
- Retired startup guards: `server_design_command_module_guard.py`, `server_design_majority_layout_guard.py`, `server_design_strict_layout_guard.py`, `server_design_studio_command_guard.py`.
- Runtime metadata/legacy helpers such as `__use_live_majority_layout`, `_infer_live_majority_context`, old consistency/doctor/home paths, deprecated registration helpers, and one-shot patch assets.

## Validation required

- [x] Explicit separator persistence has focused regression coverage.
- [x] Existing saved style fields remain unchanged during deliberate separator-only persistence coverage.
- [x] Category/Channel Editor uses native scoped planning in focused coverage.
- [x] Reset This Item removes every same-item override layer in focused coverage.
- [x] Reset All Design Overrides clears every advertised override layer while preserving ordinary server draft settings in focused coverage.
- [ ] No active production path imports or activates retired design runtime guards.
- [ ] No duplicate public command registration or competing public home/apply owner remains.
- [ ] Temporary one-shot patch workflow/helper removed.
- [ ] Focused Dank Design tests/audits green on exact final head.
- [ ] Full repository CI green on exact final head.
- [ ] Final diff scoped; conflict markers and obvious committed credential prefixes absent.

## Scope protection

No unrelated Community Tools, moderation, tickets, verification, profiles, welcome cards, or hosting/runtime work in this task.

## Next step

Trace every remaining reference to the retired guards, legacy public-looking UI owners, runtime magic flags, and temporary patch files. Remove only code proven dead, preserve required compatibility primitives, then run focused and full exact-head validation before merging.
