# ACTIVE TASK

## Active task / desired outcome

**P0-INT-001 — Dank Design reviewed Apply / Undo native interaction guard**

Migrate the consolidated Dank Design state-changing Apply and Undo interaction boundaries to the native interaction service without redesigning the existing transaction, preflight, compensation, snapshot, or navigation behavior.

## Scope

In scope:
- `ReviewedPreviewView.apply` in `public_design_studio_v2.py`;
- consolidated Undo open/confirm boundaries;
- safe early user responses for stale preview, missing preview, blockers, busy guild lock, and missing Undo snapshot;
- focused regression coverage proving these mutation boundaries use `run_guarded_interaction()`;
- update the production-readiness ledger for the exact slice completed.

Out of scope:
- changing apply/undo transaction semantics;
- changing rename ordering, compensation, snapshot persistence, rule persistence, or delay timing;
- migrating unrelated V2 selectors/buttons;
- setup, tickets, verification, invite policy, command registry, settings, or startup guards.

## Status

**VALIDATED — targeted exact-head checks pass; full GitHub Actions cannot start a runner**

## Root cause

The exact-format editor slice from the older command-center entry is already native-guarded on current `main`. The consolidated V2 reviewed Apply and Undo owners are not.

`ReviewedPreviewView.apply`, `_open_undo`, and `UndoConfirmView.confirm` currently execute through raw component callbacks. Their business logic has strong preflight/compensation behavior, but unexpected callback/defer/send exceptions can still escape to the view-level fallback instead of producing the native structured interaction failure record and Error ID.

These are higher-risk than cosmetic editor callbacks because Apply and Undo can mutate live channel/category names.

## Required behavior to preserve

- stale preview refusal before mutation;
- blocker refusal before mutation;
- per-guild design lock;
- full-batch preflight;
- apply compensation and residual snapshot behavior;
- separator-setting compensation;
- durable/memory-only Undo snapshot behavior;
- Undo preflight and stale snapshot refusal;
- existing progress and success embeds;
- existing reviewed Apply / Undo navigation and custom IDs.

## Implementation rule

Use `stoney_verify.interaction_guard.run_guarded_interaction` as the native callback boundary. Keep the existing legacy guild mutation lock inside the guarded action. Use `safe_send_interaction` for early business-rule responses that currently call raw `interaction.response.send_message`.

The guard error guidance must not falsely claim that a live mutation definitely did or did not occur after an unexpected exception. It must tell the user to inspect the current Server Design state before retrying and surface the Error ID through the native diagnostics path.

## Validation

Targeted validation passed for the changed production/test surface:

- branch remained 0 commits behind `main` during implementation;
- production diff was reduced from a 490-line indentation-heavy form to a thin-wrapper implementation (83 changed production lines versus `main`);
- exact changed guard helper, Undo-open wrapper, Apply wrapper/safe-send syntax, Undo-confirm wrapper/safe-send syntax, and the new regression test parse under Python 3.11 grammar;
- focused source assertions prove reviewed Apply, Undo open, and Undo confirm call the native interaction guard;
- no raw early `interaction.response.send_message` remains in the reviewed Apply or Undo-confirm mutation regions;
- reviewed Apply still contains pending-preview validation, the per-guild lock, full-batch preflight, `apply_prepared`, compensation, residual snapshot handling, durable/memory fallback snapshot handling, and pending cleanup;
- Undo confirm still contains the per-guild lock, latest-snapshot validation, stale-snapshot refusal, Undo preflight, `undo_prepared`, and conditional snapshot pop;
- PR patch inspection found no conflict markers or trailing added whitespace;
- PR #271 is mergeable with zero unresolved review threads.

Full GitHub Actions is not a code result for this head. Fresh Dank Design Regression CI and Dank Shield CI runs completed before step 1 with `steps: null` and `logs_url: null`, including the repository Python compile job. The isolated execution container also cannot resolve `github.com`. This is the same pre-runner infrastructure failure already tracked separately; it is not represented as passing CI.

## Remaining risk

A complete repository checkout / pytest replay could not run in the current infrastructure. The change is therefore validated by exact changed-source Python 3.11 grammar checks, focused static regression replay, transaction-primitive preservation checks, patch hygiene, and GitHub mergeability/review state. The full suite must be restored once runner/account/DNS infrastructure is fixed.

## Previous completed task

PR #270 expanded category frames to 80 and removed project-specific preview leakage. It was merged and verified on `main` before this lock was opened.

## Next step

Update PR #271 with the final-head evidence, mark it ready, merge with an expected-head guard, verify the validated production/test blobs on `main`, then close this slice and select the next single P0-INT interaction boundary.
