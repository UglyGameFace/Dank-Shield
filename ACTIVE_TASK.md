# ACTIVE TASK

## DS-STATS-019 — Durable Dank Stats invite-block counting

**Status:** ACTIVE — PR #167 REPAIR IMPLEMENTED; FINAL CI RUNNING
**Branch:** `fix/dank-stats-live-recovery`
**Pull request:** `#167`
**Current repair code head:** `1da80a363fc57cb9f32d1d383e7113b5379ba4fd`
**Previous merged pull request:** `#166`
**Previous merge commit:** `2e89fd84b6c9c8e503c06782e4592a723a4c7c49`

## Production defect repaired

The old central invite-delete path incremented `invites_blocked` by exactly one deleted message, silently ignored failed stats writes, and relied on a delayed stats-channel refresh. It did not prove create/edit/fallback dedupe or restart persistence.

The first merged repair still had one live compatibility defect: its migration-safe fallback always wrote the new count into the `settings` JSON bucket. Dank Shield reads compatible buckets in `settings → config → metadata → meta` order, so an older value in a higher-precedence bucket could hide the successful fallback write and leave the visible Discord counter stale.

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
- [x] Fetch every compatible guild-config JSON bucket during fallback writes.
- [x] Mirror the runtime bucket precedence when verifying saved counts.
- [x] Write to the highest-precedence bucket that actually owns the stats/event keys.
- [x] Verify merged readback before reporting the fallback event as persisted.
- [x] Preserve `_fallback_event_hashes` after the one-shot patch replacement.
- [x] Add a regression reproducing stale `settings` versus authoritative `config`.
- [x] Restrict fallback writes to the selected bucket's stats/event keys instead of copying the fully merged config.
- [x] Preserve unrelated values in every JSON compatibility bucket.

## Previous full validation

- [x] Exact reviewed head: `06f56ddae19f915e98a3cb367c6ac407ad428ae9`.
- [x] Full suite: `923 passed, 9 warnings in 489.51s`.
- [x] Focused review suite: `16 passed`.
- [x] Python compilation and committed-whitespace checks passed.
- [x] Central policy deletion calls the durable recorder with the full decision.
- [x] Failed writes are queued rather than silently discarded.
- [x] Late-import recovery scheduling regression passed.
- [x] Bounded concurrent startup reconciliation regression passed.
- [x] Async outbox persistence regression passed.
- [x] PostgreSQL migration applies twice successfully.
- [x] SQL smoke test proved seed `5` plus three blocked codes produces total `8`.
- [x] SQL smoke test proved replaying the same event remains total `8` with `applied=false`.
- [x] SQL smoke test proved a second two-code event produces total `10`.
- [x] SQL smoke test proved exactly two unique ledger rows exist.
- [x] SQL permission test proved anon/authenticated cannot read the tables.
- [x] SQL permission test proved only the service role receives RPC execution.
- [x] Dedicated visible-counter overlay regression passed.
- [x] Claim-first ticket security passed.
- [x] Managed category and ticket-counter SQL checks passed.
- [x] Public setup, command surface, command friction, invite permissions, setup safety, Dank Design, role truth, and event-boundary audits passed.
- [x] Application command-size and profile-runtime diagnostics passed.
- [x] `/dank` payload remained `1675/8000`.
- [x] PR #166 squash-merged into `main`.

## Current repair validation

- [x] The failed one-shot workflow was traced to deleting `_fallback_event_hashes` from the replacement span.
- [x] The clean source repair retains the helper and contains the bucket-precedence implementation.
- [x] Initial focused repair suite passed: `18 passed`.
- [ ] Bucket-scoped focused repair suite passes.
- [x] Regression produced the intended `config` bucket write and visible total `7`.
- [ ] Run normal repository CI on the owner-authored PR #167 head.
- [ ] Run the full regression suite and conflict/cleanup inspection.
- [ ] Merge PR #167 only after all checks pass.
- [ ] Rebuild Dank Shield Helper on Discloud from the merged `main`.
- [ ] Confirm startup completes without durable-invite-stats or migration errors.
- [ ] Record the current visible `🔗 Invites Blocked` value.
- [ ] Post one test message containing two different external Discord invite links in a private channel protected by Invite Shield.
- [ ] Confirm the message is removed and the visible counter increases by exactly `2` after the coalesced refresh.
- [ ] Confirm replay/edit/fallback processing does not add a duplicate count.

## Previous completed implementation

### DS-SETUP-018 — Easiest possible `/dank setup` flow

- PR #165 merged at `52afa3ce60ab51ff6726d298bb08822db43e1f84`.
- Full validation passed with `907 passed, 9 warnings`.

## Later backlog

### DS-SETUP-019 — Cleanup confirmation modal crashes

The live `Confirm Discord Cleanup` modal accepts `DELETE SETUP` but then throws `AttributeError` because `public_setup_cleanup.ConfirmDeleteModal.on_submit()` calls `public_setup_solid._safe_defer_modal`, which does not exist. Inspect the canonical modal defer/follow-up path, all cleanup modal callers, interaction-response guards, and setup cleanup regressions before implementing the repair. The submitted cleanup did not run after this exception.

### DS-MEDIA-001 — Klipy direct GIF unfurl listener

After Stats is live-verified, add one moderation-safe listener with strict host validation, bounded requests, redirect validation, caching, bot-loop prevention, permission/error handling, and parser/listener/security regressions.

## Single Active Task Lock

Do not begin another unrelated repair until DS-STATS-019 reaches its live Definition of Done.
