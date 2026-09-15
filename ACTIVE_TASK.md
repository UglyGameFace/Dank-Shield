# ACTIVE TASK

## DS-FIX-ANTINUKE-BOT-PERMISSION-INTEGRITY — Preserve legitimate bot authority

**Outcome:** AntiNuke keeps unknown bot installs behind the canonical bot-add authorization gate while treating already-operational bot actors as bounded delegated principals, preventing one legitimate bot action or a guardian panic burst from immediately kicking the bot or stripping all of its manageable roles.

**Status:** IMPLEMENTATION COMPLETE; EXACT-HEAD VALIDATION PENDING. The prior task closure is suspended until the repair head passes required CI and final diff/review checks.

**Repair branch:** `fix/antinuke-legit-bot-permission-integrity`
**Repair PR:** #238 — `Fix AntiNuke legitimate bot permission damage` (draft during validation)
**Prior merged PR:** #236 — `Fix AntiNuke authorized bot trust ownership`
**Prior merge SHA:** `eb94e6e46d0c0cfdef2657eb302d0538a6e804fa`
**Superseded closure PR:** #237 — closed without merge after live permission damage was reported

## Scope

- `stoney_verify/anti_nuke_lockdown_runtime.py`
- `stoney_verify/anti_nuke_product_policy_runtime.py`
- focused legitimate-bot permission-integrity regression coverage
- this task record

No unrelated AntiNuke redesign, setup, tickets, verification, moderation, or PR #235 work is included.

## Root cause

1. Native destructive-event processing classifies every bot except Dank Shield itself as an untrusted actor, making ordinary bot actions first-strike events.
2. Lockdown structural overrides can force `threshold_override=1`, collapsing even otherwise bounded structural actions to one strike.
3. Canonical containment kicks the attributed actor and, if that fails, strips every manageable role, not only dangerous roles.
4. Guardian panic containment can apply that same containment to observed peer actors, creating multi-bot blast radius.
5. Gateway-fast dangerous role/member-role paths can roll back role permissions before ordinary threshold processing.
6. The later-installed Strict Lockdown product-policy layer can rebuild guardian wrappers and reintroduce synthetic-untrusted/first-strike behavior after the lockdown repair unless it shares the same bot boundary.
7. Simply making all bots trusted globally would weaken bot-add authorization because a bot inviter could then authorize arbitrary new bot installs. Bot-add authorization therefore needs a separate trust context.

## Repair behavior

- Operational bot actors are treated as delegated for ordinary destructive-event thresholds.
- Structural and panic `threshold_override=1` values are ignored for bot actors so one attributed action cannot destroy their role state.
- Direct containment preserves a bot actor unless the bot has active hostile reputation; threshold-triggered canonical processing may still contain a bot after it actually crosses configured limits.
- Bot-add authorization runs in an isolated context that still requires explicit human/role trust or target bot pre-approval; implicit operational-bot trust cannot authorize a new bot install.
- Guardian strict overwrite/AutoMod rollback does not force bot actors through the synthetic untrusted proxy.
- Gateway-fast dangerous role create/update by a bot uses canonical threshold processing instead of immediate rollback/containment.
- Gateway member-role handling does not strip newly granted roles from a bot target.
- Native member dangerous-role grants and bot-only role permission escalation are protected from immediate rollback.
- The final Strict Lockdown product-policy layer preserves the same bounded-bot rule for direct strict actions, guardian processing, overwrite rollback, and AutoMod rollback.
- Durable hostile reputation remains authoritative and can still allow containment of a known-hostile bot.

## Validation added

Focused regressions cover:

- bot actors receiving bounded thresholds instead of forced first strike
- bot-add authorization remaining strict despite operational-bot delegated trust
- direct bot containment being blocked until threshold/hostile conditions justify it
- native member-role protection for bot targets
- guardian strict rollback preserving the real bot actor instead of the untrusted proxy
- gateway bot role creation/update using canonical thresholds
- gateway bot targets retaining newly granted roles
- final product-policy installation preserving bot thresholds and real bot identity in Strict Lockdown

## Pending validation

- exact-head full GitHub CI
- Python compile and complete unit suite
- focused AntiNuke regression suite results
- required SQL/security lanes
- final diff and changed-file inspection
- PR review/thread state
- merge-base freshness against canonical `main`

## Blocker / runtime boundary

The repository repair can prevent future AntiNuke permission damage, but GitHub cannot reconstruct Discord permissions that were already stripped in the live server. Existing damaged bot roles/permissions must be restored in Discord after the repaired revision is deployed.

## Next step

Freeze PR #238's exact head, resolve only task-owned CI failures, merge after every required check passes, then close the task record with the validated head and canonical merge SHA.
