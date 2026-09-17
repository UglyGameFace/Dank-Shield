# ACTIVE TASK

## DS-SEC-OWNER-POLICY-NORMALIZATION — Normalize AntiNuke guild-owner evidence

**Status:** FINAL EXACT-HEAD VALIDATION AFTER PR #251 INTEGRATION

**Branch:** `fix/antinuke-owner-policy-normalization`
**Current base main:** `0c8d8ae1971656f0c5c2e9ef95ebd11a9c94fa97`
**PR:** #253

## Previous task closed

PR #251 (`DS-MEMBER-JOIN-LOG-REGRESSION`) was synchronized with then-current `main`, passed every exact-head workflow, merged as `0c8d8ae1971656f0c5c2e9ef95ebd11a9c94fa97`, became current `main`, and Discloud reported `discloud/commit: success` for that exact merge commit.

The Single Active Task Lock now returns to this AntiNuke owner-policy normalization task.

## Outcome target

Guild-owner activity must be classified by actual security severity. Cosmetic/routine administration must not become a false `Owner-Compromise Warning 1/1`; high-confidence destructive/security-authority mutations must remain immediate warnings; specialized dangerous-role owner paths must not be silently skipped.

## Root cause

1. The legacy lockdown owner wrapper supplies `threshold_override=1` without knowing whether the event is cosmetic, routine, bounded, or genuinely destructive.
2. That can turn harmless owner administration into an immediate compromise accusation.
3. Specialized dangerous role creation, dangerous role-permission escalation, and security-sensitive member-role grant handlers historically return early for the physical guild owner before canonical owner policy.
4. The result is inconsistent in both directions: some harmless owner activity can over-alert while some dangerous owner authority events can be skipped.

## Implemented severity policy

- cosmetic guild identity and cosmetic role edits are non-punitive owner evidence
- routine/lower-confidence administration uses elevated bounded owner thresholds instead of `1/1`
- channel creation, single-message deletion, onboarding/Stage administration, webhook create/update, integration create/update, vanity/security-adjacent guild settings, harmless role creation, role position/permission removal, and member-role removal are bounded
- channel deletion, permission-overwrite mutation, role deletion, prune, webhook/integration deletion, command-permission mutation, AutoMod security mutation/deletion, bulk message deletion, guild owner/MFA mutation, dangerous role creation/escalation, and sensitive role grants remain immediate
- owner-created OAuth/integration setup remains bounded so it does not conflict with canonical bot-add authorization and hostile-bot correlation
- dangerous owner role creation, permission escalation, and sensitive member-role grants now enter canonical owner policy instead of being silently skipped

Discord's platform authority boundary is unchanged: Dank Shield can detect/report physical-owner compromise but cannot kick, ban, or strip the guild owner.

## Preserved behavior

Unknown/untrusted actor containment, operational-bot authorization, trusted-operator thresholds, overwrite protection, hostile reputation, Strict Lockdown, self-action protection, canonical bot-add authorization, and the newly merged PR #251 member-lifecycle route remain unchanged.

No duplicate punishment engine or extra runtime patch layer is introduced.

## Regression coverage

`tests/test_antinuke_owner_policy_normalization.py` covers cosmetic role rename/color, cosmetic guild rename, bounded channel creation, bounded single-message deletion, bounded onboarding/Stage/webhook-update/integration-create/vanity changes, immediate channel deletion/MFA/webhook-delete/integration-delete, dangerous owner role creation and permission escalation, and sensitive member-role grants.

## Validation history

The prior final head `548489871fb0435d23afdac9f0c36fd89f9f775e` passed all required and companion workflows, including full unit tests, Python compile, standalone audits, Application Command Size Diagnostics, Dank Design Regression CI, Ticket Owner Emergency Override, Profile Runtime Diagnostics, Claim-first ticket security, Managed category SQL smoke test, and Dank Shield CI.

PR #251 then merged into `main`, making this branch stale by history only. The lifecycle implementation does not overlap this task's AntiNuke production/test files; `ACTIVE_TASK.md` is the only bookkeeping overlap.

This branch now incorporates PR #251's merged `main` exactly while preserving the AntiNuke task files. Because the head SHA changed, all validation must pass again on the exact synchronized head before merge.

## Intended PR scope

- `ACTIVE_TASK.md`
- `stoney_verify/anti_nuke_incident_runtime.py`
- `tests/test_antinuke_owner_policy_normalization.py`

The lifecycle changes inherited from `main` are not part of PR #253's AntiNuke diff.

## Merge gate

- synchronized branch is 0 behind current `main`
- every required and companion workflow passes on the exact head
- final diff is limited to the three AntiNuke task files
- no unresolved review/thread blocker exists
- PR is mergeable and marked ready
- merge only the exact validated head
- verify resulting merge commit is current `main`
- verify `discloud/commit` succeeds before releasing the task lock

## Next step

Run the full exact-head validation wave on the synchronized PR #253 head. If every check is green, mark the PR ready, merge that exact SHA, verify `main` and Discloud deployment, then release this task lock and move to the broader AntiNuke runtime-ownership consolidation as the next AntiNuke cleanup task.