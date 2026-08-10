# ACTIVE TASK

## DS-COMMAND-UX-024 — Consolidate Dank Shield into action-complete mega menus

**Status:** IN PROGRESS — PRODUCTION REGRESSION FOLLOW-UP / VALIDATING
**Branch:** `fix/ds-command-ux-024-member-ban-responsiveness`
**Base:** merged `main` at `7c30736e941bf2fd9a9390d9719acfa59990e0fa`
**Original consolidation PR:** #178 merged 2026-08-09
**Follow-up started:** 2026-08-10

## Current scope

Finish the existing DS-COMMAND-UX-024 Definition of Done after live use exposed two important facts:

1. The reduced slash-command list is the intentional compact-v2 design, not command implementation deletion. The final Discord-visible normal roots are `/dank`, `/mod`, `/ticket`, `/tickets`, and `/verify`; `/dank` intentionally contains only `home` and `upload`. Retired entry points remain implemented behind the mega-menu centers.
2. A live member-menu ban was reported as stuck. The real destructive-action path was inspected end to end before editing: `/mod` or `/dank home` → Member Command Center → member selector → `MemberActionView` → `MemberDestructiveActionModal` → staff/safety checks → per-target `action_lock` → Discord kick/ban → `record_member_action` Supabase insert → interaction follow-up.

## Confirmed findings

- `public_command_surface_v2.install_compact_public_surface_v2()` explicitly enforces the compact command tree. The old subcommands were hidden from autocomplete by design; their services were not deleted.
- `MemberDestructiveActionModal.on_submit()` previously did not defer the modal interaction until after member refresh and safety checks.
- Destructive moderation previously held the per-target action lock while waiting for `record_member_action()`.
- `record_member_action()` used `asyncio.to_thread()` for the Supabase insert with no outer timeout. A slow or hung database request could therefore keep the interaction spinning and the member/action lock held indefinitely after Discord had already processed the ban/kick.
- The central `interaction_action_lock_guard` is currently an observe-first duplicate guard and is not the root cause of this wait.
- Healthchecks on 2026-08-10 also show repeated DOWN/UP flaps, so live deployment verification must distinguish bot availability from interaction-path latency.

## Follow-up changes

- [x] Create isolated DS-COMMAND-UX-024 follow-up branch from merged `main`.
- [x] Add a hard timeout to best-effort member-action audit writes and return success/failure instead of waiting forever.
- [x] Add safe audit timeout/failure diagnostics without logging the moderation reason.
- [x] Make destructive member modal submissions acknowledge Discord immediately before remote work.
- [x] Time-bound target refresh and destructive safety checks; on timeout, fail closed and perform no moderation action.
- [x] Time-bound Discord kick/ban requests and return an explicit uncertain-state warning if Discord does not confirm in time.
- [x] Release the per-target action lock before sending the user result and before Supabase audit logging.
- [x] Send the moderation result before best-effort audit logging; still attempt the audit in `finally`.
- [x] Add a regression test that simulates a never-returning audit write and proves it times out.
- [x] Add static ordering coverage proving destructive actions defer before remote work and audit only after the action lock is released and the result is sent.
- [ ] Run focused tests and Python compile/import validation on the follow-up head.
- [ ] Run the repository CI/regression gates and inspect the diff for conflicting/duplicate moderation paths.
- [ ] Merge only when the follow-up checks are green.
- [ ] Deploy merged `main` to Discloud.
- [ ] Live-check the actual Discord command tree and a representative Member Command Center moderation action end to end.
- [ ] Mark DS-COMMAND-UX-024 complete only after live verification.

## Intended compact command surface

- `/dank home` — complete control center.
- `/dank upload` — the single attachment command for Join background, Exit background, or custom card font.
- `/mod` — moderation/member center.
- `/ticket` — current ticket controls.
- `/tickets` — ticket queues/setup/routing/categories.
- `/verify` — verification status/repair center.
- `View Dank Profile` context command remains.

Every retired slash action must remain reachable through an authorized menu or the upload command.

## Cleanup / conflict status

- No second moderation implementation was added.
- Existing permission, hierarchy, protected-role, typed-confirmation, and owner/admin safety rules remain in the canonical member-action path.
- The observe-mode global interaction lock guard was not changed.
- The follow-up is intentionally limited to responsiveness/failure-bounding in the existing member destructive-action path plus its shared audit writer.
- Final duplicate/conflict inspection is pending CI validation.

## Paused tasks

### DS-WELCOME-EXIT-023 — Welcome placeholder rendering and canonical Exit Card Studio
PR #177 merged; final real join/leave live-event verification remains pending.

### DS-TICKET-CAT-022 — Repair duplicate ticket categories across all existing servers
PR #176 merged; affected-guild live verification remains pending.

### DS-OPS-021 — Owner ticket authority, bulk moderation, and canonical join cards
Merged through PR #174 plus Welcome Studio hotfix PR #175; remaining production follow-up is paused/superseded where applicable.

### DS-SETUP-020 — Entitled ID-verification setup selection and VC permissions regression
Merged in PR #171; live Discloud/Discord verification remains paused.

## Preserved backlog

- DS-STATS-019 — Durable Dank Stats invite-block counting; live verification pending.
- DS-SETUP-019 — Cleanup confirmation modal crashes.
- DS-MEDIA-001 — Klipy direct GIF unfurl listener.
- DS-RESET-001 — Owner-authorized Emergency Reset and bulk cleanup console.

## Definition of Done

DS-COMMAND-UX-024 is complete only when the public Discord command tree is intentionally small, every previously public action remains reachable through an authorized UI or the minimal upload command, existing safety/claim/owner/role/config guards are preserved, member destructive actions cannot hang indefinitely on prechecks, Discord requests, locks, or audit writes, help text matches the menu-first workflow, regression tests and CI are green, the reviewed follow-up is merged, Discloud is deployed, and live Discord verification confirms the compact autocomplete surface plus representative moderation, ticket, verification, setup, and upload actions work end to end.
