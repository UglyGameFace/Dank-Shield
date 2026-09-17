# ACTIVE TASK

## DS-SEC-OWNER-POLICY-NORMALIZATION — Normalize AntiNuke guild-owner evidence

**Outcome target:** Guild-owner activity must be classified by actual security severity. Cosmetic/routine owner administration must not produce a false `Owner-Compromise Warning 1/1`; high-confidence destructive/security-authority mutations must still warn immediately; specialized dangerous-role owner paths must no longer be silently skipped.

**Status:** IMPLEMENTING + VALIDATING

**Branch:** `fix/antinuke-owner-policy-normalization`
**Base main:** `4f40659375cf15811dba17a4da484532eb7f1c1e`
**Previous integrated task:** PR #252 merged as `4f40659375cf15811dba17a4da484532eb7f1c1e` and Discloud reported successful deployment for that exact merge commit.

## Root cause

1. `anti_nuke_lockdown_runtime` still wraps the owner incident processor and supplies `threshold_override=1` for every owner event that reaches it, regardless of event semantics.
2. That blanket override can turn routine owner administration into an immediate compromise accusation even when the underlying event is merely bounded/cosmetic evidence.
3. The guardian/gateway surface still routes owner channel creation, harmless role creation, generic role settings/hierarchy activity, moderation, and guild-setting changes into the canonical owner processor.
4. Conversely, specialized dangerous role creation, dangerous role-permission escalation, and security-sensitive member-role grant handlers still return early for the physical guild owner before entering canonical owner-incident policy.
5. Result: some harmless owner actions can be over-classified while some genuinely dangerous owner authority events can be silently skipped.

## Execution paths

False-positive side:

`on_audit_log_entry_create`
→ gateway/guardian classification
→ canonical destructive processor
→ incident owner wrapper
→ lockdown owner first-strike wrapper (`1`)
→ owner incident warning

Silent-skip side:

`on_audit_log_entry_create`
→ incident specialized listener
→ gateway dangerous role/member-role handler
→ `_actor_is_owner_or_bot(...)`
→ early return before canonical owner incident policy

## Implementation scope

- Make `anti_nuke_incident_runtime` the severity-normalization boundary for owner evidence.
- Ignore cosmetic owner role/guild edits as AntiNuke compromise evidence while leaving ordinary modlog behavior untouched.
- Keep routine owner administrative/destructive bursts as bounded evidence with elevated floors rather than `1/1` accusations.
- Keep high-confidence destructive/security-authority actions immediate owner-compromise warnings.
- Route dangerous owner role creation, dangerous permission escalation, security-sensitive role grants, and owner member-role removals through the canonical owner policy instead of gateway early returns.
- Preserve the Discord platform boundary: Dank Shield reports owner compromise but never claims it can kick/ban/strip the physical guild owner.
- Preserve unknown/untrusted actor containment, operational-bot policy, trusted-operator thresholds, overwrite protection, hostile reputation, Strict Lockdown, and self-action protection.

## Focused regression requirements

- cosmetic owner role rename does not produce AntiNuke compromise evidence even when the legacy wrapper supplies `1`
- cosmetic owner guild rename does not produce an owner-compromise warning
- routine owner channel creation remains bounded and cannot trigger `1/1`
- owner channel deletion remains immediate
- owner MFA/security-authority mutation remains immediate
- dangerous owner role creation enters canonical owner policy
- dangerous owner role permission escalation enters canonical owner policy
- owner security-sensitive member-role grant enters canonical owner policy
- existing full AntiNuke and repository regression suites remain green

## Cleanup / follow-up lock

Do not begin the broader AntiNuke runtime-ownership consolidation until this correctness task is implemented, tested, exact-head validated, merged, and verified on `main`. The runtime-consolidation audit remains the next AntiNuke cleanup task after this merge.

## Validation gate

- compile/diff checks
- focused owner-policy regressions
- full unit suite and standalone audits
- every triggered GitHub Actions workflow green on the exact final head
- final diff limited to this task
- branch 0 behind `main`, mergeable, no unresolved review/thread blocker
- merge only the exact validated head
- verify merged commit is current `main` and Discloud/status checks succeed
