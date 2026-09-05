# ACTIVE TASK

## DS-DESIGN-032 — Consolidate Server Design / Dank Design Studio

**Status:** COMPLETE — IMPLEMENTATION + FOCUSED/FULL VALIDATION GREEN
**Branch:** `fix/ds-design-032-design-studio-consolidation`
**PR:** #188
**Base:** `dd7bbe235ba26c84b5e2dbaa367ebecc6f72081b` (`main`, merged DS-COMMUNITY-031)
**Implementation head validated:** `7e91b041376c7ed7d7882c64f0c10e18f8d53c30`
**Maps to:** `P0-DESIGN-001` / design-specific portion of `P0-GUARD-001`
**Started:** 2026-09-05
**Completed:** 2026-09-05

## Outcome

Server Design / Dank Design Studio now has one coherent public workflow, one explicit native plan authority, transaction-like batch apply/undo behavior, preserved saved-rule precedence, and no live Server Design behavior injected through the former strict/majority startup-guard monkey patches. The compact `/dank home` → **Server Design** doorway and Setup both open the same Studio owner.

## Scope

- `/dank home` → Server Design and all other routes into Server Design.
- `public_design_studio_v2.py` public Studio workflow owner.
- `public_design_studio.py` compatibility backend for mature exact-item/rule editors.
- Native saved-design and Smart Repair planning.
- Preview/apply/compensation/rollback execution.
- Saved global/category/channel/exact-name rules and protection precedence.
- Design-specific enhancement/startup-guard compatibility layers.
- Setup → Server Design bridge.
- Focused behavioral/static CI plus full repository validation.
- No unrelated Community Tools, moderation, ticketing, verification, profile, or welcome-card redesign.

## Root causes confirmed

- [x] The old Studio mixed whole-server design, exact-item editing, repair, rule management, protection, help, and rollback into one oversized home flow.
- [x] The normal design registration path activated strict/majority guard modules that replaced live design functions/classes at runtime.
- [x] A redundant command-module guard mutated command registries/profiles despite the native design group already being registered.
- [x] Setup still routed through deprecated compatibility registration behavior instead of the same public Studio owner.
- [x] Smart Repair depended on runtime replacement of `build_design_plan` rather than an explicit native planning service.
- [x] The compact `/dank home` Server Design button imposed a stricter Manage Server/Administrator gate than the Studio's established Manage Channels authority.
- [x] Majority-layout compatibility still retained a parser replacement hook even though the native parser was already separator-aware.

## Execution path inspected

- [x] public command registration/profiles → `public_design_group`.
- [x] compact `/dank home` → **Server Design** → `public_design_bridge` → consolidated Studio.
- [x] Setup → Server Design bridge → the same consolidated Studio.
- [x] Studio home → Design Entire Server.
- [x] Studio home → Edit One Category / Channel → mature exact-item editors.
- [x] Studio home → Fix Inconsistent Names → scan → Smart Repair preview.
- [x] Studio home → Saved Rules & Protection.
- [x] preview → full-batch preflight → paced apply → compensation on failure.
- [x] durable Apply snapshot → Undo preview → full-batch undo preflight → restore/compensation.
- [x] saved-rule precedence and category-aware auto-detection.
- [x] strict/majority compatibility guards and redundant command-module registration guard.
- [x] native separator parsing and majority-layout helpers.

## Changes

- [x] Added a five-workflow Studio hub: **Design Entire Server**, **Edit One Category / Channel**, **Fix Inconsistent Names**, **Saved Rules & Protection**, and **Undo Last Apply**.
- [x] Added `server_design_plan_service.py` as the explicit plan authority for saved design and category-aware Smart Repair.
- [x] Added `server_design_apply_service.py` for full-batch preflight, per-item freshness checks, compensation, durable snapshot rows, and safe Undo execution.
- [x] Smart Repair is category-aware, keeps saved narrow rules authoritative, and fails closed when confidence is too low.
- [x] Batch design changes use one reviewed Preview → Apply path; exact single-item Rename remains the only clearly documented immediate rename action.
- [x] Strict-layout and majority-layout startup guards are retired to validation-only compatibility shims and no longer replace live design functions/classes.
- [x] The redundant command-module guard no longer mutates public command registries/profiles.
- [x] Setup and compact home both route to the same consolidated Studio instead of competing design screens.
- [x] Compact home preserves the established **Manage Channels** design authority instead of requiring Manage Server/Administrator.
- [x] Public recovery guidance points users to `/dank home` → **Server Design**, matching the actual compact command surface.
- [x] The old majority parser hook is now an inert compatibility no-op; separator-safe parsing remains owned by the native design service.
- [x] Added dedicated `Dank Design 032` CI covering the compact doorway, Studio owners, native services, focused regressions, and ownership/UX audits.

## Validation / results

- [x] Dedicated **Dank Design 032** workflow passed on implementation head `7e91b041376c7ed7d7882c64f0c10e18f8d53c30`.
- [x] Focused Design suite: **88 passed, 1 warning**.
- [x] Smart Auto-Detect audit reports `category_local=yes`, `deterministic=yes`, `runtime_patch=no`, `native_flow=yes`.
- [x] Native registration, safe-repair, UX/authority, parser ownership, and compact Manage Channels doorway audits passed.
- [x] Full **Dank Shield CI** passed on the same implementation head.
- [x] Full repository tests: **1132 passed, 9 warnings in 388.28s**.
- [x] All standalone `tools/test_*.py` checks passed.
- [x] Public setup, command surface/friction, invite-permission, setup-safety, role-truth, and event-boundary audits passed.
- [x] **Application Command Size Diagnostics**, **Profile Runtime Diagnostics**, and **Ticket Owner Emergency Override** passed on the implementation head.
- [x] Branch comparison before this bookkeeping-only completion commit: **59 commits ahead, 0 behind** `main`.
- [x] Changed-file scope before this bookkeeping-only completion commit: **29 files**, limited to Server Design routing/services/guards/tests/tools/CI plus task bookkeeping.
- [x] PR patch inspection found no added `<<<<<<<` / `>>>>>>>` conflict markers.
- [x] Obvious credential-prefix inspection found no added `ghp_`, `sk-`, or `xoxb-` secrets.

## Cleanup / compatibility

- [x] Public navigation and batch execution now have explicit owners; historical exact-item/rule editor code remains only as a compatibility backend where it still provides mature functionality.
- [x] Historical `tools/apply_*design*` artifacts were not made live and were not deleted blindly; they remain outside the public execution path.
- [x] No unrelated system behavior was intentionally changed.
- [x] No design-specific live startup monkey patch remains in the validated path.

## Conflicts / blockers

None remaining for DS-DESIGN-032.

## Backlog outside this task

- Broader non-Design startup-guard cleanup remains separate under `P0-GUARD-001`.
- A future deeper extraction of mature exact-item/rule editors from the legacy Studio module can be handled separately; it is not required for the now-single public workflow/plan authority.
- Discord Gateway uptime/reconnect flapping remains a separate runtime/hosting task.

## Next step

Run exact-head validation for this bookkeeping-only completion commit, mark PR #188 ready, and merge when the final checks remain green.

## Definition of Done

Met: one public Server Design workflow hierarchy, one explicit native plan authority, no design behavior injected through startup-guard monkey patches, preserved saved-rule precedence, safe reviewed batch apply/undo behavior, correct Manage Channels access from the compact doorway, focused regression coverage, and green full repository validation.
