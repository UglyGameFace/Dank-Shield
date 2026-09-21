# ACTIVE TASK

## Active task / desired outcome

**QUIET-NOTICE-001 — Recoverable Quiet Server Notice auto-clear**

When visible human chat resumes, an auto-clear Quiet Server Notice must disappear without losing the durable message identity if Discord or storage fails transiently. The repair must remain safe for large multi-guild deployments and must not add database work to every human message.

## Status

**IMPLEMENTED ON CURRENT MAIN BASE — exact-head executable validation pending**

Branch: `fix/quiet-notice-auto-clear-current-20260921`

Rebuilt from current `main` after PR #287 because the original PR #285 branch was stale. The six implementation/test blobs are intentionally identical to the reviewed #285 versions; only stale branch history/task metadata was discarded.

## Root cause

The Community Tools runtime could clear `last_notice_message_id` in durable state before confirming that the corresponding Discord Quiet Server Notice was actually deleted.

If Discord deletion then failed with a transient permission/API/storage error, the live notice remained visible while Dank Shield had already forgotten its message ID. Later human activity had no durable identity to retry, leaving the notice stuck indefinitely.

## Correct lifecycle

Human activity
→ record activity without touching delivery identity
→ delete the tracked Discord notice
→ only after successful/idempotent deletion, compare-and-clear the exact tracked message ID

Startup/reconnect uses the same recoverable ordering.

## Scope

In scope:

- `community_quiet_notice_service.py`;
- `community_tools_runtime.py`;
- additive atomic quiet-notice migration;
- Community Tools workflow SQL coverage;
- focused runtime/service/static regressions;
- bounded per-guild retry behavior.

Out of scope:

- normal sticky redesign;
- unrelated Community Tools;
- redesigning the pre-existing 30-second full quiet-config watcher;
- startup/rate-limit work from #286;
- Invite Shield work from #287;
- unrelated verification, tickets, moderation, design, fonts, welcome, or setup systems.

## Implementation

- activity persistence is non-destructive and updates only runtime activity fields;
- activity uses one narrow PostgreSQL RPC with monotonic timestamp semantics;
- Discord deletion happens before durable delivery identity is cleared;
- durable clear uses a compare-and-clear RPC keyed to the expected message ID;
- stale workers cannot erase a newer delivery;
- `discord.NotFound` remains idempotent deletion success;
- transient Discord/storage failures keep the delivery ID for retry;
- activity and startup/reconnect retries use bounded per-guild backoff;
- the runtime hot path does not allocate new service-layer per-guild lock entries;
- no second Community Tools message listener is introduced.

## Migration

`supabase/migrations/20260921042000_quiet_notice_atomic_delivery_clear.sql`

Adds service-role-only functions:

- `record_dank_quiet_notice_activity(bigint, timestamptz)`
- `clear_dank_quiet_notice_delivery(bigint, bigint)`

The migration is additive and changes no table shape. Application calls fail closed if the migration is missing, preserving the tracked delivery identity instead of orphaning a live notice.

## Scale properties

- no DB lookup is added to every human message;
- normal activity remains in-memory/coalesced;
- an actual auto-clear uses one activity RPC, one Discord delete/fetch path, and one expected-ID clear RPC;
- retries are bounded per guild;
- correctness does not depend on one process-local lock, so overlapping/sharded workers cannot clear newer delivery state.

The existing 30-second O(N) quiet-config watcher is separate backlog and is not silently claimed solved here.

## Validation required on exact final head

- `git diff --check`;
- Python compile;
- Community Tools focused regressions;
- quiet-notice hardening regressions;
- Community Tools static ownership checks;
- migration applied twice against PostgreSQL;
- SQL behavior/RLS/service-role smoke;
- full `tests/` suite;
- final changed-file/review-thread inspection.

## Previous branch disposition

PR #285 contains the original reviewed implementation but is based on stale `main`. It is superseded by the current-main rebuild and must not be merged.

## Next step

Open a fresh draft PR from this branch, close #285 as superseded, validate the exact new head in Termux/Ubuntu including PostgreSQL migration smoke, then merge only with an expected-head guard and verify the resulting files on `main`.
