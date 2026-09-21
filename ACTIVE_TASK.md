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

**IMPLEMENTED — targeted validation passed; pending exact-head PR/main verification**

## Required behavior preserved

- staff-only gate remains intact;
- saved verification channel still wins over current channel;
- current text channel remains the fallback;
- Basic Verify disabled state still returns its existing explanation;
- runtime install result still controls the success suffix;
- `post_basic_verify_panel` remains the canonical live panel mutation owner;
- slash/center/setup callers still use the same `verify_panel()` API;
- the old `ephemeral=True, thinking=True` acknowledgement behavior is preserved.

## Implementation

`verify_panel()` now runs behind `run_guarded_interaction`.

The guard uses `defer=False` intentionally. The first operation inside the guarded action keeps the existing:

`interaction.response.defer(ephemeral=True, thinking=True)`

This preserves component/slash acknowledgement behavior exactly while allowing defer failures to surface through the native guard instead of being swallowed.

The live panel posting logic moved into `_verify_panel_action` without changing staff checks, channel selection, runtime install, disabled-mode handling, success suffixes, or `post_basic_verify_panel(...)` ownership.

The old broad posting `except Exception` was removed so unexpected live-panel failures reach the native structured Error ID path. Recovery guidance tells staff to inspect the target channel before retrying because a create/replace operation may have partially completed.

## Validation

Targeted branch validation passed:

- branch is 0 commits behind current `main`;
- diff is limited to `public_verify_basic_panel.py` plus one focused regression test;
- modified Verify Panel region parses under Python 3.11 grammar;
- focused regression test parses under Python 3.11 grammar;
- exact source replay confirms:
  - native guard owns the canonical `verify_panel()` boundary;
  - `thinking=True` defer remains the first I/O inside the guarded action;
  - defer precedes staff check, channel selection, runtime install, and live panel posting;
  - `post_basic_verify_panel(...)` remains the canonical mutation;
  - disabled Basic Verify handling and both success suffixes remain present;
  - the broad `Could not post panel: <Type>` exception swallow is gone;
  - `/verify panel`, Verification Center, compact setup testing, and setup recommendations still call the same canonical `verify_panel()`.
- existing Basic Verify restart/authorization tests do not require the retired broad exception shape.

Validated branch blobs:
- `public_verify_basic_panel.py` → `c268e4780c446ff543f76dd2b3ad9fa9e482b7da`
- `test_public_verify_panel_native_interaction_static.py` → `cc7f30b17a321f80097d434dd8a75deee98480ff`

Full GitHub Actions execution state must be checked on the final PR head. No absent runner execution will be represented as passing CI.

## Previous completed slice

**PR #279 — Guard Verification Center role mapping persistence**

- merged as `373080f76eb255db92dff29abdfc0761be75345c`;
- verified on `main`;
- validated production blob: `2dfc0d4554f2d3cd17fe2658c951245c957736be`;
- validated regression-test blob: `76ed972b0c21ecc0cb8248edd5ed44055320e22d`;
- no CI run was falsely represented as passing.

## Next step

Open the focused PR, verify its exact head and CI execution state, merge with an expected-head guard if clean, then verify the validated production/test blobs on `main`.
