# ACTIVE TASK

## Active task / desired outcome

**P0-INT-001 — `/dank setup-find` result Apply native interaction guard**

Move the state-changing setup-search result Apply boundary onto the native interaction service without changing setup validation, permission ownership, object resolution, or guild-config write semantics.

## Why this is next

PR #271 completed and merged the reviewed Dank Design Apply / Undo mutation guard slice. Post-merge verification on `main` confirmed the validated production and regression-test blobs exactly.

The next concrete high-risk raw interaction boundary is `SetupSearchResultView.apply` in `stoney_verify/commands_ext/public_setup_find.py`.

That callback:

- is reached from a Discord component selection;
- resolves and validates a selected role/channel;
- directly calls `upsert_guild_config(...)`;
- invalidates and refreshes guild configuration;
- edits the interaction only after the storage mutation;
- is not currently wrapped by `run_guarded_interaction()`.

If an unexpected callback/storage/response exception occurs around that write, the current flow can fall back to generic component failure behavior without the native structured Error ID path.

## Scope

In scope:

- `SetupSearchResultView.apply`;
- the immediate setup-search result callback/error responses needed to keep that mutation boundary safely acknowledged;
- use of the existing native interaction service and safe response helpers;
- focused regression coverage proving the storage mutation remains inside the guarded action;
- update the P0 interaction ledger for this exact slice.

Out of scope:

- changing setup target definitions;
- changing setup permission policy;
- changing object matching/validation rules;
- changing `upsert_guild_config` payload semantics;
- redesigning `/dank setup`, setup recommendation, setup recovery, tickets, verification, or unrelated selectors;
- removing the global framework interaction monkey patch in this slice.

## Status

**IMPLEMENTED — targeted validation passed; pending exact-head PR/main verification**

## Required behavior preserved

- only the admin who opened the result view can use it;
- existing setup permission checks remain authoritative;
- missing/deleted/mismatched target objects are rejected before storage mutation;
- blockers prevent storage mutation;
- warnings remain visible after a valid save;
- the exact existing `_payload_for(interaction, self.spec, obj)` config payload is written;
- guild config cache invalidation and refreshed read still occur after a successful write;
- successful result still returns the saved setup summary.

## Implementation

`SetupSearchResultView.apply` now delegates the existing result logic through `run_guarded_interaction(..., defer=True)`.

The config-write body remains in `_apply_result` so the mature validation/storage order is not rewritten. Because the guard acknowledges the component before storage I/O, expired/rejected/success result updates now use `interaction.edit_original_response(...)`.

Unexpected failures use cautious guidance: reopen `/dank setup`, verify the currently saved value before retrying, and use the native Error ID in `/dank diagnostics`. The message does not claim that a partially completed config write definitely did or did not happen.

## Validation

Targeted branch validation passed:

- branch started from current `main` and remains 0 commits behind;
- production diff is 34 changed lines in `public_setup_find.py`;
- exact `SetupSearchResultView` region parses successfully with Python AST;
- the new focused regression test parses successfully with Python AST;
- focused source replay confirms:
  - `run_guarded_interaction` owns the Apply callback;
  - the guard defers before the mutation body;
  - `_resolve_object` and `_validate_object` still run before `upsert_guild_config`;
  - `upsert_guild_config` still precedes cache invalidation, refreshed config read, and success embed construction;
  - post-defer result paths use `edit_original_response`, not `interaction.response.edit_message`;
  - the no-guild response uses `safe_send_interaction`;
  - blockers, warnings, exact payload semantics, and setup verification footer remain present.
- repository search found no existing setup test that requires the retired raw `interaction.response.edit_message` shape for this callback.

Full repository pytest/Actions remains subject to the known runner/DNS infrastructure problem and must not be represented as passing unless a runner actually executes steps.

## Previous completed slice

**PR #271 — Guard Dank Design Apply and Undo interactions**

- merged as `6f7e4d60058d6c2806294721eba89b3f77640ba8`;
- verified on `main`;
- validated V2 blob: `82828185000d107084d0fc7731d6b378148abf60`;
- validated regression-test blob: `85d4a043007fee77b9cb70e93bcc83678fd2cff2`;
- full GitHub Actions remained unavailable because jobs terminated before step 1 with no steps/logs.

## Next step

Open the focused PR, verify the exact final head and CI execution state, merge with an expected-head guard if the code evidence remains clean, verify the validated production/test blobs on `main`, then lock the next single P0 interaction boundary.
