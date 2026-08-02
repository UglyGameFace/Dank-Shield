# ACTIVE TASK

## DS-STATS-019 — Durable Dank Stats invite-block counting

**Status:** ACTIVE — IMPLEMENTATION / REGRESSION VALIDATION
**Branch:** `fix/dank-stats-invite-block-counting`

## Why this task is active

The user explicitly directed work to move on from the completed setup redesign. `/dank setup` remains merged in PR #165; its remaining live screenshots no longer block this repair.

## Production defect

The old central invite-delete path incremented `invites_blocked` by exactly one deleted message, silently ignored failed stats writes, and relied on a delayed stats-channel refresh. It did not prove create/edit/fallback dedupe or restart persistence.

## Implementation scope

- [x] Count the actual unique blocked invite codes approved by central invite policy.
- [x] Give each deleted Discord message a stable SHA-256 event identity.
- [x] Add a dedicated guild total and replay-safe event ledger.
- [x] Add one transactional RPC that seeds legacy history, inserts the event once, and increments atomically.
- [x] Keep tables and RPC service-role-only with RLS enabled.
- [x] Route every successful central-policy deletion through the durable service.
- [x] Remove silent stats failure handling and emit actionable warnings.
- [x] Queue failed writes with an on-disk retry outbox.
- [x] Reconcile durable totals back into the existing visible Dank Stats compatibility counter.
- [x] Coalesce prompt Discord channel refreshes to avoid rename spam.
- [x] Reconcile durable totals for all connected guilds after startup.
- [x] Retain a bounded guild-config CAS fallback during rolling migration visibility.

## Definition of Done

- [ ] Focused durable invite stats tests pass.
- [ ] PostgreSQL migration applies twice successfully.
- [ ] SQL smoke test proves first event increments by its actual count.
- [ ] SQL smoke test proves replaying the same event does not increment again.
- [ ] Central policy test proves successful deletion calls the durable recorder.
- [ ] Failure test proves a write is queued rather than silently discarded.
- [ ] Full unit suite passes.
- [ ] Python compilation and whitespace checks pass.
- [ ] Public setup, command surface, invite permissions, setup safety, role ownership, and event ownership audits pass.
- [ ] Command-size and profile-runtime diagnostics pass.
- [ ] Branch is current with `main` and has no unresolved review threads.
- [ ] PR is merged.
- [ ] Discloud rebuild completes and live invite test increments the visible counter by the actual blocked-code count.

## Previous completed implementation

### DS-SETUP-018 — Easiest possible `/dank setup` flow

- PR #165 merged at `52afa3ce60ab51ff6726d298bb08822db43e1f84`.
- Full validation passed with `907 passed, 9 warnings`.
- Setup Home, guided configuration, separate automatic check, linear real-feature testing, and gated finish are implemented.

## Later backlog

### DS-MEDIA-001 — Klipy direct GIF unfurl listener

After Stats is complete, add one moderation-safe listener with strict host validation, bounded requests, redirect validation, caching, bot-loop prevention, permission/error handling, and parser/listener/security regressions.

## Single Active Task Lock

Do not begin another unrelated repair until DS-STATS-019 reaches its Definition of Done.
