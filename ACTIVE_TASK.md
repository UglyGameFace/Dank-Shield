# ACTIVE TASK

## DS-SEC-039 — Repair AntiNuke enforcement reliability

**Status:** IMPLEMENTED / FINAL EXACT-HEAD VALIDATION PENDING
**Branch:** `fix/antinuke-enforcement-reliability`
**Base:** `c0c99798caec2773894d6e014b6ba7e34275b3fa` (`main`, merge of PR #201)
**Started:** 2026-09-12

## User-visible failure

Dank Shield AntiNuke was enabled but failed to stop a destructive server attack reliably. The old implementation could allow several destructive actions before reacting, counted each action type separately, performed a forced remote config refresh for every event, depended on a narrow audit-log lookup window, and could claim containment readiness without proving the bot could actually remove dangerous roles because of Discord role hierarchy.

## Authoritative findings / root cause

- Destructive thresholds were late by default: channels/roles `3`, bans/kicks `5`, webhooks `3` in a `15s` window.
- Threshold windows were isolated by action type, so mixed destructive attacks could stay below every individual threshold.
- Every AntiNuke event called `get_guild_config(..., refresh=True)`, adding remote DB work to the attack hot path.
- Audit lookup searched only 10 entries with a 12-second freshness cap and silently dropped most lookup errors.
- Webhook events could repeatedly rediscover an already-consumed audit entry because lookup did not skip seen entries.
- Containment removes dangerous roles only when Discord hierarchy allows it, but enable/readiness checks verified permissions rather than hierarchy viability.
- Managed dangerous roles cannot be stripped by normal role removal and were not represented as readiness blockers.
- Trigger cooldown was recorded before containment success, so a failed containment attempt could suppress retries for 30 seconds.
- Existing regressions modeled ideal audit attribution and successful containment rather than burst/mixed/failure behavior.

## Implementation

- Kept one canonical native `stoney_verify/anti_nuke.py` runtime; no guard, monkey patch, or parallel enforcement tree was added.
- Lowered safe defaults to channels/roles `2`, bans/kicks `3`, webhooks `2` in the existing `15s` window.
- Added actor-wide mixed destructive-action counting in addition to per-action thresholds.
- Removed forced DB refreshes from the destructive-event hot path; saved settings still flow through the existing authoritative guild-config upsert/cache path.
- Widened audit correlation to 50 entries / 30 seconds, skips already-consumed entries, requires exact target matches where a target is available, and logs lookup failures instead of swallowing them.
- Unified webhook creation handling through the canonical destructive threshold handler rather than maintaining duplicate containment logic.
- Added containment-readiness checks for dangerous roles at/above Dank Shield, dangerous `@everyone`, and managed dangerous roles; explicit trusted-role exemptions remain supported.
- Split cooldown checking from cooldown recording. Successful containment starts the cooldown; failed or partial containment remains retryable on the next attributed destructive action.
- Preserved immediate rollback paths for dangerous role permission escalation and dangerous role grants.
- Added raid-shaped regressions for mixed attacks, failed-containment retry, hierarchy readiness, managed-role readiness, cached config reads, seen audit-entry skipping, and cooldown initialization/expiry.

## Validation completed before final head

On predecessor head `473cda3e17de467c1e28375528282fb6ad920aa4`:
- committed diff whitespace check passed;
- Python compile passed;
- Managed category SQL smoke test passed;
- Claim-first ticket security passed;
- Application Command Size Diagnostics passed;
- Dank Design Regression CI passed;
- Ticket Owner Emergency Override passed;
- Profile Runtime Diagnostics passed, including focused profile regression, role-menu compatibility, and competing-patch checks;
- the full Dank Shield unit/static lane was still running when the final readiness edge-case patch was made, so none of that predecessor CI is being treated as final validation.

## Definition of done / final validation still required

- Focused/new AntiNuke regressions pass on the final exact branch head.
- Python compile/static checks used by the repository pass on that same exact head.
- Full repository CI passes on that same exact head.
- Diff/scope check confirms only AntiNuke-related runtime/tests/task bookkeeping changed.
- Main/base drift is checked before marking the PR ready.
- No duplicate listeners, guards, temporary/debug bypasses, or conflicting enforcement paths are introduced.
- Remaining Discord platform limitation stays explicit: Dank Shield cannot contain the guild owner or a dangerous role Discord does not allow it to modify; containment mode must surface those blockers instead of pretending it is ready.
