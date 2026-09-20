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

**IN PROGRESS**

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

## Validation targets

- changed production blocks parse under Python 3.11 grammar;
- static regression proves Apply, Undo open, and Undo confirm are native-guarded;
- no raw early `interaction.response.send_message` remains in the guarded Apply/Undo mutation regions;
- existing transactional ownership tests remain structurally satisfied;
- branch remains focused and current with `main`;
- full GitHub Actions status is recorded separately if the known pre-runner infrastructure failure persists.

## Previous completed task

PR #270 expanded category frames to 80 and removed project-specific preview leakage. It was merged and verified on `main` before this lock was opened.

## Next step

Implement the native Apply/Undo guard slice, add focused regression coverage, validate the exact head, then update the command center and merge only after the final-head evidence is clean.
