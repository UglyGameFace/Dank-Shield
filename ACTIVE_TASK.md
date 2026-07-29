# ACTIVE TASK

## DS-MEMBER-SYNC-013 — Restore authoritative member reconciliation

**Status:** IMPLEMENTED — CI AND DEPLOYED SMOKE PENDING
**Branch:** `fix/member-reconciliation-async-generator`
**PR:** #146

## Single Active Task Lock

Do not switch to unrelated work until PR #146 passes automated validation and deployed startup smoke.

## Production failure

Every guild currently falls back to cache-only membership evidence because authoritative enumeration raises:

```text
TypeError: 'async_generator' object is not iterable
```

That safely prevents false departure marking, but it also means actual departed-member reconciliation never runs.

## Verified root cause

`collect_membership_snapshot()` passed an async generator expression directly to synchronous `tuple()`:

```python
tuple(member async for member in guild.fetch_members(limit=None))
```

The async generator must be consumed asynchronously before conversion to a tuple.

## Implemented correction

- Consume `Guild.fetch_members(limit=None)` with a real async list comprehension.
- Convert the completed member list to the immutable snapshot tuple.
- Preserve cache-only positive evidence when the Discord fetch genuinely fails.
- Preserve the rule that cache absence can never mark a member departed.
- Test the successful authoritative async-iterator path.
- Test the failed-fetch cache fallback path and captured diagnostic.
- Reject the broken `tuple(member async for ...)` form through regression coverage.

## Automated gates

- [ ] Python compilation passes.
- [ ] Focused member-reconciliation tests pass.
- [ ] Full repository unit suite passes.
- [ ] Public setup, command-surface, permission, role-truth, and event-boundary audits pass.
- [ ] `git diff --check` passes.
- [ ] Branch remains current with `main` and conflict-free.

## Deployed startup smoke

- [ ] No `TypeError: 'async_generator' object is not iterable` appears.
- [ ] Each guild reports `membership_source=discord_fetch_members`.
- [ ] Each guild reports `membership_authoritative=True`.
- [ ] Departure reconciliation is not skipped for `authoritative_member_fetch_failed`.
- [ ] Full member sync completes without reconciliation errors.
- [ ] No false departed members are created.

## Backlog

None. Finish PR #146 before beginning another task.
