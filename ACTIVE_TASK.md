# ACTIVE TASK

## DS-SETUP-018 — Easiest possible `/dank setup` flow

**Status:** MERGED — PRODUCTION DEPLOYMENT / LIVE MOBILE VERIFICATION PENDING
**Pull request:** `#165`
**Merge commit:** `52afa3ce60ab51ff6726d298bb08822db43e1f84`

## Completed implementation

- One compact Setup Home with one obvious primary action.
- One direct feature-area picker; no normal Manage Setup → All Features maze.
- Short plan, guided-step, configuration-check, help, and ticket-menu cards.
- Repair and restart tools remain under Advanced.
- **Check Configuration** only reports automatic saved-role/channel/permission readiness.
- **Start Guided Test** begins real Discord behavior testing.
- The next unfinished enabled test opens automatically.
- **Mark Passed & Continue** saves the result and advances automatically.
- The test dropdown is optional and only used to jump to a specific test.
- **Finish Setup** appears only after every enabled test passes.
- Test confirmations persist inside the active setup session and invalidate when enabled features change.

## Safety / ownership

- Reuses the compact setup session cache and canonical completion owner.
- Reuses existing ticket-panel, test-ticket, and Verify-panel actions.
- Adds no slash command, competing setup owner, schema migration, role deletion, or channel deletion.
- Presentation code remains under `stoney_verify/setup_ui/`.

## Validation

- [x] Focused guided-test behavior tests passed.
- [x] Full unit suite: `907 passed, 9 warnings in 633.51s`.
- [x] Python compilation passed.
- [x] Committed whitespace check passed.
- [x] Managed ticket-category SQL smoke test passed.
- [x] Claim-first ticket security suite passed.
- [x] Application command-size diagnostics passed.
- [x] Profile runtime diagnostics passed.
- [x] Public setup audit passed.
- [x] Canonical public command-surface audit passed.
- [x] Public command/friction audit passed.
- [x] Public invite/permissions audit passed.
- [x] Setup safety audit passed.
- [x] Dank Design Smart Auto-Detect audit passed.
- [x] Role-truth ownership audit passed.
- [x] Event-boundary ownership audit passed.
- [x] `/dank` payload remained `1675/8000` with nine canonical global commands.
- [x] Branch was zero commits behind `main` with no unresolved review threads.
- [x] PR #165 squash-merged into `main`.

## Remaining Definition of Done gate

- [ ] Deploy/restart Dank Shield on Discloud.
- [ ] Live mobile check: Setup Home shows **Start Guided Test** when configuration is ready.
- [ ] Live mobile check: green test button opens the next enabled feature automatically.
- [ ] Live mobile check: **Mark Passed & Continue** advances without returning to the list.
- [ ] Live mobile check: optional jump menu still works.
- [ ] Live mobile check: **Finish Setup** appears only after all enabled tests pass.

## Next task — start immediately after setup is live-verified

### DS-STATS-019 — Durable Dank Stats invite-block counting

- count actual blocked invite codes, not merely one deleted message;
- use durable atomic guild-scoped increments;
- never silently swallow failed stats writes;
- safely retry or reconcile failed writes;
- refresh the visible counter promptly without Discord rename spam;
- deduplicate create/edit/fallback processing;
- test real policy delete → durable increment → display refresh → restart persistence.

## Later backlog

### DS-MEDIA-001 — Klipy direct GIF unfurl listener

After setup and stats are complete, add one moderation-safe listener with strict host validation, bounded requests, redirect validation, caching, bot-loop prevention, permission/error handling, and parser/listener/security regressions.

## Single Active Task Lock

Do not begin DS-STATS-019 or another unrelated task until DS-SETUP-018 reaches its live Definition of Done.
