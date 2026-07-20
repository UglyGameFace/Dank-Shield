# ACTIVE TASK

## DS-LIVE-STATS-001 — Expand Discord-native Dank Shield live stats

**Status:** IN PROGRESS — VALIDATION PENDING
**Branch:** `feature/dank-shield-live-stats-expansion`
**Base:** current `main` after merged PR #102

## Single Active Task Lock

Do not switch to unrelated implementation work until this task reaches Definition of Done or the owner explicitly force-switches tasks.

## Scope

Expand the existing opt-in locked Discord voice-channel stats category without creating a competing stats system.

Add only real, auditable values:

- `👥 Members` — Discord guild member total.
- `🎫 Open Tickets` — current authoritative `tickets.status = open` rows.
- `🙋 Claimed Tickets` — current authoritative claimed state, including legacy/open rows that already carry an assignee.
- `✅ Closed Tickets` — current authoritative `tickets.status = closed` rows.

Keep the existing protection statistics:

- `🛡️ SpamGuard: ONLINE/OFFLINE`
- `🚫 Spam Blocked`
- `🔗 Invites Blocked`
- `⏱️ Timeouts Issued`
- `🔒 Quarantined`

Do not add invented estimates such as bots stopped, raids prevented, or users protected.

## Root Cause / Design Finding

The existing `stoney_verify/security_stats.py` system already owns the correct Discord-native display, guild-scoped durable protection counters, locked voice channels, saved channel IDs, and a rate-limited refresh worker. Building a second stats feature would duplicate ownership and create drift.

Ticket/member values are different from cumulative protection counters and must not be persisted as duplicate totals:

- member total is read from Discord's guild total, falling back to the local member cache only when Discord reports the guild is fully chunked;
- ticket totals are recalculated from the canonical `tickets` table on refresh;
- unavailable authoritative live-state reads render `N/A`, never a misleading zero.

The existing ~10-minute channel rename cadence remains in place to avoid Discord channel-edit rate-limit churn.

## Changes

- Expanded `STAT_CHANNEL_PREFIXES` with Members, Open Tickets, Claimed Tickets, and Closed Tickets.
- Added safe Discord member-count resolution that does not trust a partial member cache.
- Added read-only ticket lifecycle aggregation from the canonical `tickets` table.
- Treats an `open` ticket carrying `claimed_by` or `assigned_to` as claimed, matching repository normalization behavior.
- Keeps deleted tickets out of the public lifecycle counts.
- Renders `N/A` on unavailable member/ticket truth instead of false zeroes.
- Existing protection counters remain persisted exactly as before.
- Existing opted-in stats categories self-repair missing newly introduced stat channels during the normal refresh cycle.
- Updated the success copy from "Live SpamGuard stats" to "Live Dank Shield stats".
- Expanded behavioral tests for authoritative member count, ticket lifecycle classification, `N/A` fail-safe rendering, nine-channel creation, and migration/repair of an existing five-channel display.

## Validation

Pending:

- Committed diff whitespace check.
- Python compile validation.
- Full `pytest tests/` regression suite, including targeted live-stats tests.
- Standalone tools/audits required by CI.
- Final diff inspection against current `main`.
- GitHub Actions on final PR head.

## Cleanup / Conflict Inspection

- Reuses the existing `security_stats.py` owner instead of adding a second stats module.
- Does not persist duplicate member or ticket counters.
- Does not add per-event forced channel renames.
- Does not change the existing public `Live Stats` button/custom ID.
- Does not alter ticket write paths or member synchronization behavior.
- `main.py`, `sitecustomize.py`, and `usercustomize.py` remain untouched.

## Blockers

None known yet. Validation is required before this task can be called complete.

## Backlog

After this task is complete, separately audit authoritative verification/member-role data before considering stats such as Verified Members or Pending Verification.

## Definition of Done

- [x] Existing SpamGuard/protection stats remain intact.
- [x] Members uses Discord guild total or a proven-complete cache fallback.
- [x] Open/Claimed/Closed ticket numbers derive from authoritative ticket lifecycle state.
- [x] Unavailable live truth displays `N/A` instead of false zero.
- [x] Existing opted-in displays can gain the new channels without manual deletion/recreation.
- [x] No fake or estimated public metrics are introduced.
- [x] New behavior has targeted behavioral regression coverage.
- [ ] Full regression/compile/audits pass.
- [ ] Final diff contains only task-related permanent code/tests/task record.
- [ ] Final-head GitHub Actions pass.
- [ ] Merge/deploy requires explicit user approval.
