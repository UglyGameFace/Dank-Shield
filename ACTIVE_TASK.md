# ACTIVE TASK

## DS-RUNTIME-013 — Restore member reconciliation and durable Dank Stats

**Status:** IMPLEMENTED — DEPLOYED SMOKE PENDING
**Branch:** `fix/member-reconciliation-async-generator`
**PR:** #146

## Single Active Task Lock

Do not switch to unrelated work until PR #146 passes automated validation and deployed Discord smoke.

## Production failures

### Member reconciliation

Every guild fell back to cache-only membership evidence because authoritative enumeration raised:

```text
TypeError: 'async_generator' object is not iterable
```

That prevented false departures, but also prevented real departed-member reconciliation from running.

### Dank Stats

The live Discord stats display could become stale or misleading when:

- a claimed ticket remained active but Open Tickets showed `0`;
- one optional ticket compatibility column was unavailable;
- ticket history exceeded one PostgREST page;
- an external Discord channel deletion bypassed the lifecycle refresh hook;
- a transient SpamGuard settings read falsely displayed `OFFLINE`;
- a visible active ticket channel disagreed with a stale database snapshot;
- Discord rejected a channel rename without a useful diagnostic.

## Implemented corrections

### Authoritative members

- Consume `Guild.fetch_members(limit=None)` with a real async list comprehension.
- Convert the completed member list to the immutable snapshot tuple.
- Preserve cache-only positive evidence when Discord fetching genuinely fails.
- Preserve the rule that cache absence can never mark a member departed.
- Test successful authoritative enumeration and failed-fetch fallback behavior.
- Reject the broken `tuple(member async for ...)` form through regression coverage.

### Durable Dank Stats

- Keep Claimed Tickets as a subset of Open Tickets; Open can never be lower than Claimed.
- Read all ticket rows with pagination rather than trusting one PostgREST page.
- Fall back across `status,claimed_by,assigned_to`, `status,claimed_by`, `status,assigned_to`, and `status` when schemas differ.
- Support older/minimal PostgREST clients that do not expose `.range()`.
- Use visible active ticket channels as a floor against a false database Open Tickets zero.
- Refresh stats after externally deleted ticket channels, not only normal lifecycle buttons.
- Preserve the last known SpamGuard state through transient settings-read failures; use `UNKNOWN` when no truthful state exists.
- Log ticket-query, DB/live mismatch, and Discord channel-refresh failures instead of silently hiding them.
- Treat a successful no-change refresh as success rather than a failed refresh.
- Keep all displayed protection counters tied to durable, auditable actions; no invented totals.

## Automated gates

- [x] Native source committed.
- [x] Temporary materializers and write-enabled workflow changes removed.
- [x] Changed Python modules compile.
- [x] Focused member-reconciliation and Dank Stats regressions pass.
- [x] Full repository unit suite passes on the clean exact head.
- [x] Profile Runtime Diagnostics passes on the clean exact head.
- [x] Application Command Size Diagnostics passes on the clean exact head.
- [x] Public setup, command-surface, permission, role-truth, and event-boundary audits pass.
- [x] `git diff --check` passes.
- [x] Branch remains current with `main` and conflict-free.

## Deployed Discord smoke

### Member reconciliation

- [ ] No `TypeError: 'async_generator' object is not iterable` appears.
- [ ] Guilds report `membership_source=discord_fetch_members` and `membership_authoritative=True` when Discord enumeration succeeds.
- [ ] Reconciliation is not skipped for `authoritative_member_fetch_failed` during a healthy fetch.
- [ ] Full member sync completes without reconciliation errors or false departed members.

### Dank Stats

- [ ] A claimed active ticket displays Open Tickets at least equal to Claimed Tickets.
- [ ] Creating, claiming, unclaiming, closing, reopening, and deleting a ticket updates the display.
- [ ] Externally deleting a tracked ticket channel updates the display.
- [ ] A visible active ticket cannot coexist with a displayed Open Tickets zero.
- [ ] Ticket histories larger than one page remain fully counted.
- [ ] Transient SpamGuard read failure does not falsely flip ONLINE to OFFLINE.
- [ ] Real disabled SpamGuard still displays OFFLINE.
- [ ] Missing/renamed compatibility columns do not blank all ticket counters.
- [ ] Discord rename failures produce an actionable log line.
- [ ] No fake or estimated protection totals are displayed.

## Blocker

Deploy the final clean PR head to Discloud and complete both smoke sections before merging PR #146.

## Next task after PR #146

Enforce claim-first ticket handling. Staff may view an unclaimed ticket, but the only permitted staff interaction is Claim. Staff replies and every other bot control or command—including approve/deny, transcripts, notes, priority, transfer, reopen, close, and delete—require the recorded current claimant. Another staff member must complete a formal transfer or takeover first. Enforce staff-message gating with channel permissions where possible and remove unauthorized staff messages with a clear explanation when necessary. The requester may continue providing information and may cancel an unclaimed ticket, but may never delete ticket history. Server owners and administrators receive no silent Dank Shield bypass; unauthorized native Discord actions must be logged as policy violations where observable.
