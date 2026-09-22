# ACTIVE TASK

## Active task / desired outcome

**P0-CMD-CLEANUP-001 — Retire dormant global command cleanup patcher**

Remove the obsolete `startup_guards/slash_command_cleanup.py` CommandTree monkey patch and its ticket-panel command epoch shim now that canonical command ownership lives in `stoney_verify.command_runtime.DankCommandTree`.

## Status

**IMPLEMENTED — exact-head validation pending**

Branch: `audit/retire-dormant-command-cleanup-20260921`

Base: current `main` after PR #290.

## Previous task closed

**P0-GUARD-EMBED-001** is complete.

PR #290 merged as `efef78fa9ed3f5be744d2585b1e9ed93f4209566`.

Exact implementation head `ee5020269ded0a40b541697eb7f4cb421969b347` passed:

- diff integrity;
- Python compile;
- startup guard retirement regression;
- native newline owner regression;
- startup/ownership focused tests;
- full suite: **1794 passed, 79 warnings, 0 failures**.

Post-merge verification confirmed:

- `embed_literal_newline_guard.py` absent;
- obsolete guard test absent;
- historical metadata entry absent;
- native `clean_design_text` owner present;
- native newline regression present.

## Root cause / ownership finding

`startup_guards/slash_command_cleanup.py` is a dormant global CommandTree patcher:

- no production installer imports or applies it;
- normal startup does not iterate the historical startup-guard inventory;
- importing the module immediately calls `install_slash_command_cleanup_guard()`;
- that function replaces `discord.app_commands.CommandTree.sync` and `clear_commands` process-wide;
- useful sync behavior has already moved to `stoney_verify.command_runtime.DankCommandTree`.

The only remaining code importer is `ticket_panel_command_epoch_guard.py`, which merely mutates the dormant cleanup guard's `COMMAND_CLEANUP_EPOCH` to force a one-time legacy ticket-panel sync.

That epoch shim is itself not production-owned. It is referenced only by the dormant `ticket_panel_doctor_production_wording.py`, audit tooling, and workflow path metadata.

## Native owner

`stoney_verify.command_runtime` owns:

- `DankCommandTree`;
- public command-surface validation;
- unchanged global sync hashing/state;
- `DANK_SKIP_UNCHANGED_GLOBAL_SYNC`;
- `DANK_FORCE_COMMAND_SYNC_ON_BOOT`;
- `DANK_COMMAND_SYNC_STATE_FILE`;
- global command budget enforcement;
- configured stale guild-command copy cleanup.

`tests/test_command_runtime_native_ownership.py` already exercises fail-closed surface validation and unchanged-sync behavior.

## Scope

In scope:

- delete `startup_guards/slash_command_cleanup.py`;
- delete `startup_guards/ticket_panel_command_epoch_guard.py`;
- remove `slash_command_cleanup` from inert historical startup metadata;
- detach the dormant ticket-doctor compatibility loader from the retired epoch shim;
- update ticket-panel doctor audit/workflow references;
- replace the old guard-preservation regression with native command-owner/absence assertions;
- update command/startup/production-readiness ownership ledgers.

Out of scope:

- redesigning the public command UX;
- changing `DankCommandTree` behavior;
- changing normal command registration;
- removing the remaining ticket-doctor compatibility loader;
- migrating live interaction locks;
- central settings registry work;
- invite/protection/setup/design compatibility families.

## Changes

- removed global `CommandTree.sync` / `clear_commands` patcher;
- removed obsolete ticket-panel command epoch shim;
- historical startup metadata no longer advertises the cleanup guard;
- ticket-doctor compatibility loader no longer imports the epoch shim;
- ticket doctor audit requires both retired files to remain absent;
- ticket doctor workflow no longer tracks the deleted epoch file;
- `test_public_command_cleanup_contract_static.py` now verifies:
  - both retired files stay absent;
  - `DankCommandTree` owns sync policy;
  - canonical `PUBLIC_DANK_CHILDREN` remains `home/purge/setup/upload`;
  - runtime pruning remains disabled by default in the canonical command registrar;
- ownership ledgers now point to `command_runtime.DankCommandTree`.

## Expected production behavior

**No behavior change.**

Neither retired file has a production importer. The native command runtime already owns the required sync and surface policy.

The intended change is risk reduction: accidental imports can no longer reactivate process-wide Discord CommandTree mutation or revive a stale ticket-panel sync epoch.

## Validation required

- exact-head `git diff --check`;
- Python compile;
- `tests/test_public_command_cleanup_contract_static.py`;
- `tests/test_command_runtime_native_ownership.py`;
- `tools/audit_ticket_panel_doctor.py`;
- command/startup ownership tests;
- full Python suite;
- final changed-file/review-thread inspection;
- merge with expected-head guard;
- post-merge absence verification on `main`.

## Next step

Inspect the exact branch diff, open a focused draft PR, use Termux exact-head validation if GitHub runners remain unavailable, then merge and verify both retired command-cleanup files stay absent on `main`.
