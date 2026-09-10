# ACTIVE TASK

## DS-TICKET-034 — Restore public Create Ticket interaction after restart

**Status:** LIVE BUTTON ACKNOWLEDGED — PER-GUILD CATEGORY SOURCE FIX IN VALIDATION
**Branch:** `fix/ticket-panel-guild-category-source`
**Base:** `2bd7c819c23aec184bf5e3cd496f32f66460ade1` (`main`, merged PR #191)
**Started:** 2026-09-10

## Outcome required

An already-posted Dank Shield **Create Ticket** panel must remain usable after bot restart, acknowledge Discord on time, and show the server's own configured ticket choices without duplicate visible options. One guild's ticket configuration must never leak into or overwrite another guild's menu.

## Production evidence so far

- The original 2026-06-11 Create Ticket panel now opens successfully after the restart-runtime fix and telemetry deployment.
- Production click trace for guild `1514374173517152418` showed `listener_received age_ms=108`, `fallback_dispatch age_ms=260`, and `fallback_return age_ms=2239 response_done=True`.
- That proves Discord delivered the interaction promptly, the independent fallback recovered the missed persistent callback, and the interaction was acknowledged within Discord's response window.
- The remaining visible problem is category correctness: the menu showed only **Appeal**, **Report a Member**, and **Support** instead of the guild's saved ticket choices.

## Root cause

- `commands_ext.public_ticket_panel_clean` had its own second ticket-category implementation: a local hard-coded fallback catalog, broad keyword canonicalization, direct `ticket_categories` table reads, and local dedupe.
- The repository already has one authoritative category service in `tickets_new.managed_category_service`. That service owns catalog reconciliation, saved setup-v2 selections, exact-alias duplicate repair, custom-row preservation, visible-label dedupe, and per-guild scoping.
- The clean panel bypassed that service, so it could render stale `is_enabled` rows and rewrite or collapse legitimate custom categories based on words such as `support`, `help`, `report`, or `bug`.
- The authoritative reconciliation RPC explicitly treats completed setup-v2 `ticket_category_setup_selected_keys` as the source of truth and scopes every read/update by `guild_id`.

## Current execution path

`Create Ticket`
→ restart-safe runtime/fallback from PR #190/#191
→ canonical `public_ticket_panel_clean` handler
→ immediate interaction defer
→ ticket safety preflight
→ **managed_category_service.ensure_category_setup_state(guild.id)**
→ per-guild catalog reconciliation when needed
→ enabled-row + visible-label dedupe
→ preserve owner-created custom labels/descriptions
→ category picker
→ existing confirm/create path

## Changes in this branch

- Removed the clean panel's duplicate broad keyword category canonicalizer.
- `DEFAULT_ROWS` now derives from the canonical managed starter rows instead of maintaining a separate menu catalog.
- `_load_rows` now delegates to `managed_category_service.ensure_category_setup_state()` with the exact interacting guild ID instead of querying `ticket_categories` directly.
- Menu rows use the authoritative dedupe rules, which collapse true aliases and duplicate visible labels while preserving distinct custom rows.
- Custom `button_label`/`name` and description values are no longer rewritten to generic built-in labels merely because their text contains words like `support`.
- Verification routing uses the authoritative canonical category key.
- Timeout/error fallback still fails safely to the canonical starter categories.
- Added focused tests for custom-label preservation, true-alias dedupe, per-guild isolation, and the requirement that the clean loader not bypass the canonical category service.

## Multi-guild safety

- The menu loader passes only `guild.id` into the authoritative category service.
- The service reads `guild_configs` and `ticket_categories` with an exact `guild_id` equality filter.
- Managed reconciliation receives `p_guild_id` for a single guild during menu load.
- Existing custom rows stay distinct unless they are a true reserved alias or produce the exact same member-visible label.
- No global category selection is written by this panel change.

## Validation / results

Pending exact-head GitHub Actions for this branch.

Required validation:

- focused category-source tests
- existing ticket category setup/selection suite
- restart-runtime ticket tests
- full Dank Shield unit suite
- compileall and diff check
- managed-category SQL smoke test
- claim-first ticket security
- public setup/command/invite/safety audits
- final diff and review-thread inspection

## Cleanup / conflicts

- No second ticket creation path was added.
- The duplicate local category normalization is removed from the active panel path rather than layered over.
- Existing ticket creation, permissions, numbering, forms, confirmation, and persistence behavior remain unchanged.
- PR #191 telemetry remains available for production acceptance and can verify which guild loaded which resolved category keys.

## Blockers / risks

- Repository validation must pass before deployment.
- If the corrected canonical loader still resolves only the three starter keys for guild `1514374173517152418`, then the persisted setup selection itself is three starters and restoring an older selection requires database-history evidence rather than guessing category names.
- Production validation must click the existing June 11 panel in the user's guild and at least one other guild to confirm each receives only its own saved menu.

## Backlog

- **Server Design setup regression / full audit requested:** the Server Design wizard appears to snap back to **Rule Locks** after **Reset All Design Overrides** instead of keeping/resetting the intended design state. User reports the overall server design remains wrong and wants a top-to-bottom audit of setup so guild owners can configure their own server reliably. Screenshots show the Design Center with Rule Locks active, the destructive reset confirmation modal, and a saved Rule Locks summary immediately afterward. Treat as a separate implementation task after DS-TICKET-034 unless explicitly force-switched. Audit must cover state persistence, reset semantics, page routing, preview/save behavior, per-guild isolation, restart behavior, stale component/session state, and all design setup flows before declaring complete.
- Do not add global category resets or hard-coded guild-specific menu data without database evidence.

## Next step

Run exact-head CI, inspect the final diff, merge only if green, deploy the merged `main` to Discloud app `1777867264417`, then click the existing June 11 panel in guild `1514374173517152418` and one other guild. Confirm the log line `loaded canonical ticket choices guild=... keys=[...]` differs according to each guild's saved configuration and that no member-visible category label repeats.

---

PR #190 restored persistent runtime registration. PR #191 added live dispatch telemetry and proved the original panel can be acknowledged in production. This branch continues the same DS-TICKET-034 task by fixing the category-source split exposed after the button path began working again.
