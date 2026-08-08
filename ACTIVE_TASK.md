# ACTIVE TASK

## DS-WELCOME-EXIT-023 — Fix welcome placeholder rendering and add canonical Exit Card Studio

**Status:** IN PROGRESS
**Branch:** `fix/ds-welcome-exit-023-canonical-cards`
**Base:** merged `main` at `e07575ca6b779adbfae78028bb9bd5418fdd2ce9`
**Started:** 2026-08-07
**Force-switch reason:** Live welcome cards are leaking unresolved placeholders such as `{username}`, and leave events need a matching configurable Exit Card Studio/runtime with saved channel, text, theme, image/card settings, preview, enable/disable controls, and exactly one canonical leave sender.

## Confirmed production symptoms

1. A live Welcome Card Studio join post contains a literal unresolved `{username}` token in its embed title even though the message footer identifies `dank_shield:welcome_card_runtime:v1` as the live sender.
2. Leave events still use the router-owned hardcoded `dank_shield:leave_event:v4` gray embed instead of a configurable card runtime.

## Confirmed root causes before editing

1. Placeholder rendering is duplicated between `welcome_card_runtime.py` and `welcome_event_services.py`. Both use exact case/spacing-sensitive `.replace()` loops, so preview and live behavior can drift and non-exact stored token shapes can leak publicly.
2. `member_lifecycle_router_guard.py` delegates joins to `send_live_welcome_card()` but builds/sends leave output itself via `_send_public_leave()`.
3. Welcome Card Studio owns join design settings (`welcome_card_*`) while leave has only announcement text/channel/toggles (`welcome_leave_*`, `goodbye_*`) and no image/card design studio.
4. The current Join & Leave Announcements center mixes member-facing join/leave controls with wording that describes leave as a private staff log, while the lifecycle router separately suppresses collisions with staff audit routes.
5. Multiple compatibility keys exist for leave channel/enabled state; the new runtime must read them safely but establish one canonical exit-card configuration owner.

## Scope

1. Create one shared lifecycle template renderer used by previews and live join/leave output.
   - Resolve canonical placeholders case-insensitively and tolerate harmless whitespace inside braces.
   - Support member/server/channel/count/account-age/join-time placeholders consistently.
   - Keep invite placeholders explicit when live invite attribution is unavailable rather than leaking raw braces.
   - Detect/sanitize unresolved known placeholder variants before any public send.
2. Make live join output use that shared renderer without changing the current Welcome Card Studio design behavior.
3. Add a canonical Exit Card Studio with separate saved exit configuration:
   - enable/disable;
   - exact exit channel;
   - exit title/body;
   - theme;
   - font;
   - color mode/custom colors;
   - custom background;
   - shuffle mode;
   - preview;
   - reset/clear controls;
   - safe upload commands where Discord requires attachments.
4. Reuse the hardened card rendering engine rather than introducing a second unrelated renderer.
5. Add `send_live_exit_card()` as the only member-facing leave sender and route `on_member_remove` through it.
6. Retire `dank_shield:leave_event:v4` public output while preserving separate staff audit/modlog listeners.
7. Preserve compatibility with existing `welcome_leave_*`, `goodbye_*`, and `leave_*` channel/toggle/text settings by mapping them into the new canonical runtime without forcing existing servers to redo setup.
8. Prevent duplicate join/leave delivery with per-member lifecycle locks/recent-delivery guards.
9. Update `/dank welcome` command/compaction ownership so Welcome Card Studio and Exit Card Studio both survive the final public command surface.
10. Add targeted/regression coverage for placeholder variants, exact live output, preview/live parity, exit channel routing, exit enable/disable, image fallback, duplicate suppression, custom asset settings, command ownership, and listener uniqueness.

## Work plan

- [x] Inspect live join runtime, card renderer/service, Join & Leave setup service, public welcome commands, and lifecycle router.
- [x] Confirm placeholder formatting duplication and hardcoded leave runtime ownership.
- [ ] Inspect final command compaction and all existing welcome/leave tests before editing command ownership.
- [ ] Implement shared lifecycle template renderer and regression coverage.
- [ ] Implement reusable join/exit card style resolution and exit image file rendering.
- [ ] Implement canonical exit-card live runtime with compatibility fallbacks and duplicate suppression.
- [ ] Replace router hardcoded leave sender with canonical exit runtime while keeping staff audit separate.
- [ ] Build Exit Card Studio UI plus direct slash-command entry/upload controls.
- [ ] Update Welcome/Join & Leave setup copy so responsibilities are unambiguous.
- [ ] Validate final `/dank welcome` command tree after compaction.
- [ ] Run targeted tests, full unit suite, compile/static checks, standalone tools, and runtime diagnostics.
- [ ] Inspect final diff for duplicate listeners, competing senders, config-key collisions, unsafe mention behavior, or broken legacy compatibility.
- [ ] Open one reviewed PR and merge only after all validation gates are green.
- [ ] Deploy to Discloud and live-verify one join and one leave with configured cards before marking complete.

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
