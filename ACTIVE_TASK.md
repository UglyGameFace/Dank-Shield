# ACTIVE TASK

## DS-FIX-TICKET-REBUILD-READINESS — Stop false-positive ticket setup readiness

**Outcome:** `Rebuild Default Ticket Choices` no longer treats existing/default ticket-choice rows as proof that ticket creation is operationally ready.

**Status:** MERGED + POST-MERGE VALIDATED. Repository implementation and production Supabase promotion are complete. The only remaining unvalidated environment detail is whether the live Discloud bot process is currently running the merged revision; GitHub does not expose that host runtime revision and this repository has no Discloud deployment workflow proving it automatically.

**Implementation PR:** #230 — `Stop ticket choice rebuild from falsely reporting setup ready`
**Validated PR head:** `383c1cbcc706560c5d6b4559d5992dd6916c7cb4`
**Merge SHA:** `beedb110fed87a0dd0ab558075b9246fe897bb43`
**Canonical main:** `beedb110fed87a0dd0ab558075b9246fe897bb43`

## Scope

- `stoney_verify/commands_ext/public_setup_recovery.py`
- focused regression coverage in `tests/test_setup_rebuild_ticket_readiness.py`
- no ticket creation schema, command ownership, interaction-lock ownership, moderation, verification, or unrelated setup behavior was changed

## Root cause

`public_setup_recovery._rebuild_recommended_menu()` previously treated successful ticket-choice seeding, or the fact that default choices already existed, as the complete readiness result.

Actual ticket creation uses `public_ticket_panel_clean._ticket_setup_preflight()`, which separately validates the operational ticket path, including the Active Tickets category, staff role, category privacy/permissions, and ticket-panel channel permissions.

That split produced the user-visible contradiction where rebuild reported a green success result while a member pressing **Create Ticket** was immediately blocked by the canonical preflight.

## Execution path after the fix

1. Rebuild/confirm the default ticket-choice catalog using the existing canonical seed owner.
2. Detect when ticket choices still require owner confirmation.
3. Run the same canonical `_ticket_setup_preflight()` used by real ticket creation.
4. Fail closed if readiness cannot be verified.
5. Surface blockers and warnings directly in the rebuild result.
6. Return ready only when owner confirmation is not pending and ticket-creation preflight has no blockers.

Warnings remain non-blocking, matching the real ticket-creation path.

## Changes

- added post-rebuild canonical ticket preflight
- removed the false implication that `Default ticket choices already exist` means setup is ready
- added clear `ticket setup is not ready` output for blockers
- added repair guidance to **Safety & Repair → Specific Channel** / **Fix Channel Access**
- added fail-closed behavior if the canonical preflight throws or cannot complete
- added owner-confirmation-required handling

## Regression coverage

Focused tests prove:

- existing ticket choices + missing Active Tickets access cannot return ready
- owner confirmation required cannot return ready
- a clean canonical ticket preflight can return ready
- preflight failure returns not-ready instead of optimistic success

## Validation results

### Exact PR head

On `383c1cbcc706560c5d6b4559d5992dd6916c7cb4`:

- Dank Shield CI #2137 — SUCCESS
- Application Command Size Diagnostics #1144 — SUCCESS
- Ticket Owner Emergency Override #708 — SUCCESS
- Managed category SQL smoke test — SUCCESS
- Claim-first ticket security — SUCCESS
- full Python/unit/audit lane — SUCCESS
- PR was mergeable with zero submitted reviews and zero review threads
- latest canonical main had been integrated before this validation pass

### Post-merge canonical main

On merge SHA `beedb110fed87a0dd0ab558075b9246fe897bb43`:

- Dank Shield CI #2139 — SUCCESS
- full Python compile/unit/audit lane — SUCCESS
- Managed category SQL smoke test — SUCCESS
- Claim-first ticket security — SUCCESS
- Ticket Owner Emergency Override #710 — SUCCESS
- Deploy Supabase migrations #33 — SUCCESS
  - validated release checkout passed
  - immutable current-main verification passed
  - required-secret validation passed
  - Supabase CLI/project link passed
  - migration status and preview passed
  - pending migration apply passed
- canonical `main` remained exactly `beedb110fed87a0dd0ab558075b9246fe897bb43` after promotion

## Cleanup / conflict inspection

- PR #230 differs from its integrated base by exactly two intended files: the recovery implementation and its focused regression tests.
- no unrelated feature behavior was mixed into the fix.
- no review threads or requested changes remain.
- the stale previous active-task record was discovered after merge and is being corrected by a bookkeeping-only follow-up PR.

## Remaining blocker / risk

The repository is configured for Discloud with app ID `stoneyverify`, `MAIN=main.py`, and `AUTORESTART=true`, but that config does not prove that a GitHub merge automatically uploads the new source revision to the running Discloud app.

No Discloud deployment workflow exists in this repository, and the currently available GitHub access cannot read the live Discloud process revision. Therefore the code fix is merged and fully validated in canonical source, but the exact source revision running on the live Discloud process remains externally unverified.

## Backlog

- unrelated prior audit follow-ups remain separate and must not be mixed into this task
- prior interaction-guard lock-retention debt remains a separate concurrency task

## Next step

Verify the live Discloud `stoneyverify` process is running merge SHA `beedb110fed87a0dd0ab558075b9246fe897bb43`, or redeploy/restart it from canonical `main` if the host does not automatically track GitHub. Once that runtime revision is confirmed, this task can be marked fully closed with no remaining validation gap.
