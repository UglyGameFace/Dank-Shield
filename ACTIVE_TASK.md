# ACTIVE TASK

## DS-DESIGN-033 — Fix separator, editor, reset correctness and remove redundant Dank Design paths

**Status:** IN PROGRESS — FINAL CLEANUP VERIFIED; EXACT-HEAD FULL CI PENDING
**Branch:** `fix/ds-design-033-editor-separator-reset-correctness`
**Base:** `656ee13d02e54614c9f6f7a34d69008f2a0943e1` (`main`, merged DS-DESIGN-032)
**Started:** 2026-09-05

## Outcome required

Make Dank Design behave exactly like its UI says and leave one understandable execution path for each job. A selected separator must become the saved desired separator, category/channel editors must preview the correct native scoped plan, Reset must remove the authority the user expects, and dead compatibility/runtime-patch-era code must not remain around as a second apparent owner.

## User-reported failures

- Channel separators selected in Dank Design were not working/sticking correctly.
- Category Editor behavior was incorrect.
- Locks did not appear to lift when trying to reset/remove them.
- Full Dank Design pass requested for redundant code after the correctness repair.

## Root causes confirmed

- [x] Separator-only Apply changed live names without saving the selected separator into authoritative design settings/rules.
- [x] Saved lock generation could ignore a user-selected separator because `_current_format_lock()` could derive `separator_id` from the theme and revive the old separator.
- [x] Category/Channel `Preview Fixes` depended on retired runtime magic instead of explicitly using the native scoped plan service.
- [x] Reset/remove behavior was fragmented across overlapping authorities, so removing one row could leave another exact/category/global/protection authority active while the UI implied the item was unlocked.
- [x] Reset All did not clear normalized-name `protection_rules`.
- [x] Separator-only planning did not correctly honor exact-item protection/cumulative protection modes.
- [x] Exact manual names could immediately fight a newly applied separator.
- [x] Category Editor could repair children while preserving the selected category header even when its saved design required a category-name repair.
- [x] Retired startup guards/shims, historical mutation scripts, and public-looking legacy Design owners remained physically present after earlier consolidation.
- [x] A later regression-test rewrite drifted away from the native service contracts: it called a nonexistent separator API, passed obsolete reset arguments, expected the wrong reset return shape, and monkeypatched a nonexistent `plan_service.legacy` attribute. Those test contracts were restored to the production service APIs rather than changing production to satisfy invalid tests.

## Final execution path

- [x] `/dank home` → **Server Design** is the canonical public doorway.
- [x] **Design Entire Server** → settings → preview → consolidated V2 Apply.
- [x] **Change Separators Only** → saved separator state → preview → consolidated V2 Apply → transactional separator authority persistence.
- [x] **Edit One Category / Channel** → exact item editor → native scoped planner for Preview Fixes.
- [x] **Custom Format** → exact category/channel saved rule.
- [x] **Fix Inconsistent Names** → read-only scan / Smart Repair → native plan/confidence services.
- [x] **Saved Rules & Protection** → remove one rule / reset item / reset all with explicit authority semantics.
- [x] **Undo Last Apply** → consolidated transactional undo path.
- [x] Legacy Studio is compatibility/backend only for still-used exact-item, saved-rule, separator and rollback primitives.

## Changes landed

- [x] Added `server_design_rule_service.py` as the pure saved-rule/separator/reset authority.
- [x] Separator-only Apply saves the chosen separator transactionally and updates exact-name rows touched by the reviewed batch.
- [x] Explicit saved separators outrank theme defaults when locks are built/synchronized.
- [x] Category/Channel `Preview Fixes` routes through native scoped planning with confidence evaluated after scope filtering.
- [x] Category repair includes the selected category header when its saved design requires repair while child channels retain safe category-local repair.
- [x] Added `Reset This Category`, `Reset This Channel`, and complete `Reset All Design Overrides` semantics.
- [x] Corrected exact-item/cumulative protection handling for separator-only planning.
- [x] Replaced misleading one-rule “Unlock” wording with explicit remove/reset language.
- [x] Removed retired design startup guards/shims, historical one-shot mutation scripts, duplicate registration/runtime-magic ownership paths, the competing legacy Home/Apply implementation, and dead legacy Design UI owners/helpers.
- [x] Removed obsolete V2 compatibility-help bridge used only by dead legacy menus.
- [x] Updated legacy recovery guidance to `/dank home` → **Server Design**.
- [x] Added permanent redundancy audit coverage so retired owners/submenus cannot silently return.
- [x] Repaired drifted regression tests back to the native `server_design_rule_service` and `server_design_plan_service` contracts.
- [x] Final guarded cleanup deleted its own temporary workflow/helper after validation.

## Redundancy ownership result

- `public_design_studio_v2.py` — one public workflow/home/apply owner.
- `public_design_studio.py` — compatibility/backend primitives only; no public registration/home/apply/Doctor/Start Here/Advanced Tools ownership.
- `public_design_bridge.py` / `public_design_group.py` — routing/registration only.
- `server_design_plan_service.py` — native plan authority.
- `server_design_rule_service.py` — saved rule/separator/reset authority.
- `server_design_apply_service.py` — transactional Apply/Undo authority.
- Majority/confidence/studio services — analysis/rendering helpers with no startup/runtime monkey patches.
- Retired design guards, historical mutators, and temporary cleanup machinery — physically removed.

## Validation

- [x] Separator persistence, scoped editor repair, reset semantics, protection handling, consolidated ownership, and rollback-owner retirement have focused regression coverage.
- [x] Guarded cleanup workflow `34045911729` succeeded before publishing cleanup commit `154bbe1671c14f24f6a12e69af101822a9d3493e`.
- [x] Guarded cleanup focused suite: **63 passed, 1 warning**.
- [x] Redundancy audit: `public_registrar=1 retired_runtime=0 historical_mutators=0 runtime_magic=0 dead_submenus=0 dead_owners=0 native_plan=yes consolidated_apply=yes compatibility_boundary=ui_only`.
- [x] Smart Auto-Detect audit: `category_local=yes raw_separator_identity=yes deterministic=yes keep_existing_exact=yes runtime_patch=no native_flow=yes`.
- [x] Cleanup commit removed **722** lines of dead legacy code in the validated migration and deleted the temporary cleanup workflow/helper.
- [x] Bot-authored cleanup head `154bbe1671c14f24f6a12e69af101822a9d3493e` produced `action_required` PR checks with no jobs, confirming the repository's contributor-authored validation requirement rather than a product/test failure.
- [ ] All six required PR workflows green on the current contributor-authored exact head.
- [ ] Final branch comparison is 0 behind `main` and diff remains scoped.
- [ ] Final PR patch has no conflict markers, temporary cleanup files, debug artifacts, or obvious committed `ghp_`, `sk-`, or `xoxb-` credential prefixes.

## Cleanup / conflicts

- The invalid regression-test rewrite was repaired to match established production service contracts; production behavior was not altered to satisfy nonexistent APIs.
- Dead legacy owners were removed only after AST reference checks proved they were not executable dependencies outside the compatibility backend.
- Temporary migration workflow/helper deleted themselves after focused tests, audits, compilation, and `git diff --check` passed.
- No unrelated feature area is part of this task.

## Blockers / risks

- No known product blocker remains after focused cleanup validation.
- Merge remains blocked until every required workflow passes on the exact final contributor-authored SHA, including any later bookkeeping SHA.

## Backlog

- None for DS-DESIGN-033 beyond final exact-head validation and merge bookkeeping.

## Next step

Run the six required PR workflows on this contributor-authored bookkeeping head. If all are green, perform final branch/diff/credential/temporary-file review, update this task to COMPLETE, revalidate the new final bookkeeping SHA, then mark PR #189 ready and merge only if that exact final head is green.
