# ACTIVE TASK

## DS-SEC-039 — Repair AntiNuke enforcement reliability

**Status:** IMPLEMENTED / FINAL EXACT-HEAD VALIDATION PENDING
**Branch:** `fix/antinuke-enforcement-reliability`
**Base:** `c0c99798caec2773894d6e014b6ba7e34275b3fa` (`main`, merge of PR #201)
**Implementation candidate before this bookkeeping commit:** `ee3d99d979e84161e63a39b167ee7c3379a5089a`
**Started:** 2026-09-12

## Objective

Make Dank Shield AntiNuke difficult to evade through mixed destructive actions, low-and-slow behavior, compromised delegated operators, audit-log latency/races, Discord hierarchy, channel overwrites, sparse gateway attribution, or coordinated multi-actor attacks. Prevention and containment are the scope of this task. Structural backup/restore is explicitly out of scope for the product constraint on this task and must not be implied by the AntiNuke claim.

## Original production failure

AntiNuke could allow several destructive actions before reacting, counted attack types too independently, forced remote config work on the hot path, relied on narrow audit correlation, did not prove containment authority, treated trusted operators too much like exemptions, and lacked broad coverage for several current Discord administrative attack surfaces.

## Canonical ownership

- `stoney_verify/anti_nuke.py` is the single policy, counter, trust, containment, readiness, and incident authority.
- `stoney_verify/anti_nuke_gateway_runtime.py` is the production audit-gateway compatibility/fast-path entrypoint.
- `stoney_verify/anti_nuke_guardian_runtime.py` is transport/classification support for the broader audit surface and guild-wide panic accounting; it does not own a second punishment policy.
- Native Discord-event + REST correlation remains fallback where appropriate.
- Gateway and REST paths share canonical audit-entry dedupe/claim state so one Discord audit entry cannot be enforced twice.

## Implemented hardening

- Safe destructive defaults: channels/roles `2`, bans/kicks `3`, webhooks `2`, existing `15s` short window.
- Unknown/untrusted destructive actors use first-strike containment on guarded AntiNuke actions.
- Configured trusted users/roles are delegated operators, not blanket immunity. They retain saved short-window limits plus a 10-minute hard emergency ceiling capped at 8 destructive actions.
- Mixed destructive actions share an actor-wide burst budget.
- Long-horizon counting covers channel/role create-update-delete activity, bans, kicks, pruning, role stripping, member timeouts, and webhook mutation.
- Config reads use the existing cache on the attack path instead of forced remote refreshes.
- REST audit correlation searches 50 recent entries / 30 seconds with retries, exact target matching where available, freshness checks, dedupe, and atomic claim locks.
- Security-priority audit reads bypass only Dank Shield's artificial generic audit spacing; they still share the guild audit lock and honor Discord 429 backoff.
- Sparse gateway audit entries resolve the executor from `user_id`/member state before consumption; unresolved evidence is left available for REST reconciliation instead of being consumed-and-dropped.
- Per-actor containment locks prevent concurrent duplicate containment.
- Definitive containment kicks the proven malicious actor first. If Discord blocks removal, every manageable non-default role is stripped as fallback, and partial/failed containment remains retryable.
- Contain-mode readiness requires View Audit Log, Manage Roles, and Kick Members and reports dangerous `@everyone`, managed roles, effective member hierarchy, and dangerous channel/category overwrite blockers.
- `moderate_members` is treated as a dangerous containment-risk permission.
- AntiNuke configuration mutation is server-owner-only.
- Configuration-history restore confirmation is server-owner-only because a restore can alter AntiNuke/security state.

## Gateway-fast authority protection

- Dangerous role permission escalation uses gateway audit evidence when available, attempts immediate permission rollback, then contains the executor.
- Dangerous or AntiNuke-trusted-exemption role grants use gateway evidence, attempt immediate target-role rollback, then contain the executor.
- Member role-removal abuse and timeout extension use claimed gateway evidence directly when available.
- Dangerous role creation keeps immediate rollback/containment.
- Untrusted bot additions remove the new bot and contain the inviter on the gateway fast path.
- discord.py `$add` / `$remove` member-role audit normalization is covered by regression tests.

## Broad prevention surface

The guardian intentionally classifies destructive/security-sensitive Discord audit actions including:

- server settings / identity mutation (`guild_update`);
- channel create/update/delete;
- explicit channel overwrite create/update/delete;
- role create/update/delete, including hierarchy/position mutation;
- bans, unbans, kicks, and pruning;
- member role stripping and timeout abuse through the authority fast path;
- bot additions;
- webhook create/update/delete;
- invite deletion;
- emoji and sticker deletion;
- integration deletion;
- scheduled-event cancellation;
- thread deletion;
- application-command permission mutation;
- soundboard deletion;
- Discord AutoMod rule create/update/delete.

Benign member-facing creation/update activity such as ordinary invite creation, emoji creation, sticker creation, or scheduled-event creation is intentionally not made first-strike AntiNuke evidence merely to inflate a feature count.

## Channel-overwrite ownership fix

Discord records permission overwrite changes as explicit overwrite audit actions, not generic channel updates. Production installation now retires the old native overwrite listener that queried the wrong audit bucket and installs one target-correct owner using `overwrite_create`, `overwrite_update`, and `overwrite_delete`, with channel-ID matching and security-priority REST fallback.

## Coordinated multi-actor panic

- Guild-wide panic uses a 10-second weighted destructive-action window across executors.
- Structural and authority-changing events carry higher weights than routine moderation.
- Ban/kick/unban activity uses a much higher distributed flood threshold so normal multi-moderator raid response does not itself trigger panic.
- Role-position or dangerous-permission mutation is weighted more heavily than cosmetic role edits.
- A severe fast path closes the score-six evasion case: two different actors performing two high-confidence structural/security destructions in the guild window trigger panic immediately even if the broader weighted score has not yet reached its threshold.
- Severe actions include channel deletion, overwrite creation/update/deletion, role deletion, bot addition, member prune, webhook deletion, integration deletion, application-command permission mutation, and AutoMod rule deletion.
- Ordinary channel/role creation and routine bans/kicks are deliberately excluded from the severe two-actor fast path.
- Once panic is active, delegated allowances are suspended for guarded activity and observed peer executors are contained through the canonical containment primitive.
- This design intentionally does not imitate Wick's reversible global role stripping because this product does not currently provide the backup/restore state required to make that destructive lockdown safely reversible.

## Competitive red-team result

The prevention/containment comparison was refreshed against current Wick documentation, current Vetox published protection coverage, Discord's authoritative audit-log surface, and publicly advertised nuke capability classes.

### Resolved gaps

1. Channel overwrite attribution mismatch — **resolved** with explicit overwrite audit actions and one production owner.
2. Distributed multi-admin evasion — **resolved** with weighted guild panic plus the two-actor severe fast path.
3. Server identity, expression, invite, scheduled-event, integration, thread, command-permission, and AutoMod destruction gaps — **resolved** in the guarded audit surface.
4. Role-position/hierarchy mutation omission — **resolved** and weighted as high risk when position changes.
5. Sparse gateway executor loss / consume-and-drop — **resolved** with actor resolution before evidence consumption and REST reconciliation fallback.
6. Gateway latency on dangerous role escalation, sensitive role grants/removals, timeout abuse, bot additions, and dangerous role creation — **resolved** with specialized fast paths.
7. Over-broad first-strike coverage for benign creation activity — **resolved** by excluding low-risk member-facing creation/update actions from AntiNuke first-strike classification.

### Defensible prevention advantages

- Vetox publicly lists 21 monitored administrative action types; Dank Shield now covers that published destructive set plus overwrite mutation, AutoMod security-rule tampering, command-permission mutation, integration/thread/soundboard destruction, unban abuse, timeout abuse, role stripping, hierarchy mutation, and guild-wide coordinated panic.
- Wick publicly documents Trusted Admins and Extra Owners as completely immune. Dank Shield does **not** give configured trusted operators unlimited immunity; delegated operators remain bounded by short-window thresholds, a 10-minute hard ceiling, and guild-wide panic.
- Dank Shield uses gateway audit delivery plus target-correct REST fallback and shared dedupe for high-confidence evidence, while keeping one canonical containment policy.
- Dangerous authority escalation is rolled back and the executor contained immediately instead of waiting for a broad nuke threshold.

### Explicit non-claim

Dank Shield does **not** currently provide Wick-equivalent structural snapshot/restore or reversible global role lockdown. Backup/recovery is excluded from this task by product constraint. The superiority claim for DS-SEC-039 is therefore limited to **prevention/containment behavior and monitored destructive/security surfaces**, not post-damage structural restoration.

## Hard platform limits

Dank Shield cannot contain the physical guild owner, override Discord hierarchy or managed-role restrictions, manufacture audit attribution Discord did not provide, defend against compromise of Dank Shield's own bot token, or act while Discord itself is unavailable/rate-limiting required APIs. These must remain visible limits, not be represented as healthy containment.

A previously loaded guild preserves stale cached config through a database outage. A totally cold process with no cache and no reachable authoritative database cannot safely invent the prior AntiNuke policy; durable cold-start security-state recovery remains part of the later persistence/DR remediation work.

## Regression coverage

Focused coverage now includes AntiNuke behavior, adversarial bypasses, definitive containment, race/event coverage, owner control, audit priority, readiness edge cases, extended evasion, gateway runtime, guardian panic, competitive hardening, gateway authority escalation, severe multi-actor coordination, and configuration-history security behavior.

## Definition of done / final validation

- No further prevention bypass is identified in the current competitor/nuker comparison outside the explicit backup/recovery non-scope.
- Python compile and committed-diff checks pass on one final exact head.
- Full repository unit tests and standalone audits pass on that same exact head.
- All companion PR workflows pass on that same exact head.
- Changed-file scope remains entirely AntiNuke runtime, required owner/security bypass closure, API-safety support, focused regressions, or task bookkeeping.
- `main` base drift is rechecked before readiness/merge.
- No duplicate AntiNuke policy engine, duplicate overwrite attribution owner, temporary bypass, or unresolved review thread remains.
- PR description is updated to the final exact head and actual scope without changing code.
- Mark ready and merge only after the final exact-head gate is green; then verify merged `main` CI and production/deployment acceptance before closing DS-SEC-039.
