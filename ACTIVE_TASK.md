# ACTIVE TASK

## DS-SEC-039 — Repair AntiNuke enforcement reliability

**Status:** IMPLEMENTED / FINAL EXACT-HEAD VALIDATION PENDING
**Branch:** `fix/antinuke-enforcement-reliability`
**Base:** `c0c99798caec2773894d6e014b6ba7e34275b3fa` (`main`, merge of PR #201)
**Started:** 2026-09-12

## User-visible failure

Dank Shield AntiNuke was enabled but failed to stop a destructive server attack reliably. The original implementation could allow several destructive actions before reacting, counted action types separately, performed remote config work on the attack path, depended on narrow audit correlation, and could report containment readiness without proving that Discord would let Dank Shield neutralize the attacker.

## Authoritative root causes

- Destructive defaults reacted too late and mixed attacks could stay below per-action thresholds.
- Audit lookup was too narrow and could be delayed by the generic audit-log safety spacing.
- Concurrent targetless audit claims could race and reuse the same audit entry.
- Containment readiness checked permissions more than effective hierarchy/managed-role authority.
- Channel/category overwrites and harmless-looking high roles could preserve destructive authority even after visible dangerous roles were stripped.
- Failed containment started cooldown too early in the old path.
- Trusted users/roles behaved too much like blanket exemptions instead of delegated operators with an emergency ceiling.
- A compromised delegated operator could evade short-window thresholds with low-and-slow bans, kicks, structural mutations, role stripping, or timeouts.
- AntiNuke mutations and configuration restore paths could otherwise give delegated admins a route to weaken owner-defined security state.
- Event coverage did not fully represent webhook updates/deletes, pruning, bot additions, dangerous role creation, channel creation/overwrite mutation, role removal, timeout abuse, or destructive permission reductions.

## Implementation

- Kept one canonical native `stoney_verify/anti_nuke.py` runtime; no parallel AntiNuke engine or duplicate listener tree was introduced.
- Lowered safe defaults to channels/roles `2`, bans/kicks `3`, webhooks `2` in the existing `15s` window.
- Unknown/untrusted destructive actors now use first-strike containment; configured trusted users/roles are delegated operators, not unlimited exemptions.
- Added actor-wide mixed destructive-action counting plus a 10-minute delegated long-horizon emergency ceiling that cannot be raised above 8 destructive actions even if short-window thresholds are configured higher.
- Extended the long-horizon set across channel/role create-update-delete activity, bans, kicks, pruning, role stripping, member timeouts, and webhook mutations.
- Removed forced DB refreshes from the destructive-event hot path; saved settings flow through the existing guild-config cache/upsert authority.
- Widened audit correlation to 50 entries / 30 seconds with retries, exact target matching where Discord exposes a target, audit-entry dedupe, and atomic claim locks.
- Security-priority AntiNuke audit reads bypass only the guard's artificial six-second generic spacing while still sharing the guild audit lock and respecting real Discord 429 backoff.
- Added per-actor containment locks.
- Containment is definitive: kick the proven malicious actor first; if Discord blocks removal, strip every manageable non-default role as fallback and keep the incident retryable.
- Contain-mode readiness now requires View Audit Log, Manage Roles, and Kick Members and reports dangerous `@everyone`, managed roles, effective member hierarchy, and dangerous channel/category overwrite blockers.
- `moderate_members` is treated as a dangerous containment-risk permission.
- Preserved immediate rollback/containment for dangerous role permission escalation, dangerous/trusted-exemption role grants, dangerous role creation, and untrusted bot addition.
- Added detection for member role stripping, member timeout application/extension, channel creation, channel overwrite mutation, non-dangerous role creation flooding, and destructive role-permission reduction/mutation through the same canonical threshold engine.
- Webhook create/update/delete share the canonical destructive engine; member prune falls back from kick attribution and triggers immediately.
- AntiNuke configuration mutations are server-owner-only.
- Final configuration restore confirmation is server-owner-only because restores can change AntiNuke/security state.

## Security model / hard platform limits

Dank Shield can aggressively contain any attributable non-owner actor that Discord allows the bot to act on. It cannot contain the guild owner, override Discord hierarchy/managed-role restrictions, manufacture missing audit-log attribution, defend the server if Dank Shield's own bot token is compromised, or act while Discord itself is unavailable/rate-limiting the required API. Readiness and incident output must surface those limits rather than pretending protection is healthy.

## Validation already established on predecessor heads

Earlier exact/predecessor runs established compile, managed-category SQL smoke, claim-first ticket security, command-size diagnostics, Dank Design regressions, ticket owner emergency override, and profile runtime diagnostics. Those runs do **not** count as final validation after later AntiNuke hardening changed the branch head.

## Definition of done / final validation required

- Focused AntiNuke behavior, adversarial bypass, definitive containment, race/event-coverage, owner-control, audit-priority, readiness, and extended-evasion tests pass on one final exact head.
- Python compile/static checks and the repository's standalone audits pass on that same exact head.
- Full repository CI passes on that same exact head.
- Diff/scope review confirms every changed file is either native AntiNuke runtime, required owner/security bypass closure, API-safety support for AntiNuke attribution, tests, or task bookkeeping.
- Main/base drift is rechecked before readiness/merge.
- No duplicate AntiNuke listeners, parallel guards, temporary bypasses, stale compatibility path, or conflicting enforcement owner remains.
- PR description is updated to the final exact head and actual changed-file scope.
- Merge only after exact-head green; then verify post-merge `main` CI and deployment/production acceptance before closing DS-SEC-039.
