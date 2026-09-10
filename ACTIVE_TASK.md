# ACTIVE TASK

## DS-INVITE-035 — Restore automatic missed-invite reconciliation

**Status:** IMPLEMENTED / EXACT-HEAD VALIDATION PENDING
**Branch:** `fix/invite-reconcile-runtime-194`
**Base:** `95c9585949eb6f8692538c0b3830c1e2d1b5aba2` (`main`, merged PR #193)
**Started:** 2026-09-10

## Outcome required

Dank Shield must keep deleting blocked Discord invites live and automatically recover blocked invite messages missed during restart, reconnect, transient persistence failure, or a short event gap. Recovery must reuse the same central invite policy as live enforcement so same-server invites, exemptions, allowed channels/roles/users, and Link/Invite Shield settings cannot disagree between live and historical messages.

## Scope

`production boot -> live invite listener -> guild/policy persistence -> central invite policy -> delayed channel recovery -> ready/resume all-channel reconciliation -> permission checks -> central delete helper -> durable stats/modlog`

No ticket redesign, Server Design work, join-context schema repair, general memory optimization, or unrelated moderation changes belong in this task.

## Proven root causes

Production logs prove `invite_live_enforcer` still deletes newly observed invites, but showed no recovery-sweep/server-wide reconciliation telemetry and also showed transient Supabase 504s.

### 1. Recovery code was not on the production boot path

- `stoney_verify/invite_policy_engine.py` is the authoritative invite decision/delete implementation and already exposes `scan_channel_invites()`.
- `stoney_verify/startup_guards/discord_invite_blocker_runtime_guard.py` historically contained delayed recent-history sweeps, but the broad startup-guard loader is dormant during normal production boot.
- `stoney_verify/globals.py` directly installs the guaranteed live `on_message` enforcer. That explains the production `invite_live_enforcer` logs, but this path performs live deletion only.
- `/dank cleanup invites` and Protection Center history scans require staff action and do not repair a missed live window automatically.

Therefore an invite missed while the live event path was unavailable could remain indefinitely without manual cleanup.

### 2. Transient guild-config reads could poison the live policy cache

- `guild_config` retries transient database errors, but after exhausted reads it previously fell through to the same `unconfigured:isolated_public_fallback` object used for a genuine no-row guild.
- `get_guild_config()` then cached that false unconfigured result for 60 seconds.
- A transient Supabase 504 could therefore temporarily make an existing guild appear unconfigured and make a configured live invite-blocking feature appear OFF.
- `spam_guard.get_spam_settings()` already preserves cached/default runtime state on unavailable reads, so the divergent weak point was guild-config persistence semantics.

### 3. Dormant compatibility code duplicated invite recovery ownership

- The historical `discord_invite_blocker_runtime_guard` had its own channel sweep task map, sweep cooldown, delayed sweep loop, and listener installer.
- Its private sweep helper had no external callers.
- One older hard-block bridge still calls `_enforce_message` directly, so that compatibility entrypoint must remain until the legacy bridge is retired.
- Keeping the old sweep machinery beside the new native runtime would create two recovery implementations and future listener-duplication risk.

## Implemented changes

- Added native `stoney_verify.invite_reconciliation_runtime` with no independent delete policy.
- Installed it explicitly from real Discloud entrypoint `main.py` before app startup.
- Kept the proven globals live enforcer as the live delete owner.
- Invite-related creates/edits schedule a bounded delayed rescan of that channel.
- `on_ready` and `on_resumed` reconcile every eligible text channel where the bot has View Channel, Read Message History, and Manage Messages.
- All historical decisions/deletes go through `invite_policy_engine.scan_channel_invites()`.
- Automatic scans are capped at 250 recent messages per channel on ready/resume and 75 on live-event recovery.
- Reconciliation concurrency is capped at 2 channels and duplicate guild/channel work is coalesced.
- Unavailable policy/config state defers reconciliation rather than being treated as OFF; one bounded retry occurs after 15 seconds.
- Added a local sleep boundary so async tests never monkeypatch Python's global `asyncio.sleep`.
- `guild_config` now distinguishes `unavailable:*` from genuine `unconfigured:*` state.
- Transient/unavailable reads preserve any stale known-good guild config without refreshing its cache timestamp.
- Cold-start unavailable reads return a safe isolated fallback but are never cached as authoritative.
- Genuine successful no-row/unconfigured results remain cacheable exactly as before.
- Unavailable writes preserve prior cached truth instead of replacing it with a false fallback.
- Refactored `discord_invite_blocker_runtime_guard` into a compatibility bridge: it no longer owns sweep task/cooldown state or installs a second live listener; recovery delegates to `invite_reconciliation_runtime` while `_enforce_message` remains for the one legacy direct caller.

## Compatibility / safety invariants

- The central invite policy remains the only authority allowed to approve an invite deletion.
- Same-server invite behavior is unchanged.
- Exempt user/role/channel and explicitly allowed invite-code behavior is unchanged.
- Link Shield and Invite Shield policy semantics are unchanged.
- Durable invite statistics still use the existing central delete helper.
- `globals` is the single live listener owner and `invite_reconciliation_runtime` is the single recovery owner.
- The historical guard remains import-compatible for its legacy direct `_enforce_message` caller without owning another listener.
- Public guild config isolation remains enforced; unavailable fallbacks cannot inherit another guild's environment IDs.
- A genuine unconfigured guild remains distinguishable from a failed database read.
- No new Supabase tables or migrations are required.
- Ticket subsystem changes from PRs #190-#193 remain untouched.

## Regression coverage

`tests/test_invite_runtime_reconcile_194.py` covers:
- real production boot wiring;
- central-scanner-only recovery ownership;
- legacy guard delegation with no second listener/sweep state;
- idempotent listener installation;
- permission-gated all-channel recovery;
- disabled-policy no-scan behavior;
- unavailable-policy defer/no cooldown;
- empty-policy and `unavailable:*` config handling;
- one bounded policy retry;
- event-triggered recent-history recovery.

`tests/test_guild_config_transient_resilience_194.py` covers:
- unavailable config classification;
- transient DB failure vs genuine unconfigured state;
- stale known-good preservation on forced refresh;
- cold-start unavailable reads never entering the cache;
- genuine unconfigured results remaining cacheable;
- failed writes preserving previous cached truth.

## Validation required

- focused invite/reconciliation regressions;
- transient guild-config resilience regressions;
- invite extraction/safety and central-policy audits;
- durable invite stats regressions;
- compileall and committed-diff whitespace check;
- repository full pytest suite;
- existing setup/command/invite/role/event-boundary audits;
- changed-file scope and conflict-marker inspection;
- review-thread inspection;
- current-main/mergeability check;
- final exact-head SHA verification.

## Cleanup / conflicts

Invite ownership is now explicit: the globals listener owns live enforcement, the new reconciliation runtime owns automatic history recovery, and the historical runtime guard is only a compatibility bridge. Its duplicate sweep task maps/listener installation were removed rather than left dormant for somebody to accidentally reactivate later.

The guild-config change is shared code, but it is in the active invite execution path and directly fixes the transient 504 failure mode observed in production. Its behavior change is limited to unavailable reads/writes; authoritative DB results and genuine no-row behavior retain their previous semantics.

## Suspended / backlog

- **DS-TICKET-034 production acceptance:** PR #193 merged; historical Ticket Choices restore/cross-guild live acceptance remains suspended.
- **Join-context Supabase schema mismatch:** production reports missing `entry_confidence` in `guild_members` and `member_joins`.
- **Dank setup interaction/RSS spike:** production RSS rose sharply during setup interactions.
- **Server Design setup regression/full audit:** preserve after ticket acceptance.

## Production acceptance remaining

After merge/deploy, require `🧹 invite_reconcile` ready/resume telemetry. Verify a blocked invite deliberately left within the bounded history window is removed automatically without staff cleanup, while a permitted/same-server invite remains untouched. Also verify a transient persistence interruption no longer converts a known configured guild into a fresh `unconfigured` cache entry.

## Next step

Run exact-head CI and repository validation on the final branch head. If every code-side gate is green, update PR #194 with exact validation evidence and mark it ready for review. Production acceptance remains the final runtime gate after deployment.
