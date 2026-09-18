# ACTIVE TASK

## DS-SEC-OWNER-AUDIT-CLAIM-RACE — Preserve owner authority evidence for the incident policy

**Status:** INVESTIGATION / IMPLEMENTATION

**Branch:** `fix/antinuke-owner-audit-claim-race`
**Base main:** `5d1ba5d3681bcba1c9ffbe7c20a0fcf4583436ff`

## Previous task closed

PR #258 (`DS-SEC-ROLE-UPDATE-SEVERITY`) merged as `5d1ba5d3681bcba1c9ffbe7c20a0fcf4583436ff`, became `main`, contains exact validated PR head `68ef8034b4e17b2ef6cb5882a45c2c4eafe87b67` as its second parent, and `discloud/commit` reported success.

The Single Active Task Lock is this audit-claim race only.

## Problem

The native event fallbacks for high-risk role authority changes can race the canonical `on_audit_log_entry_create` incident path.

Three native handlers currently:

- look up the recent audit entry
- atomically consume/mark that entry as seen
- only then inspect the actor
- return immediately when the actor is the physical guild owner

Those handlers are:

- dangerous role creation
- dangerous role permission escalation
- security-sensitive member role grants

PR #253 made the incident runtime the authoritative owner-severity classifier for exactly those actions. If a native Discord event callback wins the race, the owner audit entry can be marked seen before the incident listener receives it. The native handler then returns because the actor is the owner, and the later incident listener can no longer classify/report the owner event.

That makes owner-compromise detection depend on gateway event ordering, which is not a safe ownership boundary.

## Root cause

`anti_nuke._claim_recent_audit_entry()` always consumes a found entry before returning it. Specialized native role-security handlers need the actor from that entry in order to know whether they should defer to the owner incident path, but by the time they know, the evidence has already been consumed.

The audit listener path itself is correct: `anti_nuke_incident_runtime._process_owner_special_action()` consumes and routes owner role-create, dangerous role-update, and security-sensitive member-role-update evidence through the severity-aware owner policy.

## Intended behavior

- native high-risk role fallbacks remain atomic for normal non-owner actors
- the physical guild owner's matching role-authority entry remains unconsumed when a native fallback sees it first
- the native handler still returns without rollback/containment against the owner
- the incident audit listener remains the sole owner of owner severity classification/reporting
- bot behavior remains unchanged
- ordinary threshold-event claims, webhook claims, member-role removals, timeouts, channel events, and bot-add behavior remain unchanged

## Implementation scope

Add the smallest claim option needed for these three specialized native fallback handlers:

- allow `_claim_recent_audit_entry()` to preserve a matching physical-owner entry instead of marking it seen
- use that option only in dangerous role create, dangerous role permission escalation, and security-sensitive member-role grant handlers
- keep non-owner claims atomic and one-shot
- add race-focused regressions proving preserved owner entries remain available to the incident path while non-owner entries are still consumed exactly once

## Validation / merge gate

Before merge:

- owner-preserving audit-claim regressions pass
- existing atomic claim race tests remain green
- owner policy normalization and role-update severity regressions remain green
- gateway authority escalation and sparse recovery regressions remain green
- full compile/unit/standalone/security/event-boundary suite passes
- Claim-first ticket security and Managed category SQL smoke pass
- all companion workflows pass on exact final head
- branch is 0 behind current `main`
- final diff is task-limited with no unresolved review blocker
- merge only exact validated SHA
- verify resulting `main` merge parent and require `discloud/commit: success`

## Next step

Implement owner-preserving claim semantics for the three specialized native role-security fallbacks and add focused race regression coverage.
