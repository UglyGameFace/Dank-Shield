# ACTIVE TASK

## DS-SEC-039 — Repair AntiNuke enforcement reliability

**Status:** IMPLEMENTED / FINAL EXACT-HEAD VALIDATION PENDING / COMPETITIVE RED-TEAM BLOCKERS OPEN
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

- Kept one canonical native `stoney_verify/anti_nuke.py` runtime; no parallel AntiNuke policy engine was introduced.
- Lowered safe defaults to channels/roles `2`, bans/kicks `3`, webhooks `2` in the existing `15s` window.
- Unknown/untrusted destructive actors now use first-strike containment; configured trusted users/roles are delegated operators, not unlimited exemptions.
- Added actor-wide mixed destructive-action counting plus a 10-minute delegated long-horizon emergency ceiling that cannot be raised above 8 destructive actions even if short-window thresholds are configured higher.
- Extended the long-horizon set across channel/role create-update-delete activity, bans, kicks, pruning, role stripping, member timeouts, and webhook mutations.
- Removed forced DB refreshes from the destructive-event hot path; saved settings flow through the existing guild-config cache/upsert authority.
- Widened REST audit correlation to 50 entries / 30 seconds with retries, exact target matching where Discord exposes a target, audit-entry dedupe, and atomic claim locks.
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
- Added `stoney_verify/anti_nuke_gateway_runtime.py` as a transport-only fast path using Discord audit-log gateway entries before REST propagation where possible. It shares the canonical audit-entry dedupe registry and feeds the same policy/counter/containment engine rather than owning a second policy tree.
- Gateway fast path covers channel creation/deletion, role deletion, bans, kicks, prune, webhook create/update/delete, dangerous role creation, ordinary role-creation counting, and untrusted bot addition. REST/event correlation remains fallback for existing native listeners.
- Regression coverage proves gateway and REST cannot enforce the same audit entry twice and harmless-looking role creation is not consumed-and-dropped.

## Competitive red-team findings — merge blockers

A comparison against current Wick documentation, current Vetox published protection coverage, Discord's authoritative audit-log event surface, and capability classes advertised by a real public nuker implementation found that the branch is not yet defensibly superior overall.

1. **Channel overwrite attribution mismatch.** `on_guild_channel_update` detects overwrite changes but currently asks the audit log for `channel_update`. Discord/discord.py records explicit permission overwrite actions as `overwrite_create`, `overwrite_update`, and `overwrite_delete`. The current path can therefore miss or misattribute the exact permission mutation it claims to protect. Gateway fast-path coverage also does not yet claim those overwrite actions directly.
2. **No guild-wide panic/circuit breaker.** Current burst and long-horizon accounting is per actor. Several compromised delegated operators can distribute destructive actions across identities. Wick publicly documents a Panic Mode that locks down the server once a nuke is detected. Dank Shield needs an independent guild-wide destructive budget / emergency state so coordinated multi-actor activity cannot stay beneath every per-actor ceiling.
3. **Administrative coverage breadth trails documented competitors.** Vetox publicly documents protection for server rename/icon changes, invite deletion, emoji/sticker deletion, and scheduled-event cancellation in addition to the core actions. Wick documents vanity protection. Discord exposes authoritative audit actions for `guild_update`, invites, emojis, stickers, scheduled events, AutoMod rule mutation/deletion, permission overwrites, threads, integrations, and more. Dank Shield must classify the destructive subset intentionally instead of leaving them unmonitored.
4. **Real nuker parity gap: expressions.** A public nuker implementation advertises mass channel/role deletion, mass bans, webhook deletion, mass channel/role/category creation, and mass emoji deletion. Dank Shield now covers the other major classes, but emoji/expression deletion remains an uncovered first move.
5. **Recovery still loses to Wick.** Wick publicly documents Panic Mode plus structural imaging/backup restore. Dank Shield currently has strong prevention and configuration history, but not equivalent server-structure snapshot/restore. Prevention can be superior only if recovery is treated as a separate explicit product capability rather than implied.
6. **Role-position/hierarchy mutation requires explicit review.** The current role-update listener exits when dangerous permission bits did not change, so pure role-position mutation is not counted. Discord role hierarchy is itself the authority boundary for whether a bot can kick/strip a member. This needs an explicit threat-model decision and tests rather than accidental omission.
7. **Native security-control deletion needs coverage.** Discord exposes AutoMod rule create/update/delete audit actions. A compromised administrator can weaken Discord's own protective rules before attacking. Dank Shield should treat destructive AutoMod rule mutation/deletion as security-state tampering.

## Security model / hard platform limits

Dank Shield can aggressively contain any attributable non-owner actor that Discord allows the bot to act on. It cannot contain the guild owner, override Discord hierarchy/managed-role restrictions, manufacture missing audit-log attribution, defend the server if Dank Shield's own bot token is compromised, or act while Discord itself is unavailable/rate-limiting the required API. Readiness and incident output must surface those limits rather than pretending protection is healthy.

A previously loaded guild keeps stale cached config through a database outage. A totally cold process with no cache and no reachable authoritative database cannot safely invent the prior AntiNuke policy; durable cold-start security-state recovery is intentionally left for the persistence/DR remediation task.

## Validation already established on predecessor heads

Earlier exact/predecessor runs established compile, managed-category SQL smoke, claim-first ticket security, command-size diagnostics, Dank Design regressions, ticket owner emergency override, and profile runtime diagnostics. Those runs do **not** count as final validation after later AntiNuke hardening changed the branch head.

## Definition of done / final validation required

- Competitive red-team blockers above are resolved or explicitly separated into a later recovery product task where they are not prevention bypasses.
- Focused AntiNuke behavior, adversarial bypass, definitive containment, race/event-coverage, owner-control, audit-priority, readiness, extended-evasion, and gateway-runtime tests pass on one final exact head.
- Python compile/static checks and the repository's standalone audits pass on that same exact head.
- Full repository CI passes on that same exact head.
- Diff/scope review confirms every changed file is either native AntiNuke runtime, required owner/security bypass closure, API-safety support for AntiNuke attribution, tests, or task bookkeeping.
- Main/base drift is rechecked before readiness/merge.
- No duplicate AntiNuke policy engine, temporary bypass, stale compatibility path, or conflicting enforcement owner remains.
- PR description is updated to the final exact head and actual changed-file scope.
- Merge only after exact-head green; then verify post-merge `main` CI and deployment/production acceptance before closing DS-SEC-039.
