# ACTIVE TASK

## DS-WELCOME-EXIT-023 — Fix welcome placeholder rendering and add canonical Exit Card Studio

**Status:** IN PROGRESS — IMPLEMENTED, VALIDATING
**Branch:** `fix/ds-welcome-exit-023-canonical-cards`
**Base:** merged `main` at `e07575ca6b779adbfae78028bb9bd5418fdd2ce9`
**PR:** #177 (draft until all repository validation is green)
**Started:** 2026-08-07
**Force-switch reason:** Live welcome cards are leaking unresolved placeholders such as `{username}`, and leave events need a matching configurable Exit Card Studio/runtime with saved channel, text, theme, image/card settings, preview, enable/disable controls, and exactly one canonical leave sender.

## Confirmed production symptoms

1. A live Welcome Card Studio join post contains a literal unresolved `{username}` token in its embed title even though the message footer identifies `dank_shield:welcome_card_runtime:v1` as the live sender.
2. Leave events still use the router-owned hardcoded `dank_shield:leave_event:v4` gray embed instead of a configurable card runtime.

## Confirmed root causes before editing

1. Placeholder rendering was duplicated between `welcome_card_runtime.py` and compatibility setup code with exact case/spacing-sensitive `.replace()` logic, allowing known token variants to drift/leak.
2. `member_lifecycle_router_guard.py` delegated joins to `send_live_welcome_card()` but built/sent leave output itself through a separate hardcoded v4 path.
3. Welcome Card Studio owned join design settings while leave had only legacy text/channel/toggle settings and no canonical image/card studio.
4. The Join & Leave compatibility center mixed public lifecycle controls with staff-log language while the lifecycle router and `events.py` separately owned public/staff routes.
5. Multiple historical leave channel/toggle aliases require compatibility reads, but a new explicit Exit Studio setting must become authoritative once saved.
6. A legacy `public_member_lifecycle_logs.py` registrar still contains alternate public join/leave listeners, although no production command profile or caller currently registers it. Regression coverage now locks that registrar out of all command profiles.

## Implemented scope

1. Shared `lifecycle_template_renderer.py` now owns canonical live/preview lifecycle template resolution.
   - Case-insensitive known placeholders.
   - Harmless whitespace/zero-width variants supported.
   - Member/server/channel/count/account-age/join-time placeholders aligned.
   - Invite values accept real attribution when supplied and use explicit safe fallbacks otherwise.
   - Unknown owner-authored brace tokens remain untouched.
2. `welcome_card_runtime.py` now uses the shared renderer for live join title/body, fixing the `{username}` leak class without changing Welcome Card design behavior.
3. Canonical Exit Card pipeline added:
   - `exit_card_renderer.py` reuses the Welcome Card visual/theme/typography engine.
   - `exit_card_service.py` owns exit design resolution and compatibility.
   - `exit_card_runtime.py` owns the one public live leave delivery path.
4. Exit Card Studio added with:
   - enable/disable;
   - exact exit channel;
   - title/body editor;
   - theme;
   - font;
   - color modes/presets/custom hex;
   - deterministic shuffle;
   - preview;
   - clear artwork;
   - reset design;
   - refresh/navigation;
   - upload help.
5. `/dank welcome exit-card-upload` safely normalizes/stores separate Exit background artwork.
6. Existing validated custom font asset can be selected independently by Exit Studio without duplicating font storage.
7. Lifecycle router now delegates `on_member_remove` only to `send_live_exit_card()`; the hardcoded public `dank_shield:leave_event:v4` sender was removed.
8. Public join and exit runtimes both use per-member delivery locks/recent-delivery suppression.
9. Existing `welcome_leave_*`, `goodbye_*`, `leave_*`, and join/leave-route settings remain compatibility inputs. Explicit `exit_card_*` settings win once present.
10. A legacy configured leave route with no historical boolean continues through the new runtime so existing guilds do not silently lose leave delivery during migration.
11. Final compact `/dank welcome` retains Welcome commands and adds:
    - `exit-card-studio`
    - `exit-card-preview`
    - `exit-card-upload`
    with the same command-payload safety limit and idempotent registration.
12. Welcome setup home now presents static welcome content, canonical Join Card Studio, and canonical Exit Card Studio as distinct responsibilities.
13. Staff audit/modlog ownership remains separate from public join/exit cards.

## Work plan

- [x] Inspect live join runtime, card renderer/service, Join & Leave setup service, public welcome commands, lifecycle router, config writer, and final command compaction.
- [x] Confirm placeholder formatting duplication and hardcoded leave runtime ownership.
- [x] Inspect existing welcome/leave tests and standalone lifecycle audits before final validation.
- [x] Implement shared lifecycle template renderer and regression coverage.
- [x] Implement reusable exit-card renderer on the existing Welcome visual engine plus a real PNG render smoke test.
- [x] Implement canonical exit-card live runtime with compatibility fallbacks and duplicate suppression.
- [x] Replace router hardcoded leave sender with canonical Exit runtime while keeping staff audit separate.
- [x] Build Exit Card Studio UI plus direct slash-command upload entry point.
- [x] Update Welcome/Join/Exit setup copy so responsibilities are unambiguous.
- [x] Preserve Exit Studio commands after final `/dank` compaction and enforce the existing payload safety limit.
- [x] Add regressions for placeholder variants, exit route/gate/fallback/dedupe, real image render, command ownership, invalid emoji prevention, and listener uniqueness.
- [x] Open draft PR #177.
- [ ] Complete fresh-head targeted tests, full unit suite, compile/static checks, standalone tools, command diagnostics, and runtime diagnostics.
- [ ] Inspect final diff after validation for duplicate listeners, competing senders, config-key collisions, unsafe mention behavior, dead compatibility ownership, or broken legacy behavior.
- [ ] Mark PR #177 ready only when every required repository gate is green.
- [ ] Merge after final review/conflict check.
- [ ] Deploy merged `main` to Discloud.
- [ ] Live-verify a real join and leave: exact channels, no raw known placeholders, one card each, Exit Studio saves/previews/uploads, exit disable works, v4 absent, staff audit separate.
- [ ] Mark DS-WELCOME-EXIT-023 complete only after live verification.

## Current validation notes

- Application Command Size Diagnostics have passed on intermediate DS-WELCOME-EXIT-023 heads after adding the Exit Studio commands.
- Python compile/import has passed on intermediate heads.
- Claim-first ticket security and managed-category SQL smoke remained green on intermediate heads.
- A stale standalone lifecycle centralization audit was found during pre-CI review and updated to require the canonical Join/Exit runtimes rather than retired v3/v4 markers.
- Final validation must use the newest head after this task-status update; older green/intermediate runs are not merge evidence.

## Paused tasks

### DS-TICKET-CAT-022 — Repair duplicate ticket categories across all existing servers

PR #176 merged to `main` as `e07575ca6b779adbfae78028bb9bd5418fdd2ce9` with repository/SQL validation green. Its required live verification on a previously affected guild remains pending. Paused by the user's FORCE SWITCH on 2026-08-07.

### DS-OPS-021 — Owner ticket authority, bulk moderation, and canonical join cards

Implementation merged through PR #174 with Welcome Card Studio emoji hotfix PR #175. Remaining production verification/follow-up is superseded for join-card behavior by DS-WELCOME-EXIT-023 and otherwise remains paused.

### DS-SETUP-020 — Entitled ID-verification setup selection and VC permissions regression

Merged in PR #171; only its live Discloud/Discord verification gate remains. Previously paused by FORCE SWITCH.

## Preserved backlog

- DS-STATS-019 — Durable Dank Stats invite-block counting; live verification pending.
- DS-SETUP-019 — Cleanup confirmation modal crashes.
- DS-MEDIA-001 — Klipy direct GIF unfurl listener.
- DS-RESET-001 — Owner-authorized Emergency Reset and bulk cleanup console.

## Definition of Done

DS-WELCOME-EXIT-023 is complete only after root-cause inspection, one shared placeholder renderer, canonical join/exit live runtimes, a complete Exit Card Studio, preserved existing configuration compatibility, exactly one member-facing join sender and one member-facing leave sender, separate staff audit ownership, targeted/regression tests, compile/static/full-suite validation, cleanup/conflict review, one reviewed PR, merge, Discloud deployment, and live Discord verification that both join and leave cards resolve placeholders correctly and post exactly once to their configured channels.
