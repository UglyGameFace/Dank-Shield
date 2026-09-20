# ACTIVE TASK

## Active task / desired outcome

**P0-INT-001 — Verification Center role-mapping save integrity + native interaction guard**

Make the Verification Center's direct Role Mapping select both natively guarded and truthful about persistence. A failed guild-config write must never be followed by a false “now uses this role” success message.

## Why this is next

PR #277 completed the shared Verification Center canonical-command dispatcher guard and merged as `35714aae0da9b4dcebfcb6d51abfade83f90beba`.

Post-merge verification on `main` confirmed the validated production and regression-test blobs exactly:

- `public_verify_command_center.py` → `c1ae1b0273871025b40d4fd9da657490d75176b8`
- `test_public_verify_command_center_native_interaction_static.py` → `1b91918bbc4c1d0f8ee8f55683d5e5cce2ac55f1`
- canonical compatibility `public_verify_group.py` → `bb7b9c314517a21d14c0f7f16e7380e2fcf15a30`

The next direct mutation boundary is `VerifyRoleSelect.callback` in `public_verify_command_center.py`.

Concrete root cause:

- the callback validates the selected role and then calls `public_verify_group._save_role_config(..., explicit_override=True)`;
- `_save_role_config` catches persistence exceptions internally, prints a warning, and returns normally;
- the callback then unconditionally tells staff `✅ ... now uses <role>`;
- therefore a failed database write can produce a false success message;
- because the persistence exception is swallowed before it reaches the UI callback, adding `run_guarded_interaction()` alone would not fix the bug.

## Scope

In scope:

- `VerifyRoleSelect.callback`;
- native defer-before-save acknowledgement;
- preserving the existing staff gate and `_bot_can_manage_role` hierarchy check;
- preserving the existing explicit-override `_save_role_config` write path;
- refreshing guild config after the attempted save and verifying the exact selected role ID actually persisted;
- refusing the success message when persistence cannot be verified;
- focused regression coverage for native guard ownership, save verification, and no false success;
- P0 interaction ledger updates for this exact slice.

Out of scope:

- changing the broad semantics of `_save_role_config` for runtime discovery/auto-create callers;
- changing role discovery aliases;
- changing canonical `/verify` command behavior;
- changing member role mutations;
- changing Verify panel posting or setup routing;
- ticket/setup/design work;
- removing the global framework interaction monkey patch in this slice.

## Status

**LOCKED — NOT IMPLEMENTED**

## Required behavior to preserve

- only the owner of the Verification Center can use the view;
- staff permission check remains authoritative;
- invalid/non-role selections are rejected;
- bot role hierarchy/manage-role validation remains before persistence;
- the same logical-role → config-key mapping remains authoritative;
- the write remains an explicit override through `_save_role_config(..., explicit_override=True)`;
- a verified successful save still receives the existing human-readable role-mapping success message.

## Implementation rule

Use `stoney_verify.interaction_guard.run_guarded_interaction` as a thin wrapper at `VerifyRoleSelect.callback`, with `defer=True`.

After `_save_role_config` returns, perform a fresh authoritative config read and compare the saved config key to the selected role ID. If the mapping does not match, raise an error inside the guarded action so the native Error ID path owns the failure and the callback does not send success.

Do not change `_save_role_config` globally in this slice because its silent best-effort behavior is also used by auto-discovery/auto-create paths. That broader semantic cleanup belongs to a separate audit item.

Failure guidance must tell staff to reopen Role Mapping and verify the currently saved role before retrying. It must not claim the write definitely failed or definitely succeeded if the verification read itself errors.

## Previous completed slice

**PR #277 — Guard Verification Center canonical dispatcher**

- merged as `35714aae0da9b4dcebfcb6d51abfade83f90beba`;
- verified on `main`;
- validated center blob: `c1ae1b0273871025b40d4fd9da657490d75176b8`;
- validated regression-test blob: `1b91918bbc4c1d0f8ee8f55683d5e5cce2ac55f1`;
- no GitHub workflow runs were created for the exact PR head before merge; this was recorded honestly rather than treated as passing CI.

## Next step

Implement the smallest guarded role-mapping callback with post-save persistence verification and focused regression coverage on a fresh implementation branch.
