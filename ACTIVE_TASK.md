# ACTIVE TASK

## DS-STATS-019 — Durable Dank Stats invite-block counting

**Status:** MERGED — CODE REVALIDATED / LIVE DISCLOUD VERIFICATION PENDING
**Merged pull request:** `#167`
**Merge commit:** `e6e2ffaef9e75008d422f349a82b3b67ea22b673`
**Final clean owner head:** `5ab92f4fe1bdd8bc28cc6aa8add890d87aaa3013`
**Source repair head:** `3fd5ca6a9c5c63d5a45f3fbc2f7e14ec01f9cf2b`
**Previous merged pull request:** `#166`
**Previous merge commit:** `2e89fd84b6c9c8e503c06782e4592a723a4c7c49`

## 2026-08-04 code revalidation

The user asked to verify the stats repair before beginning the next ticket-security task.

- [x] Confirmed current `main` still contains merge commit `e6e2ffaef9e75008d422f349a82b3b67ea22b673`; no later runtime code change superseded the repair.
- [x] Confirmed the post-merge `main` workflow completed successfully.
- [x] Confirmed the workflow log reports `927 passed, 9 warnings` with Python compilation, standalone tools, and all configured audits green.
- [x] Re-inspected the real execution path: central invite policy records stats only after Discord deletion succeeds.
- [x] Re-inspected unique-code counting and stable guild/channel/message event hashing.
- [x] Re-inspected RPC-first persistence, compatibility-bucket CAS fallback, durable retry outbox, restart recovery, and deduplication.
- [x] Re-inspected visible stats rendering: the dedicated durable total is used as the authoritative floor and a successful write schedules a forced coalesced channel refresh.
- [x] Re-inspected automatic schema bootstrap: the committed durable-invite-stats migration is executed when a direct Postgres URL is configured.
- [x] Found no additional code change justified by the evidence.
- [ ] Live Discloud/Discord behavior remains unverified because no deployment logs or observed counter result after PR #167 are available in the repository or prior conversation evidence.

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
- [x] Final clean full suite: `927 passed, 9 warnings in 660.68s`.
- [x] Post-merge `main` workflow reran the full suite successfully: `927 passed, 9 warnings in 792.51s`.
- [x] Regression reproduces `settings.invites_blocked=2`, authoritative `config.invites_blocked=5`, and a two-code event yielding visible total `7`.
- [x] Regression proves unrelated `settings`, `metadata`, and `meta` values are not copied into `config`.
- [x] Regression proves the selected bucket's unrelated values remain intact.
- [x] Python compilation and committed-whitespace checks passed on the final clean head.
- [x] All standalone regression tools passed on the final clean head.
- [x] Public setup, command surface, command friction, invite permissions, setup safety, Dank Design, role truth, and event-boundary audits passed on the final clean head.
- [x] Claim-first ticket security and managed-category SQL smoke tests passed.
- [x] Profile runtime and application command-size diagnostics passed on the final clean head.
- [x] `/dank` payload remained `1675/8000`.
- [x] Branch was zero commits behind `main` before merge.
- [x] All review threads were resolved.
- [x] PR #167 squash-merged into `main`.

## Remaining Definition of Done gates

- [ ] Rebuild Dank Shield on Discloud from repaired `main`.
- [ ] Confirm startup completes without durable-invite-stats or migration errors.
- [ ] Record the current visible `🔗 Invites Blocked` value.
- [ ] Post one message containing two different external Discord invite links in a private channel protected by Invite Shield.
- [ ] Confirm the message is deleted once and the visible counter increases by exactly `2` after the coalesced refresh.
- [ ] Confirm replay/edit/fallback handling does not increment that same message again.

## Previous completed implementation

### DS-SETUP-018 — Easiest possible `/dank setup` flow

- PR #165 merged at `52afa3ce60ab51ff6726d298bb08822db43e1f84`.
- Full validation passed with `907 passed, 9 warnings`.

## Later backlog

### DS-SETUP-020 — Entitled ID-verification setup selection and VC permissions regression

Reported from guild `1357215261001912320`, which has explicit access to the ID Verification feature.

- The core-module picker incorrectly forces `Simple Verify` ON because `Voice Verify` is hard-wired to depend on it, even when this guild's intended verification path is ID Verification and no Simple Verify channel belongs in the setup.
- Custom module buttons are not independent or deterministic: selecting or deselecting one option can fail to apply or unexpectedly toggle several other options.
- The entitled setup flow must expose and persist the exact verification choice the owner makes, with ID Verification able to satisfy the verification dependency instead of silently enabling Simple Verify.
- Continue Setup must request only the roles, channels, and permissions required by the final selected modules; it must never create or require a Simple Verify channel when Simple Verify is OFF.
- The VC verification channel must apply the correct Unverified-member voice/video permission overwrites, including the intended view/connect/speak/video-stream behavior, without granting unrelated permissions or relying on stale role overwrites.
- Inspect the canonical setup state model, all preset/custom toggle callbacks, dependency normalization, saved-draft serialization, resume/back navigation, entitlement gates, setup plan rendering, channel creation/update path, permission reconciliation, callers, compatibility layers, and focused/regression tests before implementation.
- Add regressions for entitled and non-entitled guilds, every toggle independently, dependency transitions, repeated clicks, stale interaction state, resume/back, no-Simple-Verify setup completion, and exact VC overwrite reconciliation.

**Status:** BACKLOG — blocked by the Single Active Task Lock until DS-STATS-019 reaches its live Definition of Done or the user supplies the exact required `FORCE SWITCH` instruction.

### DS-SETUP-019 — Cleanup confirmation modal crashes

The live `Confirm Discord Cleanup` modal accepts `DELETE SETUP` but then throws `AttributeError` because `public_setup_cleanup.ConfirmDeleteModal.on_submit()` calls `public_setup_solid._safe_defer_modal`, which does not exist. Inspect the canonical modal defer/follow-up path, all cleanup modal callers, interaction-response guards, and setup cleanup regressions before implementing the repair. The submitted cleanup did not run after this exception.

### DS-TICKET-020 — Guild owner final override for ticket actions

GitHub issue `#168`. The actual Discord guild owner must have an exclusive final authorization override for protected ticket actions without granting the same blanket bypass to administrators, configured staff, or the bot owner. Implementation remains blocked until DS-STATS-019 reaches its live Definition of Done or the user supplies the exact required FORCE SWITCH instruction.

### DS-MEDIA-001 — Klipy direct GIF unfurl listener

After Stats is live-verified, add one moderation-safe listener with strict host validation, bounded requests, redirect validation, caching, bot-loop prevention, permission/error handling, and parser/listener/security regressions.

## Single Active Task Lock

Do not begin another unrelated repair until DS-STATS-019 reaches its live Definition of Done.
