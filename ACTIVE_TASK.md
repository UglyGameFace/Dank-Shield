# ACTIVE TASK

## DS-STICKY-029 — Smart sticky UX, preview/testing, and quiet-server notices

**Status:** COMPLETE — READY FOR REVIEW
**Branch:** `feat/ds-smart-stickies-029`
**Base:** `6f02f644b40f175da91190340a83c3d4ee81854c` (merged PR #183)
**Validated implementation head:** `9fd3180febd1ebdc88adca5d8638dd62b6f3c63a`
**Started:** 2026-08-12
**Completed:** 2026-08-12

## Previous task closure

DS-LIFECYCLE-028 is complete. PR #183 merged at `6f02f644b40f175da91190340a83c3d4ee81854c`; its exact final head `877797da635509a77c93b32cc5f84c59594277a7` passed Dank Shield CI including Python compile, the full unit suite, and standalone static/tool audits. No unresolved review threads remained.

## User request / scope

Improve Sticky Messages so the setup is easier to understand and less button-heavy, add a safe Preview/Test path, and add a generic inactivity-triggered notice that any Dank Shield server can configure. The inactivity use case must support partner/community destinations without being hard-coded to one server.

## Root-cause findings

- The old Sticky Center exposed nine controls at once with little state-aware guidance, so first-time setup read like an admin toolbox instead of a short workflow.
- A normal sticky could only be verified by saving it and immediately posting it live; there was no private preview or temporary test-post path.
- Sticky movement is event-driven by messages in the sticky channel. A true “no activity for N time” trigger cannot be implemented only inside `on_message`, because no event fires when nobody talks.
- The existing sticky row is keyed by channel, so overloading it with a server-wide inactivity notice would prevent a normal sticky and quiet notice from coexisting in the same channel.
- The canonical `StickyRuntime` was already the single message-listener owner and had to remain so; inactivity support extends that runtime instead of adding a competing listener or monkey patch.

## Implementation

- [x] Replaced the sticky button wall with a compact, state-aware main screen: Create/Edit, Preview/Test, Sticky Settings, Sticky Poll/Poll Controls, Quiet Server Notice, Server Stickies, and Community Tools.
- [x] Moved pause/resume, cadence, custom sender, and destructive removal into Sticky Settings.
- [x] Changed Create/Edit into a draft-first flow: edit -> private draft preview -> optional 30-second public test -> explicit Publish Sticky or Discard Draft.
- [x] Added private preview plus a temporary 30-second test for existing plain/embed/poll stickies without changing live delivery state; voting is disabled in poll previews/tests.
- [x] Preserved the latest live pause state, cadence, custom sender, and delivery message ID when publishing an edited draft so a stale preview cannot casually re-enable or duplicate the live sticky.
- [x] Added a separate service-role-only `dank_quiet_notices` table so a server-wide quiet notice can coexist with ordinary channel stickies.
- [x] Added one quiet notice per guild with destination channel, inactivity duration (5 minutes through 7 days), custom message, optional partner/community name + validated HTTPS link, auto-clear-on-activity, delivery state, and persisted last-human-activity timestamp.
- [x] Extended the canonical `StickyRuntime` with one background quiet checker and in-memory/throttled activity persistence; no channel-history scan and no second `on_message` owner were introduced.
- [x] Quiet notices send at most once per inactivity cycle, re-arm on the next real human message anywhere in the guild, ignore bot/webhook messages, and optionally remove the stale notice when activity resumes.
- [x] Added guided Quiet Server Notice setup/status/preview/test/pause/resume/remove flows with generic copy suitable for partner servers, secondary communities, game/community hubs, or normal off-hours messaging.
- [x] Preserved mention suppression, managed-webhook secrecy, poll persistence, existing one-sticky-per-channel behavior, and the canonical public command surface.

## Validation

- [x] Targeted quiet-trigger/service/runtime tests green.
- [x] Sticky surface/draft-preview/live-preview/quiet-setup/poll-routing tests green.
- [x] Community Tools static ownership/security checks green, including the single-listener and no-history-scan guards.
- [x] New quiet-notice SQL migration applies twice on PostgreSQL 16 and verifies RLS, grants, inactivity constraints, and service-role-only storage.
- [x] Focused module compilation green.
- [x] Full Dank Shield CI green on implementation head `9fd3180febd1ebdc88adca5d8638dd62b6f3c63a`: committed-diff whitespace, Python compile, full unit suite, standalone tool checks, public setup/command/invite/safety audits, role/event-boundary audits, managed-category SQL smoke, and claim-first ticket security all passed.
- [x] Dedicated Smart Stickies 029 workflow green on the same implementation head: focused Python regressions/static guard and quiet-notice SQL/RLS smoke both passed.
- [x] Final changed-file/diff inspection found no unrelated implementation, duplicate listener, monkey patch, history scan, stale community-runtime import, or conflicting storage owner.
- [x] PR #184 remained mergeable with no unresolved review threads during final inspection.

## Cleanup / conflict status

- `StickyRuntime.on_message` remains the only canonical Community Tools message listener.
- Quiet activity delivery and return-to-activity cleanup share one per-guild lock to prevent competing quiet-message state changes.
- The temporary test paths do not call sticky/quiet delivery persistence or move the live message.
- The old main-screen advanced controls were integrated into `StickySettingsView`; no duplicate advanced management path was retained on the main Sticky Center.
- Historical migration `20260811122504_community_tools.sql` remains immutable; quiet-notice persistence is isolated in `20260812224000_smart_sticky_quiet_notices.sql`.
- No open Sticky/Community Tools PR conflict or unresolved review thread remains.

## Blockers

None.

## Backlog

None added during DS-STICKY-029.

## Definition of Done

Met. A server manager can understand the Sticky Messages screen without guesswork, preview/test a draft before publishing it, safely preview/test an existing live sticky, configure a server-wide quiet notice with a clear inactivity threshold and optional partner/community destination, receive no more than one notice per quiet period, have that notice re-arm only after real human activity, and keep normal stickies/polls working unchanged. Targeted tests, SQL/RLS checks, compile/static validation, full CI, cleanup/conflict inspection, and review-thread inspection all passed on the validated implementation.