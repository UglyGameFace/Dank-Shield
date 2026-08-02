# ACTIVE TASK

## DS-STATS-019 — Durable Dank Stats invite-block counting

**Status:** ACTIVE — EXACT-HEAD REGRESSION VALIDATION
**Branch:** `fix/dank-stats-invite-block-counting`
**Pull request:** `#166`

## Production defect

The old central invite-delete path incremented `invites_blocked` by exactly one deleted message, silently ignored failed stats writes, and relied on a delayed stats-channel refresh. It did not prove create/edit/fallback dedupe or restart persistence.

## Implementation complete

- [x] Count the actual unique blocked invite codes approved by central invite policy.
- [x] Give each deleted Discord message a stable SHA-256 event identity.
- [x] Add a dedicated guild total and replay-safe event ledger.
- [x] Add one transactional RPC that seeds legacy history, inserts the event once, and increments atomically.
- [x] Keep tables and RPC service-role-only with RLS enabled.
- [x] Route every successful central-policy deletion through the durable service.
- [x] Remove silent stats failure handling and emit actionable warnings.
- [x] Queue failed writes with an on-disk retry outbox.
- [x] Move outbox serialization and filesystem writes off the Discord event loop.
- [x] Protect concurrent outbox replacements with a file lock.
- [x] Recover restored pending events even when the module loads after Discord is already ready.
- [x] Reconcile guilds with bounded concurrency rather than serial startup reads.
- [x] Reconcile durable totals back into the existing Dank Stats compatibility counter.
- [x] Read the dedicated durable ledger directly when rendering the visible Discord counter.
- [x] Preserve the larger durable total when legacy or mixed config JSON contains an older value.
- [x] Coalesce prompt Discord channel refreshes to avoid rename spam.
- [x] Retain a bounded guild-config CAS fallback during rolling migration visibility.
- [x] Use the required unique 14-digit Supabase migration timestamp.

## Validation completed

- [x] Focused review suite: `16 passed`.
- [x] Central policy deletion calls the durable recorder with the full decision.
- [x] Failed writes are queued rather than silently discarded.
- [x] Late-import recovery scheduling regression passes.
- [x] Bounded concurrent startup reconciliation regression passes.
- [x] Async outbox persistence regression passes.
- [x] PostgreSQL migration applies twice successfully.
- [x] SQL smoke test proves seed `5` plus three blocked codes produces total `8`.
- [x] SQL smoke test proves replaying the same event remains total `8` with `applied=false`.
- [x] SQL smoke test proves a second two-code event produces total `10`.
- [x] SQL smoke test proves exactly two unique ledger rows exist.
- [x] SQL permission test proves anon/authenticated cannot read the tables.
- [x] SQL permission test proves only the service role receives RPC execution.
- [x] Dedicated visible-counter overlay regression passes.

## Remaining Definition of Done gates

- [ ] Full unit suite passes on the exact owner-authored head.
- [ ] Python compilation and whitespace checks pass on the exact head.
- [ ] Public setup, command surface, invite permissions, setup safety, role ownership, and event ownership audits pass.
- [ ] Command-size and profile-runtime diagnostics pass.
- [ ] Branch is current with `main` and has no unresolved review threads.
- [ ] PR is merged.
- [ ] Discloud rebuild completes and a live invite test increments the visible counter by the actual blocked-code count.

## Previous completed implementation

### DS-SETUP-018 — Easiest possible `/dank setup` flow

- PR #165 merged at `52afa3ce60ab51ff6726d298bb08822db43e1f84`.
- Full validation passed with `907 passed, 9 warnings`.

## Later backlog

### DS-MEDIA-001 — Klipy direct GIF unfurl listener

After Stats is complete, add one moderation-safe listener with strict host validation, bounded requests, redirect validation, caching, bot-loop prevention, permission/error handling, and parser/listener/security regressions.

## Single Active Task Lock

Do not begin another unrelated repair until DS-STATS-019 reaches its Definition of Done.
