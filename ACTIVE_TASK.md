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

**LOCKED — NOT IMPLEMENTED**

## Required behavior to preserve

- only the admin who opened the result view can use it;
- existing setup permission checks remain authoritative;
- missing/deleted/mismatched target objects are rejected before storage mutation;
- blockers prevent storage mutation;
- warnings remain visible after a valid save;
- the exact existing config payload is written;
- guild config cache invalidation and refreshed read still occur after a successful write;
- successful result continues to return the saved setup summary.

## Implementation rule

Use `stoney_verify.interaction_guard.run_guarded_interaction` (or an already-existing native helper that delegates to it) at the state-changing result Apply boundary.

Do not rewrite setup storage or validation. Prefer a thin wrapper around the existing Apply body, matching the low-churn pattern used for Dank Design in PR #271.

Failure guidance must not falsely claim a config write definitely did or did not occur after an unexpected exception. The user should be told to reopen `/dank setup` and verify the saved value before retrying, with the native Error ID available for diagnostics.

## Previous completed slice

**PR #271 — Guard Dank Design Apply and Undo interactions**

- merged as `6f7e4d60058d6c2806294721eba89b3f77640ba8`;
- verified on `main`;
- validated V2 blob: `82828185000d107084d0fc7731d6b378148abf60`;
- validated regression-test blob: `85d4a043007fee77b9cb70e93bcc83678fd2cff2`;
- full GitHub Actions remained unavailable because jobs terminated before step 1 with no steps/logs.

## Next step

Inspect the existing setup interaction helpers and exact `SetupSearchResultView.apply` call chain, then implement the smallest native-guard wrapper and focused regression test on a fresh implementation branch.
