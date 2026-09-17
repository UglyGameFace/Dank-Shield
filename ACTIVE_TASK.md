# ACTIVE TASK

## DS-MEMBER-JOIN-LOG-REGRESSION — Restore join events to the join/leave log

**Status:** IMPLEMENTATION / VALIDATION

**Branch:** `fix/member-join-leave-log-regression`
**Base main:** `98290563f7244af198c4384a595cb66cdc76e0e3`

## Previous task lock closed

`DS-AUD-CONTEXTUAL-REPAIR-LIVE-REGRESSION` is no longer active:

- PR #250 final head: `e28a6a65bb678d4b0f147c0c4cea0e9e987d8a62`
- all five triggered exact-head workflows completed successfully
- PR #250 merged as `98290563f7244af198c4384a595cb66cdc76e0e3`
- that merge commit is the current `main`
- Discloud commit status on the merge commit is successful

This file was stale after the merge; the repository lock is now transferred to the production join-log regression below.

## User-reported production regression

New members are joining the guild, including members arriving through a recently added bot that presents Discord invites as hyperlinks, but Dank Shield is not posting those joins in the configured join/leave channel.

## Root cause

The August 6 canonical Welcome Card change (`ae556fa34d4f9e2dd8cb7562d81472c592baa22e`) intentionally removed the operational join sender from `JOIN_LEAVE_KEYS` and made `Welcome Card Studio` the only public join sender.

That created a routing regression:

1. the configured join/leave channel stopped receiving member-joined events;
2. a disabled, unavailable, or differently routed Welcome Card Studio could therefore make a perfectly valid Discord join appear completely unlogged in the join/leave channel;
3. invite-source attribution is independent of the member-join gateway event, so an unresolved redirect/hyperlink/OAuth-style source must never suppress the basic join event log.

Discord member events are enabled in code (`intents.members = True`). The defect is the retired join/leave route, not the existence of a hyperlink around an invite URL.

## Execution path

- Discord dispatches `on_member_join`.
- `member_lifecycle_router_guard._join_listener()` is the authoritative public lifecycle listener.
- `send_live_welcome_card()` handles the optional member-facing Welcome Card Studio output.
- `JOIN_LEAVE_KEYS` contains the configured operational lifecycle log channel aliases.
- Before this task, `_join_listener()` never resolved or sent to `JOIN_LEAVE_KEYS`.
- Invite attribution is separately collected by `members_new.join_context_service` and staff/modlog paths.

## Implementation

- keep Welcome Card Studio as the canonical member-facing welcome card
- restore an independent operational member-joined event to the configured join/leave route
- make join logging independent of Welcome Card Studio enabled/disabled/failure state
- make join logging independent of invite attribution success
- suppress only a true duplicate when Welcome Card Studio already successfully posted to the exact same channel
- preserve separate staff invite-source/audit ownership
- expose the operational join/leave route clearly in `/dank member-logs` status/help text
- retain legacy duplicate listener retirement

## Regression coverage

`tests/test_modlog_join_dedupe_behavior.py` now covers:

- the canonical Welcome Card runtime still receives member joins
- a disabled Welcome Card Studio does not suppress the operational join log
- the operational join log resolves through `JOIN_LEAVE_KEYS`
- same-channel duplicate suppression occurs only when Welcome Card Studio actually delivered there
- existing modlog semantic dedupe remains intact

Existing lifecycle centralization/static tests continue to guard against re-registering the retired legacy lifecycle module and old v3/v4 marker senders.

## Validation gate

- targeted member lifecycle regression tests pass
- join/leave centralization standalone audit passes
- Python compile/static checks pass
- full applicable test suite passes
- every triggered PR workflow succeeds on the exact final head
- final branch is compared against current `main` and contains no unrelated changes
- final diff has no debug code, conflict artifacts, secrets, generated junk, or duplicate lifecycle senders
- exact validated head is merged
- resulting merge commit is verified as current `main`

## Backlog

No unrelated redesign is included in this task. If a specific third-party invite bot uses a non-invite OAuth flow that Discord cannot attribute to an invite code, richer source attribution can be evaluated separately after this join-log regression closes. The member-joined log itself must still work regardless.

## Next step

Validate the branch, inspect CI and the final diff, fix only failures tied to this regression, then merge the exact validated head and verify the resulting `main` commit before releasing the task lock.
