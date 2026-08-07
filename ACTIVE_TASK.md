# ACTIVE TASK

## DS-OPS-021 — Owner ticket authority, bulk moderation, and canonical join cards

**Status:** IN PROGRESS
**Branch:** `fix/ds-ops-021-owner-bulk-welcome`
**Base commit:** `6da87e0ce6a3fd2609d66f7c56d1a9e80c860c7a`
**Started:** 2026-08-06
**Force-switch reason:** Repair three confirmed production defects together without adding another competing patch layer.

## Scope

### A. Actual guild-owner ticket authority

1. The real Discord guild owner may use normal ticket controls on any ticket without claiming it first.
2. Owner actions must not silently steal or replace the existing claimant.
3. Other administrators and staff remain claimant-bound unless a separate explicit authorization system is later implemented.
4. Safe lifecycle rules remain: close before permanent delete, preserve transcript requirements, and attribute every action to the real actor.
5. Emergency Override remains available for recovery but is not required for ordinary owner actions.

### B. Safely confirmed bulk moderation

1. Expand the member-role browser bulk menu beyond reminders and role changes.
2. Support bulk Verify, Timeout, Remove Timeout, Kick, Ban, Add Role, Remove Role, and Reminder actions.
3. Require exact target preview, a moderation reason where applicable, and typed confirmation for destructive actions.
4. Re-fetch/re-check every target at execution time for actor permissions, bot permissions, hierarchy, protected roles, guild-owner protection, and target availability.
5. Use per-operation and per-target concurrency protection, idempotent interaction handling, and complete success/blocked/failed reporting.
6. Record each attempted member action in the activity feed without exposing private reminder contents.
7. Never allow bulk action against the guild owner, Dank Shield itself, or protected staff/control targets unless the existing canonical policy explicitly allows it.

### C. Welcome Card Studio and canonical live join-card runtime

1. Repair the entire Welcome Card Studio flow: command registration, opening the studio, navigation, saving, preview, uploads, enable/disable, channel selection, and error feedback.
2. Welcome Card Studio settings must control the actual live join image card.
3. Establish one authoritative `on_member_join` owner for public join output.
4. Remove/disable the legacy plain `dank_shield:join_leave_event:v3` public join-card path.
5. Preserve separate staff audit and leave-announcement routes.
6. Ensure theme, font, colors, custom background, shuffle settings, title/body, channel, and enabled state are read from fresh guild config.
7. Prevent duplicate public joins and remove competing legacy listeners deterministically.
8. A render or permission failure must be logged clearly and use one intentional fallback, never an unrelated legacy card.
9. Validate saved Studio values against the renderer/service contract so stale or malformed config cannot silently break the whole studio.

## Confirmed root causes before editing

- `member_role_browser_bulk.py` intentionally exposes only Reminder/Add Role/Remove Role and explicitly states kick, ban, and timeout are individual-only.
- `tickets_new/claim_policy.py` explicitly denies the guild owner all normal claimant-controlled actions and only allows `owner_emergency_*` actions.
- `member_lifecycle_router_guard.py` emits the plain `dank_shield:join_leave_event:v3` card and removes the listener that invokes `welcome_card_file()`.
- `welcome_member_events_guard.py` already contains the image-card send path, but it is not the authoritative listener.
- User reports the entire Welcome Card Studio is also nonfunctional; registration, interaction callbacks, persistence, preview/upload, and live-consumption paths must be traced before the join-card work is considered fixed.

## Work plan

- [x] Inspect every ticket-policy caller and test before changing owner semantics.
- [x] Inspect member-browser permissions, target guards, action locks, activity logging, and existing individual action implementations.
- [ ] Inspect Welcome Card Studio registration, UI callbacks, config writes, renderer inputs, startup-guard import order, and every member join/remove listener.
- [x] Implement canonical owner bypass without requester/owner-ID ambiguity.
- [x] Implement reusable bulk-action executor and confirmation UI.
- [ ] Repair Welcome Card Studio end to end.
- [ ] Consolidate public join handling into one listener/runtime service.
- [ ] Add targeted functional and regression tests for all three areas.
- [ ] Run syntax/static validation and focused test suite.
- [ ] Inspect final branch diff for temporary files, duplicate listeners, compatibility regressions, and conflicts.
- [ ] Open one implementation PR and merge only after validation/review gates pass.
- [ ] Deploy and perform live Discord verification before marking complete.

## Paused task

### DS-SETUP-020 — Entitled ID-verification setup selection and VC permissions regression

Merged in PR `#171`; only its live Discloud/Discord verification gate remains. It was explicitly paused by the user's FORCE SWITCH on 2026-08-06. Resume after DS-OPS-021 reaches its Definition of Done unless the user explicitly changes priority again.

## Preserved backlog

- DS-STATS-019 — Durable Dank Stats invite-block counting; live verification pending.
- DS-SETUP-019 — Cleanup confirmation modal crashes.
- DS-MEDIA-001 — Klipy direct GIF unfurl listener.
- DS-RESET-001 — Owner-authorized Emergency Reset and bulk cleanup console.

## Definition of Done

DS-OPS-021 is complete only after root-cause inspection, implementation, targeted and regression tests, syntax/static validation, cleanup, conflict inspection, one reviewed PR, merge, deployment, and live verification of owner ticket authority, all bulk moderation actions, the full Welcome Card Studio, and the canonical live join-card path.
