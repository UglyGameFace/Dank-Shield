# ACTIVE TASK

## Active task / desired outcome

**P0-INT-001 — Verification Center canonical command dispatcher native interaction guard**

Move the Verification Center's shared canonical-command dispatcher onto the native interaction service so its role/member mutations get structured Error IDs without rewriting the canonical `/verify` command implementations.

## Why this is next

PR #275 completed the Ticket Operations Center command-runner slice and merged as `e58bbf0c566b07bd72412d12a566d6d4e8217dc8`.

Post-merge verification on `main` confirmed the validated production and regression-test blobs exactly:

- `public_ticket_command_center.py` → `d0abd4e3dca0a4a38638fd1e8018f9d6574cbccd`
- `test_public_ticket_command_center_native_interaction_static.py` → `e17d2fda8bdb68bcadb113bcc0b2fca38bb28191`

The next high-value raw interaction boundary is `_invoke` in `stoney_verify/commands_ext/public_verify_command_center.py`.

That dispatcher is used by Verification Center actions including:

- server-wide Pending/Unverified repair;
- member Status and Diagnose;
- Grant Verified + Member;
- Restore Pending / Pending + Clear Conflicts;
- add/remove Verified;
- add/remove Resident.

The dispatcher currently calls canonical command callbacks directly with no `run_guarded_interaction()` boundary.

Current evidence also shows:

- none of the Verification Center callers depend on an `_invoke` return value;
- canonical `public_verify_group._ack()` checks `interaction.response.is_done()` before deferring, so it is compatible with a pre-deferred interaction;
- the canonical `/verify` functions remain the owners of role hierarchy checks, role creation/discovery, config mapping, member role mutations, and their own normal response content.

## Scope

In scope:

- `_invoke` in `public_verify_command_center.py`;
- native defer-before-canonical-command dispatch;
- stable action naming derived from the canonical callback name;
- preserving callable validation and exact callback arguments;
- focused regression coverage proving guarded dispatch and pre-defer compatibility;
- P0 interaction ledger updates for this exact slice.

Out of scope:

- changing canonical `/verify` command logic;
- changing role hierarchy or role discovery rules;
- changing Verification Center navigation;
- changing direct Verification Role Mapping config writes in this slice;
- changing Verify panel posting or setup routing;
- ticket/setup/design work;
- removing the global framework interaction monkey patch in this slice.

## Status

**LOCKED — NOT IMPLEMENTED**

## Required behavior to preserve

- non-callable canonical actions still fail clearly;
- the exact canonical callback still receives the original interaction, positional args, and kwargs;
- canonical `/verify` commands remain authoritative for staff checks, role checks, role mutations, repair behavior, and normal messages;
- existing center button flows continue to work with no caller return-value dependency;
- mutations are acknowledged before slower canonical role/config work.

## Implementation rule

Use `stoney_verify.interaction_guard.run_guarded_interaction` as a thin wrapper inside `_invoke`.

Prefer `defer=True`; the canonical `_ack()` helper is response-done aware. Derive a stable action name from the callback function name, such as `verify.center.verify_grant_vr`.

Failure guidance must be cautious because a canonical verification command may have partially changed roles before an unexpected exception. Tell staff to reopen the Verification Center and inspect the member/server verification state before retrying, with the native Error ID available in `/dank diagnostics`.

## Previous completed slice

**PR #275 — Guard Ticket Operations Center command runner**

- merged as `e58bbf0c566b07bd72412d12a566d6d4e8217dc8`;
- verified on `main`;
- validated ticket-center blob: `d0abd4e3dca0a4a38638fd1e8018f9d6574cbccd`;
- validated regression-test blob: `e17d2fda8bdb68bcadb113bcc0b2fca38bb28191`;
- GitHub Actions again terminated before runner step execution with `steps: null` / `logs_url: null`.

## Next step

Implement the smallest guarded `_invoke` dispatcher and focused regression test on a fresh branch, then validate the exact head before merge.
