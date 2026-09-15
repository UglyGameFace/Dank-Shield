# ACTIVE TASK

## DS-FIX-AUTHORIZED-BOT-BAN — Stop Dank Shield from punishing legitimate server bots

**Outcome:** Dank Shield must not ban, kick, quarantine, or otherwise punish legitimate authorized bots through generic anti-spam / anti-invite / anti-nuke / join-protection paths unless the server owner explicitly configured a policy that is intended to cover those bots. The reported Discadia bump-bot ban is the triggering incident, but the fix must address the authoritative root cause rather than hardcode a bot name.

**Status:** IN PROGRESS — investigation started; no runtime code changed yet

**Branch:** `fix/authorized-bot-ban-false-positive`
**Base main:** `d3853509b90a57769cd2c5a5a3d565cad28c9d87`

## Scope

- trace every production path capable of banning/kicking/quarantining a bot account
- identify the exact listener / policy / persistence/config decision that can classify an authorized bot as hostile
- inspect trusted/allowlist/bot-specific policy ownership and duplicate enforcement paths
- verify interaction with invite blocking, spam protection, anti-nuke, member-join/removal safety, and any bot-add protections that actually feed the same action path
- implement the smallest authoritative correction
- add focused regression coverage for legitimate bots and malicious/untrusted bot scenarios

No unrelated moderation redesign, setup redesign, ticket work, permission-repair work, or general cleanup belongs in this task.

## Current incident

The server owner reports that Dank Shield banned the Discadia bump bot. The repository does not yet contain enough evidence to state which subsystem issued the ban. The investigation must establish the actual execution path before any runtime edit.

## Investigation checklist

- locate all `ban`, `kick`, `quarantine`, timeout, and destructive enforcement calls reachable for `member.bot == True`
- trace their callers, listeners, config gates, allowlists/trusted identities, persistence, and audit reasons
- determine whether authorized bots are exempted consistently or only in some paths
- search for duplicate/legacy listeners or guards that can independently punish bot accounts
- identify whether bump content/invites, bot joins, role updates, or anti-nuke attribution can trigger the incident class
- inspect existing tests for bot-account enforcement and trusted-bot behavior

## Validation / results

Not run yet.

## Cleanup / conflicts

Not complete yet.

## Blockers / risks

- GitHub cannot provide the live Discord audit-log event that banned Discadia unless that event was persisted/logged in repository-accessible data. If the exact incident record is unavailable, the code path will still be traced exhaustively and the fix will be based on reproducible enforcement behavior rather than guessing.

## Backlog

- external Discloud runtime verification for the previously merged permission-repair task remains separate
- unrelated startup-guard and operation-queue cleanup remains separate

## Next step

Trace the authoritative destructive-enforcement execution paths for bot accounts on canonical main, identify the root cause, then implement only the correction required for legitimate authorized bots to remain safe without weakening hostile-bot protection.
