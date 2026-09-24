# ACTIVE TASK

## Active task / desired outcome

**P0-OWNER-AUTHORITY-002 — make actual guild ownership authoritative across public management gates**

Eliminate the class of false permission denials where the real Discord server
owner is rejected because an interaction arrives without fully resolved
`discord.Member` state or a populated permission/role cache.

## Production evidence

The actual server owner opened **Setup & Settings** from `/dank home` and Dank
Shield replied:

`❌ Server setup requires the configured server-control role or Administrator.`

The same owner had already hit a separate **Staff only** false denial in the
Verification Center.

PR #307 fixed the staff/Verification Center path. The Setup failure proved that
owner truth was still duplicated in a second permission implementation.

## Status

**IMPLEMENTED — final exact-head validation pending after top-level Design audit**

Branch: `fix/owner-authority-consolidation-20260924`

Base: `main@43e8d8a20d8e6455090bd60eedd92eb6f1851d28`

## Root cause

Public management authorization had multiple independent implementations.

The server-control path in
`commands_ext/public_access_control.py` performed this ordering:

1. require `isinstance(user, discord.Member)`;
2. only then check whether the user is the guild owner;
3. then check Administrator/configured server-control role.

An application-command interaction with partial member state could therefore
return False before the authoritative owner-ID comparison ever ran.

Several neighboring public UI helpers independently repeated
`Administrator or Manage Server` checks with the same Member-first assumption,
including the command hub used by `/dank home`.

PR #307 made staff authority owner-safe, but it did not consolidate these
server-control/manager helpers.

## Canonical authority contract

New module:

`stoney_verify/commands_ext/public_owner_authority.py`

owns the shared identity rule:

- `guild.owner_id == user.id` is authoritative first;
- owner truth does not depend on role cache, configured role IDs, or resolved
  `guild_permissions`;
- non-owner users still require real Discord Member state before
  Administrator/Manage Server permission checks;
- partial non-owner interactions fail closed.

## Changes

### `public_access_control.py`

- `scoped_is_server_control()` now checks actual guild ownership before the
  `discord.Member` type gate;
- `scoped_is_ticket_staff()` follows the same owner-first rule;
- added `scoped_interaction_is_server_control()` so interaction guild context
  remains available even when `interaction.user` is partial;
- `require_server_control()` now rejects only DMs before evaluating canonical
  owner/server-control authority.

This directly fixes the **Setup & Settings** false denial shown in production.

### Shared public management surfaces

The following manager gates now delegate to the same canonical
`interaction_has_manage_guild_authority()` helper:

- `public_command_hub._admin_or_manage`;
- `public_setup_group._admin_or_manage_guild`;
- `public_setup_overview._admin_or_manage_guild`;
- `public_diagnostics_group._admin_or_manage_guild`;
- `public_embed_group._admin_or_manage_guild`.

This covers the compact `/dank home` manager doorways such as Protection,
Logs, setup overview, diagnostics repair actions, and embed/setup tools without
duplicating owner logic again.

### Staff scope

`public_staff_scope.py` now reuses the canonical guild-owner identity helper
instead of maintaining a separate owner implementation.

`common._staff_check` also uses the canonical owner helper as its baseline
fast path.

### Server Design doorway

The top-level **Server Design** route deliberately uses **Manage Channels**
instead of Administrator/Manage Server. Its backend still checked
`isinstance(interaction.user, discord.Member)` before permission resolution,
which could reproduce the same false owner denial under partial interaction
state.

`public_design_studio._can_user_design()` now grants the actual guild owner
first through the canonical owner helper, while non-owner users still require
the existing **Manage Channels** permission. The Design permission model was not
broadened for anyone else.

## Security properties preserved

- no hardcoded user/guild IDs;
- configured server-control roles remain guild-scoped;
- configured ticket staff roles remain guild-scoped;
- Administrator remains valid;
- Manage Server remains only the bootstrap fallback when no control role is
  configured;
- partial non-owner interactions fail closed;
- ticket claim/security ownership is unchanged;
- no permission is granted merely because a username/role label resembles an
  owner/admin role.

## Tests

Added `tests/test_owner_authority_consolidation.py` covering:

- owner identity with no Member/permission/role state;
- non-owner partial state failing closed;
- server-control owner fast path before Member type checks;
- `require_server_control()` never denying the actual owner;
- shared public management gates all accepting the actual owner;
- the same gates rejecting partial non-owner state.

Existing PR #307 staff-scope regressions remain in place.

## Validation results

Exact implementation head `f35b78af68c09b4e90b8e746b7d9ac6e5307c707` passed:

- PR mergeable and 0 commits behind `main`;
- committed-diff whitespace check;
- Python compile;
- complete unit test suite;
- standalone tool checks;
- public setup/isolation audit;
- canonical public command-surface audit;
- startup-friction audit;
- public invite-permission audit;
- setup-safety audit;
- Dank Design Smart Auto-Detect audit;
- role-truth ownership audit;
- event-boundary ownership audit;
- focused claim-first ticket security suite;
- managed-category SQL smoke test;
- Ticket Owner Emergency Override workflow;
- Application Command Size Diagnostics;
- DS Backlog 027 Validation;
- Dank Design Regression CI;
- Profile Runtime Diagnostics.

Final diff inspection found no conflict markers, trailing whitespace, or debug
artifacts. PR review-thread inspection found no open review threads.

A follow-up audit of every top-level `/dank home` management doorway found one
remaining Member-first permission check in Server Design. That implementation
was corrected after the validation above, so those results are historical
evidence only; the new exact head must pass the complete gates again.

## Backlog

**Next P0 after this task:** Create Ticket interaction failure. Current code has
already exposed a concrete bookkeeping defect: when the persistent Create Ticket
view registers successfully, `public_ticket_panel_clean.py` sets
`_PANEL_FALLBACK_LISTENER_REGISTERED = True` without actually registering the
fallback listener. That task remains isolated until owner authority closes.

Startup activity-history reconciliation cost/noise remains a later performance
task.

## Next step

Run the complete exact-head CI after the Server Design audit repair. If all
workflows remain green, perform final currentness/diff/review inspection and
mark PR #309 ready for merge. After merge, activate the isolated Create Ticket
persistent-interaction repair.
