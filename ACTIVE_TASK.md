# ACTIVE TASK

## DS-INVITE-035 — Restore automatic missed-invite reconciliation

**Status:** IMPLEMENTING / VALIDATION PENDING
**Branch:** `fix/invite-reconcile-runtime-194`
**Base:** `95c9585949eb6f8692538c0b3830c1e2d1b5aba2` (`main`, merged PR #193)
**Started:** 2026-09-10

## Outcome required

Dank Shield must continue deleting blocked Discord invite links live, and must also recover blocked invite messages that were missed during a restart, reconnect, transient live-enforcement failure, or short event gap. Recovery must reuse the same central invite policy as live enforcement so same-server invites, exemptions, allowed channels/roles/users, and Link/Invite Shield settings cannot disagree between live and historical messages.

## Scope

`real production boot path -> live invite listener -> central invite policy -> bounded delayed channel recovery -> ready/resume all-channel reconciliation -> permission checks -> central delete helper -> durable invite stats/modlog`

No ticket redesign, Server Design work, join-context schema repair, general memory optimization, or unrelated moderation changes belong in this task.

## Findings / root cause

Production logs prove `invite_live_enforcer` is deleting newly observed invites successfully, but contain no fallback-sweep or server-wide reconciliation telemetry.

The repository contains the missing recovery pieces, but they are not on the real boot path:

- `stoney_verify/invite_policy_engine.py` is the authoritative invite decision/delete implementation and already exposes `scan_channel_invites()`.
- `stoney_verify/startup_guards/discord_invite_blocker_runtime_guard.py` contains delayed recent-history sweeps, but the broad startup-guard loader is dormant during normal production boot.
- `stoney_verify/globals.py` installs the guaranteed live `on_message` enforcer directly, which explains the production `invite_live_enforcer` logs. That path performs live deletion only.
- `/dank cleanup invites` can manually scan old messages, and Protection Center can scan a selected channel, but neither provides automatic server-wide restart/reconnect recovery.
- Therefore blocked invites missed while the live event path was unavailable can remain until staff manually run cleanup.

## Implementation

- Add a native `stoney_verify.invite_reconciliation_runtime` service with no independent delete policy.
- Install it explicitly from `main.py`, which is the actual Discloud entrypoint.
- Keep the proven globals live enforcer as the live delete owner.
- After an invite-related create/edit event, schedule a short delayed history rescan of the same channel so a transient live-delete failure can be recovered.
- On Discord `on_ready` and `on_resumed`, run a bounded reconciliation over every text channel where the bot has View Channel, Read Message History, and Manage Messages.
- Reuse `invite_policy_engine.scan_channel_invites()` for every historical decision/deletion.
- Scan at most 250 recent messages per channel on ready/resume and 75 on event-triggered recovery.
- Bound reconciliation concurrency to 2 channels and coalesce repeated ready/resume or per-channel sweeps.
- Skip automatic history reads when no currently configured invite-delete feature can approve deletion.

## Compatibility / safety invariants

- The central invite policy remains the only authority allowed to approve an invite deletion.
- Same-server invite behavior is unchanged.
- Exempt user/role/channel and explicitly allowed invite-code behavior is unchanged.
- Link Shield and Invite Shield policy semantics are unchanged.
- Durable invite statistics still record through the existing central delete helper.
- The live globals enforcer is not removed in this task because production already proves it works.
- No new Supabase tables or migrations are required for invite reconciliation.
- Ticket subsystem files and the PR #190-#193 fixes are untouched.

## Validation required

- regression proving the real `main.py` installs reconciliation before app startup;
- regression proving the recovery runtime calls the central scanner and never directly deletes messages;
- install-idempotence regression;
- permission-gated all-channel reconciliation regression;
- disabled-policy no-scan regression;
- event-triggered recent-history rescan regression;
- invite extraction/safety audit;
- invite policy/durable stats regressions;
- compileall;
- full pytest if executable in the available environment;
- diff check / changed-file scope;
- conflict-marker and accidental-secret inspection;
- final exact-head SHA check.

## Cleanup / conflicts

The dormant sweep-capable startup guard and the guaranteed live globals listener overlap historically, but this task does not activate a second delete listener. The new runtime is recovery-only and delegates all deletion to the central policy scanner. A broader startup-guard migration remains separate work unless it becomes necessary for correctness.

## Suspended / backlog

- **DS-TICKET-034 production acceptance:** PR #193 is merged. Historical Ticket Choices restore/cross-guild live acceptance remains pending and is suspended while DS-INVITE-035 is active.
- **Join-context Supabase schema mismatch:** production reports missing `entry_confidence` in `guild_members` and `member_joins`; investigate separately after this task.
- **Dank setup interaction/RSS spike:** production RSS rose sharply during setup interactions; investigate separately unless validation proves it shares this task's root cause.
- **Server Design setup regression/full audit:** preserve prior backlog item after ticket acceptance.

## Blockers / risks

Automatic reconciliation is deliberately bounded to recent history so reconnect recovery cannot hammer Discord across every channel. Very old invite messages outside the automatic window remain available to the existing manual `/dank cleanup invites all_text_channels:true` deep scan. Production acceptance must confirm the automatic window is sufficient for the observed missed-message case.

## Next step

Implement the native recovery runtime and boot wiring, run the focused invite regressions and repository validation, inspect the final diff, then open a focused PR. After deployment, require `invite_reconcile` ready/resume telemetry and verify a deliberately missed blocked invite is removed without staff running manual cleanup.
