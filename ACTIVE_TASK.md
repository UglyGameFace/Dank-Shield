# ACTIVE TASK

## DS-STICKY-029 — Smart sticky UX, preview/testing, and quiet-server notices

**Status:** ACTIVE — ROOT CAUSE / IMPLEMENTATION
**Branch:** `feat/ds-smart-stickies-029`
**Base:** `6f02f644b40f175da91190340a83c3d4ee81854c` (merged PR #183)
**Started:** 2026-08-12

## Previous task closure

DS-LIFECYCLE-028 is complete. PR #183 merged at `6f02f644b40f175da91190340a83c3d4ee81854c`; its exact final head `877797da635509a77c93b32cc5f84c59594277a7` passed Dank Shield CI including Python compile, the full unit suite, and standalone static/tool audits. No unresolved review threads remained.

## User request / scope

Improve Sticky Messages so the setup is easier to understand and less button-heavy, add a safe Preview/Test path, and add a generic inactivity-triggered notice that any Dank Shield server can configure. The inactivity use case must support partner/community destinations without being hard-coded to one server.

## Root-cause findings

- The current Sticky Center exposes nine controls at once with little state-aware guidance, so first-time setup reads like an admin toolbox instead of a short workflow.
- A normal sticky can only be verified by saving it and immediately posting it live; there is no private preview or temporary test-post path.
- Sticky movement is event-driven by messages in the sticky channel. A true “no activity for N time” trigger cannot be implemented only inside `on_message`, because no event fires when nobody talks.
- The current sticky row is keyed by channel, so overloading it with a server-wide inactivity notice would prevent a normal sticky and quiet notice from coexisting in the same channel.
- The canonical `StickyRuntime` is already the single message-listener owner and must remain so; inactivity support should extend that runtime rather than add a competing listener or monkey patch.

## Planned implementation

- [ ] Make Sticky Center state-aware and reorganize controls around Create/Edit, Preview/Test, timing, sender, polls, quiet notice, and destructive actions.
- [ ] Add private exact-content/embed/poll preview plus an optional temporary public test that does not alter live sticky delivery state.
- [ ] Add a separate service-role-only `dank_quiet_notices` table so a server-wide quiet notice can coexist with ordinary channel stickies.
- [ ] Add one quiet notice per guild with destination channel, inactivity duration, custom message, optional partner/community name + HTTPS link, auto-clear-on-activity, delivery state, and persisted last-human-activity timestamp.
- [ ] Extend the canonical runtime with one background quiet checker and in-memory/throttled activity persistence; do not scan channel history or add another `on_message` owner.
- [ ] Send at most one quiet notice per inactivity cycle, re-arm on the next human message, and optionally remove the stale notice when activity resumes.
- [ ] Add a guided Quiet Server Notice setup/status/preview/pause/remove flow with generic copy that works for partner servers, secondary communities, game servers, or normal off-hours messaging.
- [ ] Preserve mention suppression, managed-webhook secrecy, poll persistence, and existing one-sticky-per-channel behavior.

## Validation required

- [ ] Targeted quiet-trigger/service/runtime tests green.
- [ ] Sticky surface/preview/quiet-setup tests green.
- [ ] Community Tools static ownership/security checks green.
- [ ] Community Tools SQL migration applies twice and verifies RLS/grants/constraints.
- [ ] Python compile/static validation green.
- [ ] Full Dank Shield CI green on exact final PR head.
- [ ] Final diff, stale-reference, duplicate-listener, dead-code, and review-thread inspection complete.

## Cleanup / conflict status

- Existing `StickyRuntime.on_message` remains the only canonical community-message listener.
- No open Sticky/Community Tools PR conflicts were found before starting this task.
- Historical migration `20260811122504_community_tools.sql` will remain immutable; the quiet-notice schema will use a new migration.

## Blockers

None currently.

## Backlog

None added during DS-STICKY-029 yet.

## Definition of Done

A server manager can understand the Sticky Messages screen without guesswork, preview or temporarily test a sticky before changing live delivery, configure a server-wide quiet notice with a clear inactivity threshold and optional partner/community destination, receive no more than one notice per quiet period, have that notice re-arm only after real human activity, and keep normal stickies/polls working unchanged. The exact final PR head must pass targeted tests, SQL/RLS checks, compile/static validation, full CI, cleanup/conflict inspection, and review-thread inspection.