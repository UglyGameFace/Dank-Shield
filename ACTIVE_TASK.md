# ACTIVE TASK

## DS-FIX-ANTINUKE-BOT-PERMISSION-INTEGRITY — Preserve legitimate bot authority

**Outcome:** AntiNuke keeps unknown bot installs behind the canonical bot-add authorization gate while treating already-operational bot actors as bounded delegated principals, preventing one legitimate bot action or a guardian panic burst from immediately kicking the bot or stripping all of its manageable roles.

**Status:** VALIDATED; MERGE PENDING. Runtime/code head `c5d1d8e27f7edd373141793722cf2303fc192319` passed every required workflow. This task-record-only commit changes no runtime code and must receive the repository's required exact-head checks before PR #238 is merged.

**Repair branch:** `fix/antinuke-legit-bot-permission-integrity`
**Repair PR:** #238 — `Fix AntiNuke legitimate bot permission damage`
**Validated runtime/code head:** `c5d1d8e27f7edd373141793722cf2303fc192319`
**Prior merged PR:** #236 — `Fix AntiNuke authorized bot trust ownership`
**Prior merge SHA:** `eb94e6e46d0c0cfdef2657eb302d0538a6e804fa`
**Superseded closure PR:** #237 — closed without merge after live permission damage was reported

## Scope

- `stoney_verify/anti_nuke_lockdown_runtime.py`
- `stoney_verify/anti_nuke_product_policy_runtime.py`
- `tests/test_antinuke_legit_bot_permission_integrity.py`
- this task record

No unrelated AntiNuke redesign, setup, tickets, verification, moderation, or PR #235 work is included.

## Root cause

1. Native destructive-event processing classified every bot except Dank Shield itself as an untrusted actor, making ordinary bot actions first-strike events.
2. Lockdown structural overrides could force `threshold_override=1`, collapsing otherwise bounded structural actions to one strike.
3. Canonical containment can kick the attributed actor and, if that fails, strip every manageable role, not only dangerous roles.
4. Guardian panic containment can apply that same containment to observed peer actors, creating multi-bot blast radius.
5. Gateway-fast dangerous role/member-role paths could roll back role permissions before ordinary threshold processing.
6. The later-installed Strict Lockdown product-policy layer rebuilt guardian wrappers and could reintroduce synthetic-untrusted/first-strike behavior after the lockdown repair unless it shared the same bot boundary.
7. Making every bot globally trusted would weaken bot-add authorization because a bot inviter could then authorize arbitrary new bot installs, so bot-add authorization requires a separate trust context.
8. The first exact-head CI attempt also exposed three task-owned test-contract failures: two legacy partial AntiNuke test doubles lacked newly canonical hooks, and the product-policy test replaced the lockdown module with a minimal fake that did not own the private bot classifier.

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
- Product policy owns its local bot classifier instead of depending on a private helper from another runtime layer.
- Lockdown canonical security hooks remain fail-closed for the real production module while focused unit-test doubles may omit unrelated hooks.
- Durable hostile reputation remains authoritative and can still allow containment of a known-hostile bot.

## Validation / results

Exact runtime/code head `c5d1d8e27f7edd373141793722cf2303fc192319`:

- Dank Shield CI #2168: SUCCESS
- Python compile: SUCCESS
- full unit suite: SUCCESS; the prior 3 failures are cleared
- committed diff whitespace: SUCCESS
- standalone tool checks: SUCCESS
- public setup/isolation audit: SUCCESS
- canonical command-surface audits: SUCCESS
- invite-permission audit: SUCCESS
- setup-safety audit: SUCCESS
- Dank Design Smart Auto-Detect audit: SUCCESS
- role-truth ownership audit: SUCCESS
- event-boundary ownership audit: SUCCESS
- managed-category SQL smoke: SUCCESS
- claim-first ticket security: SUCCESS
- Dank Design Regression CI #418: SUCCESS
- Application Command Size Diagnostics #1171: SUCCESS
- Ticket Owner Emergency Override #739: SUCCESS
- Profile Runtime Diagnostics #925: SUCCESS
- branch freshness: 0 commits behind `main` at validated code head
- changed-file inspection: task-only files; no unrelated generated, conflict, secret-bearing, or accidental files found
- PR review/thread inspection before record closeout: no blocking review/thread findings

The earlier exact-head failure on `0b59ae5c2ceb14e306a4dd06711717fbd2f37fd5` was fully diagnosed rather than retried blindly: 1490 tests passed and three task-owned compatibility assertions failed. Those failures were corrected by commits `ce579806fe8010161b43005fe5f9584796ae5286` and `c5d1d8e27f7edd373141793722cf2303fc192319`.

## Cleanup / conflicts

- No duplicate bot-add authorization owner was added.
- No startup workaround, retry loop, blanket bot exemption, or containment bypass was introduced.
- The later product-policy wrapper now shares the same operational-bot boundary instead of undoing lockdown behavior.
- Real production canonical hooks remain mandatory and fail closed if unexpectedly absent.
- PR #235 and unrelated setup/ticket/moderation work remain outside this task.

## Blocker / runtime boundary

No repository-code blocker remains on the validated runtime head. The repository repair prevents future AntiNuke permission damage, but it cannot reconstruct Discord permissions already stripped from live bots. Existing damaged bot roles/permissions must be restored in Discord after the repaired revision is deployed.

## Next step

Let the required checks pass on this record-only final head, mark PR #238 ready, merge it with the expected head SHA, then use the PR's canonical merge metadata as the final merge record rather than creating another bookkeeping commit and restarting CI again.
