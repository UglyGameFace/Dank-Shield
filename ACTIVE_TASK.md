# ACTIVE TASK

## Active task / desired outcome

**P0-SCALE-001 — Production startup recovery storm / crash hardening**

Make Dank Shield startup/reconnect recovery safe for a public, sharded deployment without losing authoritative repair behavior.

The bot must not launch overlapping full-guild Discord history/member sweeps or redundant database rewrites after `on_ready`. Recovery must be bounded per process, exclusive per guild, checkpointed where historical replay is required, and must not silently stop working after an arbitrary guild count.

## Status

**IMPLEMENTED ON `fix/startup-recovery-storm` — exact-head validation pending**

The supplied production excerpt proves a startup traffic storm and warning amplification, but it still does **not** contain the final fatal traceback/signal/OOM line. Do not claim the exact process-termination mechanism is proven until Discloud logs show it.

## Production evidence

Observed during the crashing startup:

- Invite Shield recovery scanned **58 channels / 2,348 historical messages** in one guild.
- Every scanned message could touch deprecated `Message.interaction`, producing the repeated discord.py 2.7.1 deprecation warning flood.
- Departed-member reconciliation reported **139 current members** and **234 marked departed** for one guild, then immediately continued to another guild.
- Activity continuity repair was also active during the same startup window.
- Invite recovery starts about 3 seconds after ready.
- `app.py` startup maintenance begins departed reconciliation about 5 seconds after ready.
- activity restart reconciliation defaults to starting about 20 seconds after ready.

## Root cause / architecture findings

The startup pressure was not one API call. Multiple independent owners were allowed to overlap:

1. `invite_reconciliation_runtime` performed all-channel history recovery on ready/resume with up to 250 messages per readable channel.
2. `events.py` scheduled a full member sync for every guild on ready.
3. That full member sync already included departed-member reconciliation.
4. The same `events.py` startup path then separately ran departed-member reconciliation again.
5. `app.py` separately ran departed-member reconciliation again five seconds after ready.
6. Departed reconciliation rewrote rows already marked departed, turning stale historical rows into repeated database writes.
7. `events.py` warmed invite attribution for every guild independently of the other recovery systems, including unnecessary vanity requests.
8. Startup ticket sync/backfill could inspect up to 50 recent messages per matched ticket channel on every boot.
9. Public startup maintenance silently sliced the guild list to `DANK_STARTUP_MAX_GUILDS=50`, trading load for incorrect behavior after guild #50.
10. The recovery systems had separate locks/queues, so they did not coordinate their Discord/database pressure across feature boundaries.

## New execution model

### Shared recovery budget

`startup_recovery_coordinator.startup_recovery_slot(guild_id, label)`

- one heavyweight recovery owner at a time for the same guild;
- a small configurable process-wide concurrency pool across different guilds;
- default `DANK_STARTUP_RECOVERY_MAX_CONCURRENT=2`;
- no permanent per-guild lock table: lock entries are removed after the final user exits;
- activity, invite recovery, member departure recovery, new-guild member bootstrap, and invite-cache warming share the same budget.

### Member truth

Normal restart:

`on_ready`
→ background startup runner
→ require durable pre-restart activity heartbeat
→ one departed-only reconciliation per configured guild
→ shared recovery slot
→ authoritative `fetch_members(limit=None)`
→ skip rows already durably marked departed
→ update only newly departed/stale rows.

New guild:

`on_guild_join`
→ delayed background bootstrap
→ shared recovery slot
→ one full member sync.

The old all-guild full member sync and duplicate `events.py` departed reconciliation were removed from startup ownership.

### Invite Shield history recovery

`on_ready` / `on_resumed`
→ read immutable pre-restart `member_activity_tracker_state.last_heartbeat_at`
→ reject missing/future/over-safe-limit recovery windows
→ fixed `after` / `before` downtime window
→ skip channels whose last message predates the window
→ shared recovery slot
→ bounded history scan only inside the downtime window
→ central invite policy remains the only deletion authority.

The pre-restart heartbeat is pinned in memory so activity tracking cannot advance the database heartbeat and accidentally collapse Invite Shield's pending recovery window.

### Invite attribution cache

Startup cache warm:

- shares the recovery coordinator;
- paced between guilds;
- skips `guild.invites()` when the bot lacks Manage Server;
- skips `guild.vanity_invite()` unless the guild advertises `VANITY_URL`.

### Ticket history backfill

Automatic all-guild startup ticket backfill is disabled by default.

`DANK_STARTUP_TICKET_BACKFILL=true` is now an explicit repair/migration mode rather than a permanent boot-time history crawl.

## Changes

- added `stoney_verify/startup_recovery_coordinator.py`;
- removed deprecated `Message.interaction` fallback from Invite Shield message-surface classification;
- added `after` / `before` support to the central invite history scanner;
- changed native Invite Shield startup recovery to use durable restart windows;
- added inactive-channel preflight from Discord snowflake timestamps;
- moved activity restart history work onto the shared recovery coordinator;
- pinned pre-restart activity heartbeats per guild for cross-system recovery consistency;
- removed `events.py` startup full-member-sync ownership;
- removed `events.py` duplicate departed-member reconciliation ownership;
- kept one canonical departed-only restart reconciliation in `app.py`;
- moved full member bootstrap to `on_guild_join`;
- suppressed database updates for rows already marked departed;
- bounded/paced invite cache warm through the shared recovery budget;
- added invite permission and vanity-feature preflights;
- made ticket startup history backfill opt-in;
- removed the silent 50-guild startup maintenance cutoff;
- documented new recovery controls in `.env.example`.

## Scale invariants

- No all-guild full member rewrite on every process restart.
- No arbitrary all-channel last-250 Invite Shield crawl when no durable recovery checkpoint exists.
- No same-guild heavy recovery overlap across activity, invite, member, or invite-cache paths.
- Cross-guild recovery concurrency is bounded per process/shard.
- No hard guild-count cutoff that silently abandons maintenance after guild #50.
- New guilds still receive one authoritative bootstrap.
- Live member/invite/ticket event ownership remains unchanged.

## Validation / results

Branch base:

- `main` merge commit for previous task: `373080f76eb255db92dff29abdfc0761be75345c`
- implementation branch: `fix/startup-recovery-storm`
- branch was created directly from that merged main head.

Focused regression coverage added/updated for:

- no deprecated `Message.interaction` access;
- Invite Shield fixed recovery-window forwarding;
- inactive-channel history avoidance;
- policy retry retaining the same recovery window;
- per-guild/process-wide recovery concurrency;
- recovery lock-state cleanup;
- no duplicate `events.py` startup member owners;
- new-guild-only full member bootstrap;
- no silent startup guild-count cutoff;
- ticket startup backfill default-off behavior;
- already-departed database write suppression;
- invite-cache permission/vanity request preflights.

Exact-head compile/pytest/workflow validation is still pending and must not be represented as passing yet.

## Previous completed slice

**PR #279 — Guard Verification Center role mapping persistence**

- merged to `main` as `373080f76eb255db92dff29abdfc0761be75345c`;
- production/test blobs on merged `main` match the validated PR blobs;
- focused role-mapping regression passed 6/6 before merge;
- GitHub-hosted Actions jobs repeatedly failed before step 1 with `steps=null`, so full CI was not falsely reported as green.

## Blockers / risks

- The exact Discloud process-termination line was not included in the supplied log excerpt. The startup storm is proven; the final kill mechanism is not.
- Discord/API and Supabase load after deployment must be observed with the process-health logs to confirm the production crash loop is gone.
- Very long Invite Shield downtime gaps intentionally fail bounded recovery rather than launching an unbounded historical crawl.

## Next step

Open a draft PR, validate the exact final head with focused and full repository checks where runners permit, inspect the final diff for unrelated changes, and only then mark the PR merge-ready. After merge, verify the exact production/test blobs on `main` before redeploying.
