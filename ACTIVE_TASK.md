# ACTIVE TASK

## DS-TICKETS-014 — Enforce claim-first ticket handling

**Status:** IMPLEMENTED — EXACT-HEAD CI AND DEPLOYED SMOKE PENDING
**Branch:** `fix/ticket-claim-first-enforcement`
**PR:** #147

## Single Active Task Lock

Do not switch to unrelated work until PR #147 passes exact-head validation and deployed Discord smoke.

## Security rule

- Staff may view an unclaimed ticket, but **Claim is the only permitted staff interaction**.
- Staff cannot reply, edit a reply, approve/deny verification, send macros, create or view notes, change priority, view ticket controls, generate transcripts, transfer, unclaim, reopen, close, or delete until the ticket is claimed.
- After claim, only the recorded current claimant can reply or use ticket actions.
- Another staff member must receive a formal transfer before interacting.
- Server owners and administrators receive no silent Dank Shield bypass.
- The requester may continue providing information.
- The requester may cancel their own unclaimed ticket, but cannot close it after claim and can never delete ticket history.
- Delete is a separate second-stage action available only after the current claimant closes the ticket.

## Implemented enforcement

### Central policy

- Added one authoritative claim-first decision engine.
- Human actions require a real Discord actor and registered ticket row.
- Claim is allowed only for open tickets and cannot be taken by the requester.
- Unclaimed staff actions return `claim_required`.
- Actions by anyone other than the current claimant return `claimant_required`.
- Explicit internal system operations remain possible only through `system_action=True`.
- Removed the obsolete elevated-owner/administrator lifecycle bypass helper.

### Ticket service

- Close, delete, transcript attachment, unclaim, transfer, priority, notes, and reopen are claim-gated in the service layer.
- Service-layer checks run before repository writes, preventing UI or command bypasses.
- Staff-role channel overwrites are read-only until a claim is recorded.
- The current claimant receives the explicit member overwrite needed to reply and manage ticket messages.
- Unclaim and transfer remove the previous claimant overwrite and apply the new truth.
- Closed tickets remove claimant write access.

### Panels and commands

- Every staff panel action except Claim is checked centrally.
- Every canonical `/ticket` subcommand except `claim` is checked before its callback runs.
- The group-wide guard covers read actions and direct permission actions, including info, owner, access, add, remove, rename, lock, and unlock.
- Requester cancellation is allowed only while the ticket remains unclaimed.
- Macros require the current claimant.
- Verification approve/deny controls require the current claimant.
- The old one-click open-ticket close-and-delete path is removed.

### Runtime enforcement

- Unauthorized staff messages and edited messages are removed with a clear claim/transfer explanation.
- Unauthorized direct lifecycle renames are reverted when Dank Shield can observe and edit the channel.
- Discord-native channel deletion is recorded as a critical policy violation; Discord does not permit a bot to overrule the server owner after deletion occurs.

## Automated gates

- [x] Native source committed.
- [x] Temporary materializers removed.
- [x] Write-enabled workflow logic removed; permanent workflow is read-only.
- [x] Changed ticket and command modules compile.
- [x] Every existing `tests/test_ticket*.py` regression passes on the generated native source.
- [x] Canonical `/ticket` group guard passes the complete ticket regression suite.
- [x] Application Command Size Diagnostics passed during implementation validation.
- [ ] Exact clean-head Dank Shield CI passes.
- [ ] Exact clean-head Application Command Size Diagnostics passes.
- [ ] Exact clean-head Profile Runtime Diagnostics passes.
- [ ] Public setup, command-surface, permission, role-truth, and event-boundary audits pass.
- [ ] `git diff --check` passes on the clean exact head.
- [ ] Branch remains current with `main` and conflict-free.

## Deployed Discord smoke

- [ ] An unclaimed staff member can see the ticket but cannot send a message.
- [ ] An unclaimed staff member cannot use info, owner, access, add, remove, rename, lock, unlock, or any other `/ticket` action except Claim.
- [ ] Claim succeeds and grants the claimant reply/control access.
- [ ] The claimant can use notes, macros, priority, transcript, verification review, close, and other normal controls.
- [ ] A different staff member cannot reply or use controls until transfer.
- [ ] Server owner/administrator receives no Dank Shield action bypass.
- [ ] Transfer removes the old claimant access and grants the new claimant access.
- [ ] Unclaim returns the ticket to staff read-only state.
- [ ] The requester can continue typing before and after claim.
- [ ] The requester can cancel an unclaimed ticket.
- [ ] The requester cannot cancel after claim or delete ticket history.
- [ ] Delete on an open ticket is refused with instructions to close first.
- [ ] The current claimant can delete only after closure.
- [ ] Unauthorized staff messages are removed with a clear explanation.
- [ ] Unauthorized lifecycle rename attempts are reverted and logged.
- [ ] Direct external deletion is logged as a critical policy violation.

## Blocker

Run the exact clean head through repository CI, deploy it to Discloud, complete the smoke gates, then merge PR #147.

## Backlog

None. Finish PR #147 before beginning another task.
