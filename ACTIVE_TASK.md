# ACTIVE TASK

## DS-DESIGN-033 — Fix separator, editor, reset correctness and remove redundant Dank Design paths

**Status:** IN PROGRESS — DEEP REDUNDANCY CLEANUP LANDED; FINAL EXACT-HEAD VALIDATION PENDING
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

- [x] **Separator-only Apply changed live names without saving the selected separator into authoritative design settings/rules.**
- [x] **Saved lock generation could ignore a user-selected separator.** `_current_format_lock()` could derive `separator_id` from the theme and revive the old separator.
- [x] **Category/Channel `Preview Fixes` depended on retired runtime magic.** The editor still set `__use_live_majority_layout=True` instead of explicitly using the native scoped plan service.
- [x] **Reset/remove behavior was fragmented across overlapping authorities.** Removing one row could leave another exact/category/global/protection authority active while the UI implied the item was unlocked.
- [x] **Reset All did not clear normalized-name `protection_rules`.**
- [x] **Separator-only planning did not correctly honor exact-item protection/cumulative protection modes.**
- [x] **Exact manual names could immediately fight a newly applied separator.**
- [x] **Category Editor could repair children while preserving the selected category header even when its saved design required a category-name repair.**
- [x] **Retired startup guards/shims and historical mutation scripts remained physically present after DS-DESIGN-032.**
- [x] **The legacy Studio still carried a competing public-looking Home/Apply owner plus dead Doctor, Start Here, Editors/Locks, Advanced Tools and compatibility-help surfaces.**

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
- [x] Removed retired design startup guards/shims and historical one-shot mutation scripts.
- [x] Removed duplicate registration/runtime-magic ownership paths.
- [x] Removed the competing legacy Studio Home and independent Apply implementation.
- [x] Removed dead legacy Design Doctor, Start Here, Editors/Locks and Advanced Tools submenu/helper code.
- [x] Removed obsolete V2 compatibility-help bridge used only by those dead legacy menus.
- [x] Updated legacy recovery guidance to `/dank home` → **Server Design**.
- [x] Added permanent redundancy audit coverage so retired owners/submenus cannot silently return.
- [x] Temporary cleanup helpers/workflows remove themselves after their guarded migration commits.

## Redundancy ownership result

- `public_design_studio_v2.py` — one public workflow/home/apply owner.
- `public_design_studio.py` — compatibility/backend primitives only; no public registration/home/apply/Doctor/Start Here/Advanced Tools ownership.
- `public_design_bridge.py` / `public_design_group.py` — routing/registration only.
- `server_design_plan_service.py` — native plan authority.
- `server_design_rule_service.py` — saved rule/separator/reset authority.
- `server_design_apply_service.py` — transactional Apply/Undo authority.
- majority/confidence/studio services — pure analysis/rendering helpers; no startup/runtime monkey patches.
- retired design guards and historical mutation scripts — physically removed.

## Validation

- [x] Explicit separator persistence has focused regression coverage.
- [x] Existing saved style fields remain unchanged during deliberate separator-only persistence coverage.
- [x] Category/Channel Editor uses native scoped planning in focused coverage.
- [x] Reset This Item removes every same-item override layer in focused coverage.
- [x] Reset All Design Overrides clears every advertised override layer while preserving ordinary server draft settings in focused coverage.
- [x] No active production path imports or activates retired design runtime guards in the redundancy audit.
- [x] No duplicate public command registration or competing public Home/Apply owner remains in the redundancy audit.
- [x] Dead legacy public-looking submenus/helpers were physically removed at cleanup head `30bf7467c663b51ef673ebfab6d599a2365b95ff`.
- [x] First guarded dead-UI attempt correctly failed on three stale tests and committed no product cleanup; those assertions were replaced rather than ignored.
- [x] Second guarded cleanup succeeded, deleted its own temporary helper/workflow, and committed the validated cleanup.
- [ ] Focused Dank Design tests/audits green on the final human-authored exact head.
- [ ] Full repository CI green on the final human-authored exact head.
- [ ] Final branch comparison is 0 behind `main` and diff remains scoped.
- [ ] Final PR patch has no added conflict markers or obvious committed `ghp_`, `sk-`, or `xoxb-` credential prefixes.

### CI note

The cleanup commit `30bf7467c663b51ef673ebfab6d599a2365b95ff` was authored by `github-actions[bot]`. GitHub created PR workflow runs for that bot-authored head with `action_required` and no jobs, rather than executing them. This bookkeeping commit intentionally creates a normal contributor-authored exact head so the complete focused and repository CI gates run against the already-validated cleanup content.

## Scope protection

No unrelated Community Tools, moderation, tickets, verification, profiles, welcome cards, or hosting/runtime work belongs in this task.

## Next step

Run every required workflow on this exact head. If they are green, perform the final branch/diff/credential-prefix audit, mark this task COMPLETE, update PR #189, mark it ready, and merge only after the final bookkeeping head is also green.
