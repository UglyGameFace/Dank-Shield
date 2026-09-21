# ACTIVE TASK

## Active task / desired outcome

**P0-INT-001 — Canonical Verify Panel posting native interaction guard**

Move the canonical `verify_panel()` posting path onto the native interaction service so every entry point gets structured failure handling without duplicating wrappers.

## Why this is next

PR #279 completed Verification Center role-mapping persistence integrity and merged as `373080f76eb255db92dff29abdfc0761be75345c`.

Post-merge verification on `main` confirmed the validated blobs exactly:

- `public_verify_command_center.py` → `2dfc0d4554f2d3cd17fe2658c951245c957736be`
- `test_public_verify_role_mapping_integrity_static.py` → `76ed972b0c21ecc0cb8248edd5ed44055320e22d`

The next direct verification mutation is `verify_panel()` in `stoney_verify/commands_ext/public_verify_basic_panel.py`.

That canonical function is used by:
- the `/verify panel` slash command;
- the Verification Center **Post / Refresh Verify Panel** button;
- setup surfaces that call the same helper.

Current behavior:
- manually defers the interaction;
- performs staff checks and target-channel lookup;
- installs/refreshes the Basic Verify runtime;
- calls `post_basic_verify_panel(...)`, which creates/replaces a live Verify panel;
- catches all posting exceptions locally and flattens them into `Could not post panel: <Type>`;
- does not use `run_guarded_interaction()`, so unexpected posting failures do not get native Error IDs.

## Scope

In scope:
- `verify_panel()` and a thin extracted action body;
- native defer-before-read/post acknowledgement;
- preserving staff checks, channel selection, runtime install, disabled-mode handling, and existing success text;
- removing only the broad unexpected exception swallow around live panel posting;
- focused regression coverage;
- P0 readiness ledger updates.

Out of scope:
- changing Basic Verify runtime installation semantics;
- changing `post_basic_verify_panel` behavior;
- changing verification mode authorization;
- changing setup callers;
- changing Verification Center navigation;
- member role mutations;
- ticket/design/setup work;
- removing the global framework interaction monkey patch.

## Status

**LOCKED — NOT IMPLEMENTED**

## Required behavior to preserve

- staff-only gate;
- saved verification channel still wins over current channel;
- current text channel remains the fallback;
- Basic Verify disabled state still returns its existing explanation;
- runtime install result still controls the success suffix;
- `post_basic_verify_panel` remains the canonical live panel mutation owner;
- slash/center/setup callers keep using the same `verify_panel()` API.

## Implementation rule

Wrap the canonical `verify_panel()` boundary with `run_guarded_interaction(..., defer=True)` and keep the existing business logic in a separate action body.

Do not wrap only the Verification Center button. Guarding the canonical function covers all callers and avoids duplicate native wrappers.

Unexpected failure guidance must be cautious because a create/replace operation may have partially completed before an exception. Staff should reopen/refresh the Verify Panel flow and inspect the target channel before retrying, with the Error ID available in `/dank diagnostics`.

## Previous completed slice

**PR #279 — Guard Verification Center role mapping persistence**

- merged as `373080f76eb255db92dff29abdfc0761be75345c`;
- verified on `main`;
- validated production blob: `2dfc0d4554f2d3cd17fe2658c951245c957736be`;
- validated regression-test blob: `76ed972b0c21ecc0cb8248edd5ed44055320e22d`;
- no CI run was falsely represented as passing.

## Next step

Implement the canonical Verify Panel native guard on a fresh branch, add focused regression coverage, validate exact-head ownership/order, then merge and verify on `main`.
