# ACTIVE TASK

## DS-SETUP-018 — Easiest possible `/dank setup` flow

**Status:** ACTIVE — FINAL GUIDED-TEST REFINEMENT
**Branch:** `fix/setup-guided-test-finish`
**Previous merge:** PR `#164` / `bcc06b9cb6348061026372c0b7e08195670c45e3`

## Goal

Finish `/dank setup` as one seamless mobile-first path before starting any other repair.

## Completed foundation

- One compact Setup Home.
- One direct feature-area picker.
- No normal Manage Setup → All Features & Settings maze.
- Short plan, health, ticket-menu, and guided-step cards.
- Repair and restart tools live under Advanced.
- Check Configuration clearly means automatic saved-role/channel/permission validation.
- Real feature testing remains separate from configuration readiness.
- Test confirmations persist inside the current setup session and invalidate when enabled features change.

## Final refinement in progress

The checklist is being converted into a linear guided test:

1. Press **Start Guided Test**.
2. Dank Shield opens the next enabled feature automatically.
3. Perform the shown real Discord test.
4. Press **Mark Passed & Continue**.
5. Dank Shield automatically opens the next unfinished test.
6. **Finish Setup** appears only after every enabled test passes.

The test dropdown remains only as an optional jump control. It is no longer required for the normal path.

## Safety / ownership

- Reuses the compact setup session cache and canonical finish owner.
- Reuses existing ticket-panel, test-ticket, and Verify-panel actions.
- Adds no slash command, competing setup owner, schema migration, role deletion, or channel deletion.
- Presentation refinement remains under `stoney_verify/setup_ui/`.

## Definition of Done

- [x] Normal configuration has one obvious next action.
- [x] Configuration checks and real feature tests have distinct names and behavior.
- [x] Guided testing automatically advances to the next unfinished enabled feature.
- [x] Dropdown is optional rather than required.
- [x] Finish Setup remains gated by all enabled tests.
- [ ] Focused guided-test tests pass.
- [ ] Full unit suite, compile, command-size, profile-runtime, setup-safety, ownership, and whitespace gates pass.
- [ ] PR merged to `main`.
- [ ] Discloud deploy/restart completed.
- [ ] Live mobile verification confirms the full path is effortless.

## Next task — do immediately after setup is complete

### DS-STATS-019 — Durable Dank Stats invite-block counting

Fix Invites Blocked end to end:

- count the actual blocked invite codes, not merely one deleted message;
- use durable atomic guild-scoped increments;
- never silently swallow failed stats writes;
- retry or reconcile failed writes safely;
- refresh the visible counter promptly without Discord rename spam;
- deduplicate create/edit/fallback processing;
- test real policy delete → durable increment → display refresh → restart persistence.

## Later backlog

### DS-MEDIA-001 — Klipy direct GIF unfurl listener

After setup and stats are complete, add one moderation-safe listener with strict host validation, bounded requests, redirect validation, caching, bot-loop prevention, permission/error handling, and parser/listener/security regressions.

## Single Active Task Lock

Do not begin DS-STATS-019 or another unrelated task until DS-SETUP-018 reaches its Definition of Done.
