# ACTIVE TASK

## DS-DESIGN-032 — Consolidate Server Design / Dank Design Studio

**Status:** IN PROGRESS — EXECUTION PATH + REDUNDANCY AUDIT
**Branch:** `fix/ds-design-032-design-studio-consolidation`
**Base:** `dd7bbe235ba26c84b5e2dbaa367ebecc6f72081b` (`main`, merged DS-COMMUNITY-031)
**Maps to:** `P0-DESIGN-001` / `P0-GUARD-001`
**Started:** 2026-09-05

## Outcome required

Make **Server Design / Dank Design Studio** one coherent, predictable product instead of a large native UI being rewritten at runtime by compatibility/enhancement layers. Simplify the Studio navigation, establish one authoritative design-plan path, preserve saved global/category/channel/manual rules, and remove redundant design code from the live execution path without changing permissions, channel placement, ticket behavior, or unrelated systems.

## Scope

- `/dank design` registration and all routes into Server Design.
- `public_design_studio.py` Studio home, preview/apply/rollback, editors, saved rules, drift/repair flow.
- Native design services used by the Studio.
- Design-specific enhancement/compatibility/startup-guard layers that mutate the Studio or design engine.
- Setup → Server Design bridge only where it routes into the Studio.
- Behavioral regression coverage and focused CI for Server Design.
- No unrelated Community Tools, moderation, ticketing, verification, profile, or welcome-card changes.

## Findings / root causes confirmed so far

- [x] `public_design_studio.py` is a >5,000-line command module that owns UI, pending editor state, locks, rollback persistence, config load/save, planning, and rendering in one file.
- [x] The repository command center already classifies `P0-DESIGN-001` as a blocker and explicitly requires splitting Dank Design into service/state/UI/logging layers.
- [x] `public_design_enhancements.py` imports modules from `startup_guards` during the normal native `/dank design` registration path.
- [x] `server_design_majority_layout_guard.py` monkey-patches the live `build_design_plan`, `_consistency_embed`, and `DesignDoctorView` objects at runtime.
- [x] `server_design_strict_layout_guard.py` monkey-patches the design service's semantic-match function and theme definitions at runtime.
- [x] `server_design_command_module_guard.py` mutates `COMMAND_MODULES`, profiles, allowed children, and `_selected_command_modules` even though `public_design_group` is already natively present in `commands_ext` and all public profiles.
- [x] A deprecated `server_design_studio_command_guard.py` compatibility shim still exists and is imported by legacy setup guard code.
- [x] Multiple historical `tools/apply_*design*` scripts contain old copies/patch fragments of Studio classes. They are not the live owner, but they materially increase maintenance ambiguity and make regressions easier to reintroduce.
- [ ] Full Studio button/view duplication map is still being completed before code is removed.

## Execution path inspected

- [x] `commands_ext.COMMAND_MODULES` / public profiles → `public_design_group.register_public_design_group_commands()`.
- [x] `/dank design` → `public_design_studio.open_design_studio()`.
- [x] `public_design_group` → `public_design_enhancements.activate_public_design_enhancements()`.
- [x] enhancement activation → strict-layout + majority-layout guard `apply()` calls.
- [x] Setup bridge → native `public_design_studio` home/view.
- [x] deprecated design command shim and redundant command-module guard.
- [ ] Studio home → doctor/repair → editor → preview/apply/rollback paths.
- [ ] exact format editor and saved-rule authority path.
- [ ] design service semantic parsing/theme/layout helpers.

## Planned changes

- [ ] Remove design command registration mutation from runtime; native command registry remains the only owner.
- [ ] Move strict semantic matching/theme behavior into the native design service and retire strict runtime patching.
- [ ] Move category-aware repair planning/doctor behavior into native design modules and retire majority runtime patching.
- [ ] Replace the current mashed Studio home with a small workflow hub: **Design Server**, **Edit One Item**, **Review / Repair**, **Saved Rules**, **Rollback**.
- [ ] Ensure each workflow has one preview/apply path instead of multiple overlapping screens that perform the same job differently.
- [ ] Preserve exact-name/channel/category/global authority order and protection behavior.
- [ ] Add behavioral tests before deleting compatibility paths.
- [ ] Remove or clearly archive obsolete design compatibility/apply scripts only after the live path is proven independent of them.

## Validation / results

- [ ] Affected modules compile.
- [ ] New behavioral Server Design workflow tests pass.
- [ ] Existing design authority/consistency/rollback tests pass.
- [ ] No live design runtime monkey-patches remain.
- [ ] `/dank design` registration remains deterministic in public/minimal/public-admin profiles.
- [ ] Full repository CI passes on the exact final head.
- [ ] Final diff is scoped, conflict-marker free, and contains no unrelated mutations.

## Conflicts / blockers

None currently. The previous Community Tools task is complete and merged, so the task lock is free.

## Backlog outside this task

- Broader startup-guard cleanup outside Server Design remains `P0-GUARD-001` and is not being mixed into this task.
- Discord Gateway uptime/reconnect flapping remains separate.

## Next step

Finish the live Studio view/route duplication map, then migrate the design-specific runtime patches into native owners before simplifying the user-facing Studio navigation.

## Definition of Done

Server Design has one native command registration path, one native design-plan authority, no design behavior injected through startup-guard monkey patches, one understandable Studio workflow hierarchy, preserved saved-rule precedence, safe preview/apply/rollback behavior, focused behavioral coverage, and green full CI.