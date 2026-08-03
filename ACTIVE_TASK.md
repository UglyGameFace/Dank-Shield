# ACTIVE TASK

## DS-STATS-019 — Durable Dank Stats invite-block counting

**Status:** ACTIVE — PR #167 FINAL CLEAN HEAD VALIDATION
**Branch:** `fix/dank-stats-live-recovery`
**Pull request:** `#167`
**Current source repair head:** `3fd5ca6a9c5c63d5a45f3fbc2f7e14ec01f9cf2b`
**Previous merged pull request:** `#166`
**Previous merge commit:** `2e89fd84b6c9c8e503c06782e4592a723a4c7c49`

## Live production failure

The user rebuilt Dank Shield after PR #166 and performed the required live test. The visible `🔗 Invites Blocked` counter did not update, so the task remained incomplete despite the prior green CI result.

## Root causes repaired

1. The migration-safe fallback always wrote the count into `settings`, while Dank Shield merges `settings → config → metadata → meta`. An older value in a later bucket could hide a successful write.
2. The first compatibility repair wrote the fully merged config into one selected bucket, which could move unrelated keys across compatibility layers.

## Implementation complete

- [x] Count the actual unique blocked invite codes approved by central invite policy.
- [x] Give each deleted Discord message a stable SHA-256 event identity.
- [x] Add a dedicated guild total and replay-safe event ledger.
- [x] Add one transactional RPC that seeds legacy history, inserts the event once, and increments atomically.
- [x] Keep tables and RPC service-role-only with RLS enabled.
- [x] Route every successful central-policy deletion through the durable service.
- [x] Queue failed writes with an on-disk retry outbox instead of silently discarding them.
- [x] Move outbox serialization and filesystem writes off the Discord event loop.
- [x] Recover pending events after restarts and late imports.
- [x] Reconcile guild totals with bounded startup concurrency.
- [x] Read the dedicated durable total for the visible Discord counter.
- [x] Coalesce prompt channel refreshes to avoid rename spam.
- [x] Fetch every compatible guild-config JSON bucket during fallback writes.
- [x] Mirror runtime bucket precedence when reading and verifying the visible count.
- [x] Update the bucket that actually owns the stats/event keys.
- [x] Use `updated_at` optimistic concurrency for fallback writes.
- [x] Verify merged readback contains the event hash and incremented count before reporting persistence.
- [x] Restrict the selected-bucket update to `security_stats_counts` and the fallback event ledger.
- [x] Preserve unrelated values in `settings`, `config`, `metadata`, and `meta` without promoting them.
- [x] Prefer canonical `settings` when no existing bucket owns the stats keys.
- [x] Remove all temporary patch scripts and privileged one-shot workflows from the final branch.

## Validation completed

- [x] Original durable implementation suite: `923 passed, 9 warnings`.
- [x] First live-recovery full suite: `925 passed, 9 warnings`.
- [x] Initial focused live-recovery suite: `18 passed`.
- [x] Bucket-isolation focused suite: `20 passed`.
- [x] Regression reproduces `settings.invites_blocked=2`, authoritative `config.invites_blocked=5`, and a two-code event yielding visible total `7`.
- [x] Regression proves unrelated `settings`, `metadata`, and `meta` values are not copied into `config`.
- [x] Regression proves the selected bucket's unrelated values remain intact.
- [x] Python compile and committed-whitespace checks passed on the first recovery head.
- [x] Public setup, command surface, command friction, invite permissions, setup safety, role truth, and event-boundary audits passed on the first recovery head.
- [x] Profile runtime and application command-size diagnostics passed on the first recovery head.

## Remaining Definition of Done gates

- [ ] Full unit suite and every audit pass on the final clean owner-authored head.
- [ ] Profile runtime and command-size diagnostics pass on the final clean head.
- [ ] Branch is current with `main` and has no unresolved review threads.
- [ ] PR #167 is merged.
- [ ] Dank Shield is rebuilt on Discloud from the repaired `main`.
- [ ] One message containing two unique blocked external invites is deleted once.
- [ ] The visible `Invites Blocked` counter increases by exactly `2`.
- [ ] Replay/edit/fallback handling does not increment that same message again.

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
