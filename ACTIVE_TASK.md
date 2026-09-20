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

**LOCKED — NOT IMPLEMENTED**

## Required behavior to preserve

- staff-only gate remains first business rule;
- existing Ticket Operations Center authorization remains authoritative before dispatch;
- claim stays exempt from the extra center authorization rule exactly as today;
- canonical `ticket_group.get_command(name)` lookup remains the dispatch source;
- commands that need extra positional information still receive the existing dedicated-flow error instead of being guessed;
- canonical callbacks continue to own actual ticket mutations and response content;
- component users still get a private result/failure message.

## Implementation rule

Use `stoney_verify.interaction_guard.run_guarded_interaction` as a thin wrapper around the existing runner body.

Prefer `defer=True` so the Ticket Operations Center interaction is acknowledged before ticket refresh/authorization I/O. Do not duplicate canonical ticket behavior. The existing canonical helpers are already response-done aware.

Unexpected failure guidance must not claim a live ticket definitely did or did not change after an exception. It should tell staff to refresh/reopen the Ticket Operations Center and inspect the ticket state before retrying, with the Error ID available in `/dank diagnostics`.

## Previous completed slice

**PR #273 — Guard setup-find config writes**

- merged as `f9307c4254ea1631cbf6e516a8197f6d329b89e0`;
- verified on `main`;
- validated setup source blob: `637a9b0455e0ce06a3ab6450c57d8dff3be2db6c`;
- validated regression-test blob: `b6d18674b9611872a70ec28a0a7b27458fad12da`;
- GitHub Actions again terminated before runner step execution with `steps: null` / `logs_url: null`.

## Next step

Inspect the exact ticket runner/test assumptions, then implement the smallest native-guard wrapper and focused regression test on a fresh implementation branch.
