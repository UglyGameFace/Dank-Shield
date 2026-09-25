# Active Task

## Active task / desired outcome

**P0-STAFF-DENIAL-BOUNDARY-009 — hide native Discord permission recipes from non-staff users**

Desired outcome: when a normal member clicks or invokes a Dank Shield staff-only
surface, the bot returns only **❌ Staff only.** Native Discord permission
requirements such as Manage Server, Manage Channels, or Administrator are shown
only after Dank Shield has already established that the caller is recognized
staff for that guild.

## Scope / single active task lock

Only staff-denial authority/UX is active:

- use the existing per-guild canonical staff/control truth;
- regular members must not learn staff-tool permission recipes from denial copy;
- server owner, Administrator, configured server-control roles, configured ticket
  staff roles, and configured VC staff roles remain recognized staff;
- native feature permissions remain a second gate and are still explained to
  recognized staff when genuinely missing;
- fix both current compact /dank home routes and legacy-compatible duplicate
  routes so behavior cannot diverge;
- fix shared setup and Server Design gates so direct commands/buttons match the
  same rule;
- audit other top-level staff management entry points that currently expose
  Manage Server/Administrator text before staff identity is established;
- add regression coverage to existing authority/staff test modules.

Do not mix in the separate Server Stats modal-to-buttons redesign until this
staff-denial task is merged/validated.

## Root cause

Several public management routes check Discord native permissions first:

- Server Design reports its Manage Channels requirement directly;
- Logs/Protection report Manage Server/Administrator directly;
- the canonical setup gate reports server-control / Manage Server requirements;
- Diagnostics and Embed Builder have the same ordering.

That means a normal Verified member sees an implementation/authorization recipe
instead of a staff-only denial. The native permission test is valid, but the
ordering is wrong.

The repo already has canonical per-guild staff/control truth:

- `scoped_is_ticket_staff(member)` recognizes owner/admin, configured ticket
  staff, VC staff, and configured server-control roles;
- `scoped_interaction_is_server_control(interaction)` remains the stronger setup
  mutation authority;
- Server Design still requires its native Manage Channels authority after staff
  identity is established.

## Required behavior

1. No guild context -> existing server-context error.
2. Not recognized staff -> exactly `❌ Staff only.`.
3. Recognized staff but missing the feature's native/control authority -> show
   the precise staff-facing permission/configuration guidance.
4. Recognized staff with required authority -> proceed unchanged.

## Status

**IMPLEMENTATION IN PROGRESS — root cause confirmed**

Branch: `fix/staff-only-denial-boundary-20260925`

Base: current `main` after merged PR #322.

## Validation required

Before merge:

- compile changed modules;
- focused staff/owner authority tests;
- direct Server Design denial tests;
- setup/server-control denial tests;
- compact and legacy /dank home route tests/static checks;
- diagnostics/embed management denial tests where applicable;
- full `pytest tests/`;
- all repository workflow groups green;
- verify ordinary member denial contains no Manage Server, Manage Channels,
  Administrator, role mention, or other escalation recipe;
- verify configured staff still receive actionable native-permission guidance;
- verify owner/admin/control-role behavior is unchanged.

## Backlog after this task

- Replace Server Stats modal-first counter customization with button/select-first
  section editing. Custom text modals should remain only as an escape hatch.

## Next step

Implement the canonical staff-first denial order in shared setup/control and
Server Design gates, then route duplicate home controls through those canonical
gates and validate the ordinary-member/staff distinction.
