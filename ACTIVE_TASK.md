# ACTIVE TASK

## Active task / desired outcome

**QUIET-NOTICE-001 — Recoverable Quiet Server Notice auto-clear**

When visible human chat resumes, an auto-clear Quiet Server Notice must disappear without losing the durable message identity if Discord or storage fails transiently. The repair must remain safe for large multi-guild deployments and must not add database work to every human message.

## Scope

In scope:

- quiet-notice human-activity observation and persistence;
- live quiet-notice deletion and durable delivery clearing;
- startup/reconnect reconciliation for a previously delivered quiet notice;
- cross-process/shard safety for overlapping activity/delete/clear workers;
- bounded retry behavior;
- focused runtime/service/SQL/static regression coverage;
- additive migration for atomic runtime transitions.

Out of scope:

- redesigning normal channel stickies;
- changing unrelated Community Tools;
- redesigning the pre-existing 30-second full quiet-config watcher;
- unrelated startup/sharding, welcome-channel, font, verification, ticket, moderation, or design work.

## Status

**IMPLEMENTED — PR #285 draft; exact-head validation BLOCKED by GitHub Actions runner allocation failure**

Branch: `fix/quiet-notice-auto-clear-recovery-20260921`

PR: #285 — **Fix recoverable Quiet Server Notice auto-clear**

Validated code head before this task-record update: `a7ab19d44a351d1a9fab91bbc9beef65e452acfa`

Base/current `main`: `9f6a0f62a271a1a5c423f8d168bc732c2c9455ab`

## Findings / root cause

- The canonical Community Tools runtime still owns exactly one `on_message` listener; the regression is not a missing listener.
- The September 5 Community Tools hardening changed auto-clear ordering to persist delivery removal before deleting the Discord message.
- `delete_quiet_live_message()` / `_delete_previous()` could fail on Discord `Forbidden` / `HTTPException`, but the caller had already cleared `last_notice_message_id` and did not use the delete result.
- One transient Discord delete failure could therefore leave a live Quiet Server Notice whose durable message ID had already been forgotten. Later human messages had no message identity to retry, so the notice could stick indefinitely.
- The September 20/21 startup-recovery work does not modify `community_tools_runtime.py`; current `main` is still the PR base. This is an older Community Tools durability bug, not a conflict introduced by the newest startup change.

## Execution path

`/dank home` → Community Tools → Quiet Server Notice config → `ensure_community_tools_runtime()` → canonical `StickyRuntime.on_message()` → `_observe_quiet_activity()` → `_persist_quiet_activity()` → Discord delete → durable delivery clear.

Startup/reconnect path:

`StickyRuntime.on_ready()` → enabled quiet-notice load → `_reconcile_quiet_config()` → retry delete/clear when activity is newer than the delivered notice.

## Changes

- Activity persistence no longer clears quiet-notice delivery identity as a side effect.
- Activity persistence uses one narrow PostgreSQL RPC that monotonically updates `last_activity_at` while preserving delivery/config state.
- Auto-clear now records activity first, deletes the tracked Discord message second, and clears durable delivery identity only after deletion succeeds.
- Durable delivery clear uses a compare-and-clear PostgreSQL RPC keyed to the expected message ID so a stale worker cannot erase a newer delivery.
- Discord deletion now returns success/failure to the caller; `NotFound` remains idempotent success.
- Failed storage or Discord operations retain the tracked message ID for later retry.
- Per-guild retry backoff prevents a busy guild from retrying a failed auto-clear once per message.
- Startup/reconnect reconciliation honors the same recoverable ordering and retry backoff.
- Runtime hot-path RPCs do not allocate service-layer per-guild lock entries.
- Added migration `20260921042000_quiet_notice_atomic_delivery_clear.sql`.
- Added focused runtime, service, SQL, and static regression coverage.

## Scale / compatibility review

- No database lookup was added to every human message.
- Normal activity remains in-memory and coalesced by the existing persistence cadence.
- A real wake-up auto-clear performs one activity RPC, one Discord delete/fetch path, and one expected-ID clear RPC.
- Failure retries are bounded per guild.
- Correctness does not depend on a process-local service lock, so overlapping/sharded workers cannot clear a newer delivery.
- Existing public command surface and normal sticky behavior are unchanged.
- The pre-existing full quiet-config watcher remains O(N) every 30 seconds; proving/redesigning that scheduler for million-guild scale is separate backlog, not hidden inside this bug fix.

## Validation / results

Implementation review completed against current `main`:

- PR is mergeable and current `main` still matches the PR base.
- Changed files are limited to the Community Tools workflow, quiet-notice runtime/service, one additive migration, and focused tests/static guard.
- Runtime/service ordering and retry semantics have focused regressions in the PR.
- SQL migration covers monotonic activity, delivery preservation, expected-ID clear rejection/success, and service-role-only execution.

GitHub Actions validation is currently blocked by runner allocation, not by executed test failures:

- Initial workflow attempt: jobs completed with no runner steps.
- Manual failed-job rerun: attempt 2 again completed with no steps.
- Observed affected workflows include Community Tools focused/SQL, Schema Authority SQL, Application Command Size Diagnostics, Ticket Owner Emergency Override, and Dank Shield CI.
- Because no Python/pytest/compile/SQL steps executed, this task is **not** claimed complete or merge-ready.

## Cleanup / conflicts

- No second quiet-notice runtime/listener introduced.
- No generic retry/fallback layer added outside the affected lifecycle.
- No unrelated code changes are part of PR #285.
- Current `main` has not advanced past the PR base, so no rebase conflict exists at this checkpoint.
- The fix intentionally preserves the durable message ID on recoverable failures instead of creating orphaned live notices.

## Blockers / risks

- **Blocker:** GitHub Actions runner allocation is currently producing jobs with no executed steps, including the second attempt.
- The additive migration must be deployed with the application change. If code reaches production first, runtime transition RPC calls fail closed and retain the delivery ID rather than orphaning the notice.
- Million-guild scheduler architecture for the existing 30-second O(N) quiet watcher is not validated by this task.

## Backlog

Unrelated observations from the supplied production startup log, not investigated in this task:

- public startup warns that expected public guild count is 100+ while Discord auto-sharding is disabled;
- one guild reports a configured welcome-channel compatibility ID that no longer exists;
- `channel_font_rename_queue_guard` logs its startup line twice;
- quiet-notice full watcher O(N) scale architecture should receive a separate scale audit before claiming million-guild readiness.

## Next step

Obtain an exact-head CI attempt in which runners actually execute the workflow steps. If all applicable checks pass, inspect the final diff, workflow results, PR threads/comments, and migration scope on that exact head before marking #285 ready. After merge, verify the merged commit on `main` and then validate the live notice wake-up path.
