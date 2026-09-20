# ACTIVE TASK

## Active task / desired outcome

**P0-INT-001 — Ticket Operations Center command runner native interaction guard**

Move the shared Ticket Operations Center command-dispatch boundary onto the native interaction service without changing canonical ticket authorization, lifecycle services, claim-first rules, or ticket command semantics.

## Why this is next

PR #273 completed the `/dank setup-find` config-writer slice and merged as `f9307c4254ea1631cbf6e516a8197f6d329b89e0`.

Post-merge verification on `main` confirmed the validated production and regression-test blobs exactly:

- `public_setup_find.py` → `637a9b0455e0ce06a3ab6450c57d8dff3be2db6c`
- `test_public_setup_find_native_interaction_static.py` → `b6d18674b9611872a70ec28a0a7b27458fad12da`

The next high-risk raw interaction boundary is `_run_ticket_command` in `stoney_verify/commands_ext/public_ticket_command_center.py`.

That function:

- is called by the Ticket Operations Center component UI;
- performs staff and claim/authorization checks;
- refreshes ticket state and can hit ticket authorization storage before the canonical command callback;
- directly dispatches canonical ticket commands such as claim, unclaim, lock, unlock, info, owner, and access;
- catches unexpected exceptions locally and only sends a plain error string;
- does not currently use `run_guarded_interaction()`, so unexpected failures do not get the native structured Error ID path.

The canonical ticket callbacks are compatible with an already-acknowledged interaction: `safe_defer` no-ops when the response is already done and `reply_once` sends a followup. That allows this shared runner to acknowledge before authorization/DB work without rewriting each ticket command.

## Scope

In scope:

- `_run_ticket_command` in `public_ticket_command_center.py`;
- native guarded acknowledgement before authorization/dispatch work;
- preserving the existing staff check and `authorize_ticket_action` policy;
- preserving canonical command lookup and direct callback dispatch;
- focused regression coverage for guard ownership, defer-before-authorization, and canonical dispatch preservation;
- P0 interaction ledger updates for this exact slice.

Out of scope:

- changing ticket authorization rules;
- changing claim-first policy;
- changing ticket lifecycle services;
- changing ticket close/reopen/delete modal flows;
- changing individual canonical `/ticket` command implementations in this slice;
- changing ticket storage/schema;
- verification or setup work;
- removing the global framework interaction monkey patch in this slice.

## Status

**IMPLEMENTED — targeted validation passed; pending exact-head PR/main verification**

## Required behavior preserved

- staff-only gate remains first business rule;
- existing Ticket Operations Center authorization remains authoritative before dispatch;
- claim remains exempt from the center's extra authorization rule exactly as before;
- canonical `ticket_group.get_command(name)` lookup remains the dispatch source;
- commands that need dedicated picker/modal information retain the existing `TypeError` business response;
- canonical callbacks continue to own live ticket mutations and user-facing success/failure content;
- component users still receive private responses.

## Implementation

`_run_ticket_command` is now a thin native interaction boundary using `run_guarded_interaction(..., defer=True)`.

The existing runner body moved to `_run_ticket_command_action` without changing staff checks, authorization ordering, canonical command lookup, or invocation arguments.

The previous broad `except Exception` string-flattening fallback was removed so truly unexpected failures reach the native structured Error ID path. The intentional `TypeError` dedicated-flow response remains.

Because the native guard acknowledges the interaction first, the existing canonical ticket helpers remain compatible:
- `safe_defer` already no-ops when the response is done;
- `reply_once` already sends a followup when the response is done.

Failure guidance is cautious because a canonical ticket action may have partially mutated state before an unexpected exception. Staff are told to refresh/reopen the Ticket Operations Center and inspect the current ticket state before retrying.

## Validation

Targeted branch validation passed:

- branch started from current `main` and remains 0 commits behind;
- production diff is 35 changed lines in `public_ticket_command_center.py`;
- exact ticket-runner region parses successfully with Python AST;
- the focused regression test parses successfully with Python AST;
- focused source replay confirms:
  - `run_guarded_interaction` owns the shared runner;
  - the guard defers before staff/authorization/dispatch work;
  - staff check still precedes ticket authorization;
  - authorization still precedes canonical command lookup;
  - canonical lookup still precedes `_invoke`;
  - the dedicated-flow `TypeError` branch remains;
  - the old broad `except Exception` swallow is absent;
  - `safe_defer` and `reply_once` remain compatible with a pre-deferred interaction.
- existing Ticket Operations Center surface tests do not pin the retired local exception shape.

Full repository pytest/Actions remains subject to the known runner/DNS infrastructure problem and must not be represented as passing unless a runner actually executes steps.

## Previous completed slice

**PR #273 — Guard setup-find config writes**

- merged as `f9307c4254ea1631cbf6e516a8197f6d329b89e0`;
- verified on `main`;
- validated setup source blob: `637a9b0455e0ce06a3ab6450c57d8dff3be2db6c`;
- validated regression-test blob: `b6d18674b9611872a70ec28a0a7b27458fad12da`;
- GitHub Actions again terminated before runner step execution with `steps: null` / `logs_url: null`.

## Next step

Open the focused PR, verify the exact final head and CI execution state, merge with an expected-head guard if the code evidence remains clean, verify the validated production/test blobs on `main`, then lock the next single P0 interaction boundary.
