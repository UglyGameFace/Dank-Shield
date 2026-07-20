# ACTIVE TASK

## DS-TICKET-CATEGORIES-001 — Restore rich ticket picker categories without patch layers

**Status:** IN PROGRESS — SETUP ALIGNMENT + VALIDATION PENDING
**Branch:** `fix/reconcile-ticket-picker-default-categories`
**Base:** current `main` after merged PR #104

## Single Active Task Lock

Do not switch to unrelated implementation work until this task reaches Definition of Done or the owner explicitly force-switches tasks.

## Hard Architecture Rules

- No monkey patches.
- No startup guards as the implementation path.
- No import-time mutation of another module's callbacks, views, commands, or constants.
- No new bridge/tiny patch module.
- Preserve multi-server isolation and owner-defined custom ticket categories.
- Use native runtime owners and behavioral tests.

## Scope

- Restore the richer built-in ticket choices users were intended to see, including Partnerships and the other specialized ticket types already defined by the live ticket system.
- Fix legacy guilds whose categories came from the older stripped-down `/dank setup` starter set.
- Keep genuinely custom owner category sets authoritative.
- Keep the actual Create Ticket picker and `/ticket-category list` consistent.
- Align the native `/dank setup` recommended category source with the same rich built-ins before DoD.

## Root Cause

The repository currently has split category truth:

- `stoney_verify/tickets_new/panel.py` owns a richer built-in set including Verification, Account / Access, Payments / Refunds, Appeals, Reports, Staff Complaint, COD Services, Service Requests, Vouch / Referral, Giveaway / Reward, Content / Media, Partnerships, Questions, and Support.
- `stoney_verify/commands_ext/public_setup_solid.py` still seeds an older seven-option starter set: Support, Verification, Appeal, Report, Question, Bug, and Other.
- `public_tickettool_parity_polish._load_ticket_rows()` returns configured rows as soon as any rows exist, so guilds created with the older starter set never reach the richer fallback definitions.
- Historical category functionality also exists in startup-guard patch modules, but those are explicitly not an acceptable permanent implementation path and are not being used for this fix.

## Changes So Far

- Added native legacy-managed-set detection directly to `public_tickettool_parity_polish.py`.
- The live picker now layers the rich `tickets_new.panel` built-ins over only the recognized legacy managed starter shape.
- Any unknown/custom category slug makes the owner's configured category set authoritative, so custom guilds are not silently expanded or overwritten.
- `/ticket-category list` now uses the same effective category loader as the member-facing picker.
- Added behavioral tests proving:
  - the legacy managed starter set is recognized;
  - Partnerships and richer built-ins are restored for that set;
  - no canonical duplicates are introduced;
  - a custom `vip_concierge` category prevents automatic default merging;
  - the actual Discord select exposes Partnership, COD Services, Account / Access, and Payments / Refunds.

## Validation

Pending:

- Native `/dank setup` recommended-category alignment.
- Targeted ticket category tests.
- Python compile validation.
- Full `pytest tests/` regression suite.
- Standalone tools/audits required by CI.
- Final diff/conflict inspection.
- GitHub Actions on the final PR head.

## Cleanup / Conflict Inspection

- No new startup guard was added.
- No existing startup guard is used by the new reconciliation behavior.
- No new monkey patch was added.
- No new bridge module was added.
- No database schema change or duplicate category table was added.
- Custom owner categories remain authoritative.
- Existing historical patch/guard debt is outside the implementation path and must not be expanded by this task.

## Blockers

None known yet. Setup alignment and validation remain.

## Definition of Done

- [x] Legacy managed starter sets regain Partnerships and the richer live built-ins in the Create Ticket picker.
- [x] Custom owner category sets are not force-expanded.
- [x] Picker category dedupe remains canonical and capped by Discord's select limit.
- [x] `/ticket-category list` reports the same effective category choices as the picker.
- [x] Behavioral coverage exists for rich reconciliation and custom-owner preservation.
- [ ] `/dank setup` recommended category source is aligned natively with the rich built-ins.
- [ ] No monkey patch/startup-guard implementation dependency exists for the completed fix.
- [ ] Targeted tests pass.
- [ ] Full regression/compile/audits pass.
- [ ] Final diff contains only task-related permanent code/tests/task record.
- [ ] Final-head GitHub Actions pass.
- [ ] Merge/deploy requires explicit user approval.
