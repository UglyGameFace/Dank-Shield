# TicketTool Parity Standard

This document is the hard execution standard for Stoney Bot until it is honestly better than Ticket Tool in production.

## Core rule

Do **not** call Stoney Bot better than Ticket Tool until the checklist in this document is actually true in the live system.

Feature count is not enough.
Consistency, correctness, and staff trust are the deciding factors.

---

## Non-negotiable product goal

Stoney Bot must become:

1. easier for staff to use than Ticket Tool
2. more reliable in ticket lifecycle handling than Ticket Tool
3. more transparent in history, ownership, and audit trail than Ticket Tool
4. at least as safe as Ticket Tool for destructive actions

---

## Permanent execution rules

### 1. One lifecycle truth
Every ticket-facing surface must agree on the same effective state:

- open
- claimed
- closed
- archived
- deleted

No module is allowed to invent its own meaning for those states.

### 2. UI correctness over speed
No panel, button, or command should remain live if it can cause the wrong action for the ticket’s current state.

### 3. No hidden regressions
A fix that improves one flow but breaks another flow is not a valid improvement.

### 4. Staff trust beats cleverness
If a workflow is technically powerful but confusing, it fails the standard.

### 5. Archive state must be first-class
A ticket moved to archive must be treated consistently everywhere.
Closed/archive mismatches are failures.

### 6. Stale controls are bugs
A stale panel, expired button path, or duplicated control surface counts as a real bug.

### 7. VC verify is part of the product
Stoney Bot cannot be considered better overall if ticketing is strong but VC verify is flaky.

### 8. Timer safety is mandatory
Any timer that can kick, close, delete, or alter a member flow must be treated as trust-critical.

### 9. Destructive actions need hard safety
Delete, deny, close, archive, and forced VC actions must always prefer correctness over convenience.

### 10. No “better than Ticket Tool” claim before proof
Do not claim superiority until the checklist below is fully satisfied and tested.

---

## Hard go / no-go checklist

### P0 — required before claiming superiority

#### A. Unify ticket lifecycle checks everywhere
All of these decisions must come from shared lifecycle helpers, not duplicated logic:

- can show open controls
- can show closed controls
- can show reopen controls
- can delete
- can archive
- can reopen
- can post transcript
- can post actions panel
- can claim / unclaim / transfer

#### B. Reduce UI ownership overlap
There must be a clear owner for each ticket UI surface:

- open ticket controls
- close confirmation
- closed ticket controls
- staff channel actions
- staff verification review controls

The system should not split ownership across multiple competing modules unless one is explicitly a wrapper.

#### C. Eliminate stale-control paths
The system must reject or disable:

- old close prompts
- old decision panels
- old VC panels
- outdated action panels
- duplicate controls left behind after reopen / close / reissue

#### D. Full active command-surface audit
Every live command module registered by the loader must be reviewed against this standard.

#### E. Scenario test matrix completed
At minimum, verify these flows:

- create
- claim
- unclaim
- transfer
- note
- priority change
- close
- reopen
- transcript
- delete
- archive move
- restart bot
- stale button press
- duplicate button press
- command path vs button path
- owner close flow
- staff close flow

#### F. VC verify matrix completed
At minimum, verify these flows:

- request
- accept
- start
- approve
- deny
- reissue
- upload instead
- takeover
- unlock / reset
- stale panel press
- duplicate request prevention
- deleted ticket during active VC flow

#### G. Timer matrix completed
At minimum, verify these flows:

- join grace start
- join grace cancel
- no-ticket timer start
- no-ticket timer cancel
- ticket no-response timer start
- ticket no-response timer cancel
- persistence across restart
- timer expiry with permission failure
- timer expiry with transcript path
- timer expiry when user is already verified

---

## What counts as a pass

Stoney Bot can be called better than Ticket Tool only when all of the following are true:

- lifecycle state is consistent across service, repository, panels, transcripts, commands, and interactions
- no stale controls can perform the wrong action
- close / reopen / archive / delete behavior is deterministic
- VC verify works reliably enough to trust on live servers
- timer behavior is safe and predictable
- staff can operate the system without guessing what state the ticket is actually in
- audit trail and dashboard history remain correct after all major actions

---

## What counts as a fail

Any of the following blocks the claim:

- archived ticket treated as open anywhere important
- closed ticket still showing open-only actions
- deleted ticket being reopened or mutated incorrectly
- duplicate or stale panel still taking action
- VC request state getting out of sync with ticket state
- timer firing on the wrong member or wrong state
- two modules disagreeing on whether a ticket is open / closed / archived
- staff confusion caused by conflicting control surfaces

---

## Working priority order

1. unify lifecycle helpers everywhere
2. reduce ticket UI ownership overlap
3. harden stale-control rejection / cleanup
4. finish full command module audit
5. finish VC verify hardening
6. finish timer hardening
7. polish staff UX and accessibility
8. remove legacy compatibility only when safe

---

## Enforcement note

This document is the standing standard for future ticket / verification work in this repository.
Any refactor or feature that conflicts with it should be treated as a regression until fixed.
