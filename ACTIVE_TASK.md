# ACTIVE TASK

## DS-TICKET-034 — Restore public Create Ticket interaction after restart

**Status:** LIVE INTERACTION FIXED — HISTORICAL TICKET-CHOICE RECOVERY + RESET-SAFETY PATCH IN VALIDATION
**Branch:** `fix/ticket-category-reset-preserve-history`
**Base:** `66432eaf9dfa06655e14a925cad78ef8b96e010b` (`main`, merged PR #192)
**Started:** 2026-09-10

## Outcome required

An already-posted Dank Shield **Create Ticket** panel must remain usable after bot restart, acknowledge Discord on time, and show the server's own configured ticket choices without duplicate visible options. One guild's ticket configuration must never leak into, overwrite, or destructively reset another guild's menu.

## Production evidence

- The original 2026-06-11 Create Ticket panel now opens successfully.
- PR #191 production telemetry proved Discord delivers the interaction promptly and the fallback acknowledges it inside the response window.
- PR #192 production telemetry now proves the canonical per-guild loader itself is returning exactly three active keys for guild `1514374173517152418`: `['appeal', 'report', 'support']`.
- Therefore the remaining problem is no longer menu routing or duplicate rendering. The active persisted ticket-choice state for this guild has already been reduced to the safe starter trio.

## Newly proven root cause

The v2 SQL function `require_dank_ticket_category_setup()` contains a destructive asymmetry that the Python fallback does not:

- when `p_reset_to_starter=true`, it correctly reduces the live managed `ticket_categories.is_enabled` rows to `report`, `appeal`, and `support` while review is required;
- but it also overwrites `guild_configs.ticket_category_setup_selected_keys` with an empty JSON array;
- that erases the last owner-confirmed managed selection, so later canonical reconciliation has no authoritative saved keys to restore;
- the Python fallback `_mark_required_fallback_sync()` already preserves the saved selection, proving the SQL behavior is the divergent path.

This explains why PR #192 could correctly repair live-row drift when saved keys survive, yet this guild still resolves to the starter trio: the SQL safety reset can erase the saved selection itself.

## Current execution/recovery path

`Create Ticket`
→ canonical per-guild category service
→ current persisted rows resolve to starter trio
→ no guessing or hard-coded guild menu
→ recover the guild's prior Ticket Choices from `guild_config_versions`
→ explicit per-guild preview/confirm restore
→ canonical category service dedupe/reconciliation
→ production menu acceptance

Future safety:

`require_dank_ticket_category_setup(guild)`
→ may temporarily show starter rows while review is required
→ **must preserve `ticket_category_setup_selected_keys`**
→ setup remains `required=true`, version `0`, so preserved keys are recovery evidence only and do not silently bypass required review
→ user confirmation later replaces them with the newly chosen selection.

## Changes in this branch

- Added migration `20260910163000_preserve_ticket_category_selection_on_review.sql` redefining `require_dank_ticket_category_setup()` so a forced starter-row review no longer clears `ticket_category_setup_selected_keys`.
- Kept every SQL update scoped to the single supplied `p_guild_id`.
- Kept the function service-role-only.
- Registered the new migration in direct-DSN startup after the v2 selection and v3 repair migrations.
- Added focused regressions proving selection preservation, per-guild SQL filters, service-role-only execution, migration order, Python fallback parity, and that preserved keys do not become authoritative while setup is still required.
- Expanded the dedicated Ticket Panel CI gate to include this migration and test suite.

## Multi-guild safety

- No all-guild reset was added.
- The new migration changes function behavior globally but every invocation still mutates only `p_guild_id`.
- Preserved keys remain scoped to the same guild's `guild_configs` row.
- Existing history snapshots are already keyed by `guild_id` and ticket-choice restores validate that a selected version belongs to the same guild.
- Current production recovery for guild `1514374173517152418` must use only that guild's Ticket Choices history.

## Validation required

- focused reset-preservation tests
- ticket category setup/selection tests
- ticket panel single-owner/restart tests
- migration bootstrap/order tests
- full Dank Shield unit suite
- compileall and diff check
- managed-category SQL smoke/repair checks
- public setup/command/invite/safety audits
- final PR diff and review-thread inspection

## Production acceptance remaining

1. Merge/deploy the reset-safety migration only after exact-head CI is green.
2. Use **Backups & History → Ticket Choices** in guild `1514374173517152418` to inspect the prior per-guild snapshots rather than inventing category names.
3. Restore the correct prior Ticket Choices version with the existing preview + confirmation flow, which automatically backs up the current state first.
4. Press the original June 11 Create Ticket panel again and confirm the expected categories return with no repeated visible label.
5. Open the ticket panel in at least one other guild and confirm its own category set remains independent.

## Backlog

- **Server Design setup regression / full audit requested:** after DS-TICKET-034 production acceptance, immediately move to the Server Design wizard. Investigate why **Reset All Design Overrides** snaps back into saved Rule Locks and audit the entire design setup path top to bottom: persistence, reset semantics, stale UI/session state, routing, preview/save behavior, restart behavior, per-guild isolation, and setup usability.

## Blockers / risks

- The current guild's previously selected keys may already have been erased by the old SQL function. Code cannot safely infer them from the starter trio.
- The repository's durable Ticket Choices history exists specifically for this case and must be used as evidence.
- If no historical Ticket Choices snapshot contains the expected menu, the owner must explicitly choose the desired categories again through Ticket Choices setup. Do not silently enable the global catalog.

## Next step

Run exact-head validation for this branch. If green, merge/deploy, then recover the user's prior Ticket Choices from that guild's own history and complete cross-guild production acceptance. Only then close DS-TICKET-034 and activate the Server Design audit.

---

PR #190 restored restart-safe interaction registration. PR #191 proved the live dispatch path. PR #192 removed the second menu source and added saved-selection/live-row drift reconciliation. This branch fixes the deeper destructive SQL reset that could erase the saved selection itself and protects every guild from the same failure mode going forward.
