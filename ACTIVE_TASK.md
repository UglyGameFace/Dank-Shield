# ACTIVE TASK

## Active task / desired outcome

**P0-RATE-001 — Stop Discloud global Discord API shutdowns**

Dank Shield Helper must not exceed Discloud's observed process-wide Discord API ceiling of **300 requests per 30 seconds** during startup/reconnect recovery. Recovery must remain correct for many guilds without delaying live moderation/invite enforcement or silently abandoning authoritative member/activity safety.

## Production incident evidence

Discloud reported:

`App shutdown - Rate limit exceeded: 301/300 req/30s`

The bot connected successfully before the host terminated it. Production startup logs also showed two `activity_restart_reconcile` jobs running concurrently through the shared startup pool.

## Scope

In scope:

- authoritative member activity restart-gap reconciliation;
- startup/resume invite history recovery;
- authoritative full member enumeration used by recovery;
- the existing live `startup_guards.discord_api_safety` owner;
- startup recovery concurrency contracts, timeouts, configuration, and focused regressions;
- recent changes that removed/changed request-serialization behavior.

Out of scope:

- unrelated verification, ticket, design, profile, welcome, AntiNuke, or Community Tools redesigns;
- normal live invite enforcement latency;
- changing Discord business rules;
- sharding architecture beyond recording any separate scale blocker discovered here.

## Status

**IMPLEMENTING / VALIDATING — root cause confirmed; focused repair branch active**

Branch: `fix/discloud-global-rate-limit-20260921`

Base: `main` at `9f6a0f62a271a1a5c423f8d168bc732c2c9455ab`

## Findings / root cause

- `activity_reconciliation.py` is intentionally bounded but REST-heavy. A restart-gap scan can enumerate archived public threads, archived private threads, and then message history for every readable channel/thread.
- Default message history limit is 251 items including overflow detection, which can require up to three 100-message REST pages per channel.
- A startup log such as `channels=23 messages=1` therefore does **not** mean 23 Discord requests; empty/sparse channels still require thread/history API probes.
- Before PR #280, `activity_tracker.py` enforced `_STARTUP_RECONCILE_LOCK = asyncio.Lock()` with the explicit invariant: **only one guild may consume Discord history APIs at a time**.
- PR #280 removed that dedicated lock and replaced it with `startup_recovery_slot()`, whose default shared process concurrency is 2.
- Production logs after that change show two `activity_restart_reconcile` jobs overlapping.
- That change allowed two high-fanout Discord history/thread recovery scans to overlap. Combined with member enumeration, invite recovery, command/status/startup traffic, this makes the observed 301/300 host shutdown directly plausible.
- Existing regression coverage was contradictory: `test_activity_restart_responsiveness_static.py` still required `_STARTUP_RECONCILE_LOCK`, while PR #280 changed `test_startup_recovery_scaling.py` to require that the lock be absent.
- Recent GitHub Actions jobs have also been completing without runner steps, so that contradiction was not reliably executed before merge.
- The existing authoritative `discord_api_safety.py` already owns audit/send/channel-edit rate safety but had no process-wide budget for history/thread/member recovery REST traffic.

## Execution path inspected

- `main.py` explicitly loads `startup_guards.discord_api_safety`.
- `activity_tracker._on_ready()` schedules staggered per-guild restart recovery.
- `_run_scheduled_guild_tracking()` owns the activity startup worker.
- `reconcile_restart_gap()` collects messageable channels/threads and walks bounded history.
- `invite_reconciliation_runtime` owns startup/resume invite catch-up and live event recovery.
- `membership_authority.collect_membership_snapshot()` owns authoritative `guild.fetch_members(limit=None)`.
- status reporting, live profile reconciliation, command sync, kick/member-wait timer recovery, and invite-policy preflight were inspected as neighboring startup traffic.
- Live profile deep reconciliation is already lazy above five guilds and is not the incident's dominant request source.
- Startup invite recovery skips inactive channels by durable restart window and was not the dominant source in the supplied logs.
- The observed `No member wait timers resumed from persistence/live state` startup path owned a second raw all-guild `guild.fetch_members(limit=None)` sweep beginning two seconds after ready, separate from departed-member reconciliation. That duplicate full-member enumerator was also contributing uncoordinated startup REST traffic.

## Changes

- Restored the dedicated single-flight `_STARTUP_RECONCILE_LOCK` around activity history recovery.
- Kept PR #280's shared startup coordinator for unrelated recovery work.
- The activity-specific lock is acquired **before** the shared startup slot so a waiting history job cannot waste one of the global startup slots.
- Extended the existing `discord_api_safety` owner with a process-wide, sliding 30-second **recovery REST budget**.
- Default recovery budget is **100 reserved requests / 30 seconds**, leaving substantial headroom below Discloud's observed 300/30 process ceiling for normal Discord traffic.
- Added conservative page weighting: a bounded history request for 251 items reserves three request slots; member enumeration reserves by 1000-member pages.
- Activity archived-thread enumeration and history pages reserve from that shared budget.
- Startup/resume invite history scans reserve from the shared budget.
- **Live invite event recovery does not use the startup recovery budget**, preserving moderation responsiveness.
- Authoritative full-member enumeration reserves from the same budget so it cannot collide unchecked with history recovery.
- Member-wait timer live-state startup recovery no longer owns a duplicate raw `fetch_members(limit=None)` implementation; it reuses canonical `collect_membership_snapshot()` and therefore the same recovery budget/fallback behavior.
- Raised the default activity reconciliation timeout from 90s to 180s because safe pacing may legitimately extend large-guild recovery.
- Added production env examples for the recovery budget and paced timeout.
- Reconciled the contradictory startup tests and added deterministic recovery-budget coverage.

## Scale / compatibility reasoning

- This is not a blanket sleep and does not patch all discord.py REST methods.
- Normal messages, interactions, AntiNuke priority audit reads, and live invite enforcement remain outside the recovery budget.
- Multiple recovery subsystems share one process-wide allowance instead of each believing its own concurrency is safe.
- A larger guild becomes a paced backlog rather than a burst that kills the process.
- The shared startup coordinator may still run unrelated jobs concurrently while activity Discord-history scans remain single-flight.
- The budget is configurable but clamped below the known host limit.

## Validation / results

Implemented focused regression coverage for:

- conservative REST page weighting;
- sliding-window pacing when the budget is full;
- default headroom below 300/30;
- activity history single-flight ordering;
- activity history/thread use of the shared budget;
- startup invite scans using the budget;
- live invite recovery bypassing the startup budget;
- authoritative member enumeration using page-weighted budget;
- member-wait timer startup reusing canonical membership authority instead of a second raw full-member sweep;
- production env defaults.

Validation is **not complete yet**. Exact branch compile/tests and final CI/runtime evidence are still required before any completion claim.

Current validation blockers observed:
- GitHub Actions jobs are again completing with `steps=null` and no runner-executed logs, including the generic Python compile job.
- The local sandbox cannot resolve `github.com`, so it cannot clone/materialize the connected repository independently for an exact-head full test run.

## Cleanup / conflicts

- No second Discord API safety owner was introduced.
- No generic retry layer was stacked on discord.py.
- The existing `discord_api_safety` module remains authoritative.
- The dedicated activity lock restores a proven old invariant rather than adding a new competing runtime.
- Unrelated product systems are unchanged.

## Blockers / risks

- GitHub Actions has recently produced runnerless failures with no executed steps. Exact-head CI must be inspected for actual step execution, not merely conclusion labels.
- The recovery budget reserves conservatively from known bounded iterators; normal live Discord traffic is protected by leaving 200/30 requests of observed host headroom rather than attempting to globally monkey-patch every REST route.
- Public rollout still has a separate sharding warning (`DANK_EXPECTED_PUBLIC_GUILDS=100+` while auto-sharding was disabled in the supplied deployment log). That is backlog unless it is proven necessary to this incident.

## Backlog

- Audit production sharding configuration separately before large public rollout.
- Investigate duplicate `channel_font_rename_queue_guard` startup log line separately.
- Investigate stale configured welcome-channel compatibility ID separately.

## Next step

Run exact-branch source compile and focused regressions, inspect all changed files/diff for accidental scope expansion, open a focused draft PR, then require an exact-head CI attempt that actually executes steps. After merge, verify a real Discloud restart no longer approaches the 300/30 host shutdown threshold.
