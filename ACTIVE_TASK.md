# ACTIVE TASK

## DS-SEC-039 — Repair AntiNuke enforcement reliability

**Status:** IN PROGRESS
**Branch:** `fix/antinuke-enforcement-reliability`
**Base:** `c0c99798caec2773894d6e014b6ba7e34275b3fa` (`main`, merge of PR #201)
**Started:** 2026-09-12

## User-visible failure

Dank Shield AntiNuke was enabled but failed to stop a destructive server attack reliably. The current implementation can allow several destructive actions before reacting, counts each action type separately, performs a forced remote config refresh for every event, depends on a narrow audit-log lookup window, and can claim containment readiness without proving the bot can actually remove dangerous roles because of Discord role hierarchy.

## Authoritative findings / root cause

- Destructive thresholds are late by default: channels/roles `3`, bans/kicks `5`, webhooks `3` in a `15s` window.
- Threshold windows are isolated by action type, so mixed destructive attacks can stay below every individual threshold.
- Every AntiNuke event calls `get_guild_config(..., refresh=True)`, adding remote DB work to the attack hot path.
- Audit lookup searches only 10 entries with a 12-second freshness cap and silently drops most lookup errors.
- Webhook events can repeatedly rediscover an already-consumed audit entry because lookup does not skip seen entries.
- Containment removes dangerous roles only when hierarchy allows it, but enable/readiness checks currently verify only permissions, not hierarchy.
- Trigger cooldown is recorded before containment success, so a failed containment attempt can suppress retries for 30 seconds.
- Existing regressions model ideal audit attribution and successful containment rather than burst/mixed/failure behavior.

## Repair scope

- Keep one canonical native `stoney_verify/anti_nuke.py` runtime; do not add guards, monkey patches, or parallel enforcement trees.
- Remove forced DB refreshes from the destructive-event hot path while preserving immediate saved-setting visibility through the existing guild-config cache/update path.
- Add actor-wide mixed destructive-action counting in addition to per-action thresholds.
- Tighten safe defaults to reduce the damage budget before containment.
- Make audit attribution burst-tolerant by widening lookup depth/freshness and skipping already-consumed entries while keeping high-confidence target matching.
- Surface audit lookup failures instead of silently swallowing them.
- Make readiness include Discord role-hierarchy containment viability and trusted-role exemptions.
- Do not start containment cooldown until containment actually succeeds; failed containment must remain retryable.
- Preserve immediate rollback for dangerous role permission escalation and dangerous role grants.
- Add regression coverage for mixed attacks, failed-containment retry, hierarchy readiness, cached config reads, and seen audit-entry skipping.

## Definition of done / validation

- Root cause and execution path documented above.
- Focused AntiNuke regressions pass on the exact branch head.
- Existing AntiNuke behavior compatibility reviewed, including trusted users/roles and owner exemption.
- Python compile/static checks used by the repository pass.
- Full repository CI passes on one exact head.
- Diff/scope check confirms only AntiNuke-related runtime/tests/task bookkeeping changed.
- No duplicate listeners, guards, temporary/debug bypasses, or conflicting enforcement paths are introduced.
- Remaining Discord platform limitation is explicit: Dank Shield cannot contain the guild owner or a role at/above its own top role; enabling containment must no longer pretend otherwise.
