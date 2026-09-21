# ACTIVE TASK

## Active task / desired outcome

**P0-INT-001 — Surface recent native interaction failures in /dank diagnostics**

Make native Error IDs actionable by showing a sanitized, guild-isolated recent failure summary inside the existing read-only `/dank diagnostics` report.

## Why this is next

PR #282 completed canonical Verify Panel interaction hardening and merged as `c6e48d0a957366fd77b42d3fcee7cb97b5178431`.

Post-merge verification on `main` confirmed the validated blobs exactly:

- `public_verify_basic_panel.py` → `c268e4780c446ff543f76dd2b3ad9fa9e482b7da`
- `test_public_verify_panel_native_interaction_static.py` → `cc7f30b17a321f80097d434dd8a75deee98480ff`

The native interaction service stores up to 250 recent `InteractionFailureRecord` entries in-process, and user-facing errors direct admins to `/dank diagnostics`. Before this slice, diagnostics did not consume that ring.

## Status

**IMPLEMENTED — targeted privacy/grammar validation passed; pending exact-head PR/main verification**

## Implementation

`public_diagnostics_group.py` now reads `recent_interaction_failures(limit=250)` and builds a read-only **Recent Native Interaction Failures** field.

Privacy and isolation are enforced inside the helper:

- records are filtered by exact `record.context.guild_id == interaction.guild.id` before any rendering;
- only the newest five matching records are shown;
- output includes only Error ID, sanitized action name, stage/error type, and whether the user was notified;
- action/error tokens strip newlines and backticks;
- raw `error_message`, `fix_hint`, `traceback_text`, `extra`, user ID, channel ID, and message ID are never rendered;
- the summary is capped at 1,000 characters and its line body at 850 characters;
- empty state says no failures are recorded **for this server in this process**;
- diagnostics still has no failure-clear or mutation path.

## Validation

Targeted branch validation passed:

- branch is 0 commits behind current `main`;
- production diff is limited to `public_diagnostics_group.py` plus one focused privacy regression test before ledger updates;
- production failure-helper region parses under Python 3.11 grammar;
- focused regression test parses under Python 3.11 grammar;
- exact source replay confirms:
  - ring import and read-only consumption;
  - guild filtering happens before newest-five selection/rendering;
  - newest-first ordering;
  - only the five allowed display attributes are accessed for output;
  - prohibited raw/sensitive fields are absent from the helper;
  - field and line caps remain below Discord limits;
  - current guild ID is passed explicitly from the diagnostics command;
  - `clear_recent_interaction_failures` is not imported or called.
- behavioral privacy simulation with mixed-guild fake records passed:
  - current-guild records rendered;
  - other-guild Error IDs and secret fields did not render;
  - newest record appeared first;
  - newline/backtick action text was sanitized;
  - output stayed <= 1000 characters;
  - unrelated-guild empty state remained correct.

Validated branch blobs:

- `public_diagnostics_group.py` → `8910e492f2afc5078acbaeedd94572d7e46fb585`
- `test_public_diagnostics_native_interaction_failures_static.py` → `893dcf65862e12afd52b1925c697d78264a05c30`

Full GitHub Actions execution state must be checked on the final PR head. No absent runner execution will be represented as passing CI.

## Previous completed slice

**PR #282 — Guard canonical Verify Panel posting**

- merged as `c6e48d0a957366fd77b42d3fcee7cb97b5178431`;
- verified on `main`;
- validated production blob: `c268e4780c446ff543f76dd2b3ad9fa9e482b7da`;
- validated regression-test blob: `cc7f30b17a321f80097d434dd8a75deee98480ff`.

## Next step

Open the focused diagnostics PR, verify its exact head and runner state, merge with an expected-head guard if clean, then verify the validated diagnostics/test blobs on `main`.
