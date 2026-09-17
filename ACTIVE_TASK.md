# ACTIVE TASK

## DS-SEC-OWNER-POLICY-NORMALIZATION — Normalize AntiNuke guild-owner evidence

**Outcome target:** Guild-owner activity must be classified by actual security severity. Cosmetic/routine owner administration must not produce a false `Owner-Compromise Warning 1/1`; high-confidence destructive/security-authority mutations must still warn immediately; specialized dangerous-role owner paths must no longer be silently skipped.

**Status:** FINAL EXACT-HEAD VALIDATION

**Branch:** `fix/antinuke-owner-policy-normalization`
**Base main:** `4f40659375cf15811dba17a4da484532eb7f1c1e`
**PR:** #253
**Validated implementation head:** `f8749c44f2ae88f836aa3ca6d5d9117236eb4d56`
**Previous integrated task:** PR #252 merged as `4f40659375cf15811dba17a4da484532eb7f1c1e` and Discloud reported successful deployment for that exact merge commit.

## Root cause

1. `anti_nuke_lockdown_runtime` still wraps the owner incident processor and supplies `threshold_override=1` to owner events reaching it, without understanding whether the audit event is cosmetic, bounded administrative activity, or high-confidence destructive/security activity.
2. That blanket override could turn routine owner administration into an immediate compromise accusation.
3. Conversely, specialized dangerous role creation, dangerous role-permission escalation, and security-sensitive member-role grant handlers returned early for the physical guild owner before reaching canonical owner-incident policy.
4. Result: harmless owner actions could be over-classified while genuinely dangerous owner authority events could be silently skipped.

## Execution paths

False-positive side:

`on_audit_log_entry_create`
→ gateway/guardian classification
→ canonical destructive processor
→ incident owner wrapper
→ legacy owner first-strike override
→ severity normalization now decides benign / bounded / immediate

Silent-skip side:

`on_audit_log_entry_create`
→ incident specialized listener
→ owner-special authority classifier
→ canonical owner incident policy

## Implemented policy

- **Benign owner evidence:** cosmetic guild identity changes such as name/icon/banner/description and cosmetic role changes such as name/color/icon/hoist/mentionable do not enter owner-compromise counters.
- **Bounded owner evidence:** ordinary channel creation, moderation/admin bursts, single-message deletion, onboarding and Stage administration, webhook create/update, integration create/update, vanity/security-adjacent guild settings, harmless role creation, role position or dangerous-permission removal, and member-role removals use elevated owner floors rather than legacy `1/1` treatment.
- **Immediate owner warnings:** channel deletion, permission-overwrite creation/update/deletion, role deletion, member prune, webhook deletion, integration deletion, application-command permission mutation, AutoMod security mutation/deletion, bulk message deletion, guild owner/MFA mutation, dangerous role creation, dangerous role permission grants, and security-sensitive role grants.
- Owner-created OAuth/integration setup remains bounded so it does not conflict with canonical bot-add authorization and hostile-bot correlation.
- Dangerous owner role creation, permission escalation, and sensitive role grants now enter canonical owner policy instead of being silently skipped.
- Discord's platform boundary remains explicit: Dank Shield reports physical-owner compromise but does not claim it can kick, ban, or strip the physical guild owner.
- Unknown/untrusted containment, operational-bot authorization, trusted-operator thresholds, overwrite protection, hostile reputation, Strict Lockdown, and self-action proof remain unchanged.

## Focused regression coverage

Coverage verifies cosmetic role rename/color and cosmetic guild rename remain non-punitive; channel creation and single-message deletion are bounded; onboarding/Stage/webhook-update/integration-create/vanity changes are bounded; channel deletion, MFA mutation, webhook deletion, and integration deletion retain immediate warning behavior; dangerous owner role creation and permission escalation reach canonical owner policy; and security-sensitive member-role grants are no longer skipped.

## Validation results on implementation head `f8749c44f2ae88f836aa3ca6d5d9117236eb4d56`

- committed-diff whitespace check: PASS
- Python compile: PASS
- full unit test suite: PASS
- standalone tool checks: PASS
- public setup/command/invite/safety/design/role/event-boundary audits: PASS
- required `Claim-first ticket security`: PASS
- required `Managed category SQL smoke test`: PASS
- `Application Command Size Diagnostics` #1254: PASS
- `Dank Design Regression CI` #496: PASS
- `Ticket Owner Emergency Override` #837: PASS
- `Profile Runtime Diagnostics` #1003: PASS
- `Dank Shield CI` #2266: PASS
- branch was 4 commits ahead / 0 behind `main`
- PR diff remained exactly 3 task files: `ACTIVE_TASK.md`, `stoney_verify/anti_nuke_incident_runtime.py`, and `tests/test_antinuke_owner_policy_normalization.py`

This bookkeeping update changes the PR head. Every required and companion workflow must pass again on the exact new head before PR #253 can be marked ready or merged.

## Cleanup / conflict inspection

- no threshold-only workaround or owner whitelist was added
- no second punishment engine or new runtime layer was introduced
- canonical bot-add authorization remains authoritative
- no temporary files remain in the PR diff
- broader AntiNuke runtime-ownership consolidation is intentionally excluded from this correctness PR

## Final validation gate

- every triggered workflow succeeds on this exact bookkeeping head
- branch remains 0 behind current `main`
- PR remains mergeable with no unresolved review/thread blocker
- final diff remains limited to the 3 task files
- mark ready only after exact-head green
- merge only the exact validated SHA
- verify resulting merge commit is current `main`
- verify post-merge CI/deployment/Discloud status before releasing the task lock

## Next AntiNuke task after this lock releases

Audit and consolidate the stacked AntiNuke runtime ownership so canonical policy stays in `anti_nuke.py` and gateway/guardian/runtime layers stop repeatedly wrapping the same functions. Do not begin that cleanup until PR #253 is merged and verified on `main`.
