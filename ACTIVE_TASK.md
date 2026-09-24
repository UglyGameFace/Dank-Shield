# ACTIVE TASK

## Active task / desired outcome

**P0-ACCESS-REPAIR-RUNTIME-004 — consolidate Dank Shield access repair and eliminate false startup-health failures**

Production screenshots after reinvite + role repositioning show that Dank Shield's
repair surfaces disagree with each other:

- Diagnostics → **Fix Channel Access** can deny the guild owner with
  `Manage Server, Manage Channels, or Administrator is required`;
- selected-target Fix Access can attempt a write and then return Discord
  `Forbidden`;
- Setup Check can say configuration passed while reporting several configured
  verification/ticket targets still missing bot access;
- diagnostics reports retired/lazy verification compatibility modules as
  missing startup owners even though current production boot intentionally does
  not load them as startup owners.

PR #313 is already merged and all exact-head workflows passed. Basic Verify
legacy-panel repair remains separate and is not being reopened here.

## Single active task lock

Only access-repair authority/capability ownership and startup-health truth are
active in this task.

Do not broaden into unrelated setup, ticket, verification, AntiNuke, or design
changes.

## Root causes

### 1. Repair authorization had a second local authority implementation

`permission_repair_core._actor_can_manage()` independently required a cached
`discord.Member` and read only cached guild permissions/owner ID.

That bypassed the canonical owner/interaction-permission authority introduced
for the rest of the public command surface. A valid owner/manager could pass the
Diagnostics doorway and then be rejected by the Fix Access subsystem.

### 2. Setup repair still used the wrong Discord permission prerequisite

The setup repair compatibility path treated **Manage Channels** as the
permission required to mutate channel permission overwrites.

discord.py `GuildChannel.set_permissions()` requires **Manage Roles**
(Discord's channel UI calls the equivalent channel capability
**Manage Permissions**).

This caused repair previews/results to disagree with the selected-target repair
and to give the wrong remediation instruction.

### 3. Self-lockout was presented as auto-repairable

If Dank Shield has Manage Roles at the server level but a category/channel
resolves **Manage Permissions** as denied for the bot, Discord will not permit
the bot to edit the overwrite that is blocking itself.

The UI still exposed green repair actions and retry/reauthorize guidance even
though that exact target required a manual Discord permission change first.

### 4. Startup diagnostics still expected retired/lazy compatibility guards

`startup_diagnostics.EXPECTED_STARTUP_OWNER_MODULES` still listed:

- `basic_verification_mode_guard`;
- `id_verify_allowlist_guard`;
- `unverified_ticket_panel_flow`.

Current `main.py`, `app.py`, and `commands.py` do not own those as
mandatory startup modules. The health report therefore emitted false warnings.

## Repair in progress

### Canonical actor authority

A shared
`interaction_has_channel_management_authority()` now recognizes:

- actual guild owner;
- Administrator;
- Manage Server;
- Manage Channels;

using Discord-resolved interaction permissions before cached Member state.

Fix Access delegates to that owner instead of maintaining a second permission
gate.

### Canonical overwrite capability

`permission_overwrite_edit_blocker()` is now the single capability check for
channel/category overwrite mutation.

It distinguishes:

1. missing server-level Manage Roles;
2. target-level Manage Permissions self-lockout;
3. a genuinely repairable target.

Selected-target and setup repair consume the same capability truth.

When a target is self-locked, the normal green Fix Missing Access action is
disabled and labeled **Manual Discord Fix Required** instead of repeatedly
issuing doomed writes.

### Setup repair compatibility

The still-referenced setup compatibility helper keeps its historical function
name for callers, but its implementation now checks Manage Roles / Manage
Permissions rather than Manage Channels.

Setup preview/apply uses the canonical overwrite capability and reports Discord
Forbidden results with the same explanation as selected-target repair.

### Setup Check truth

Setup Check no longer treats saved configuration completeness as sufficient to
show a green pass.

When the guided configuration is structurally complete but the configured
channel/category access audit is unhealthy:

- the title becomes **Configuration Saved — Access Needs Attention**;
- the description states that feature testing is blocked on access;
- the misleading guided-test / continue action is removed;
- the contextual access-repair/manual-fix control becomes the forward action.

This prevents the previous contradictory card that said Configuration Check
Passed while simultaneously listing unrepaired access targets.

### One generic repair doorway

Diagnostics now exposes **Repair Bot Access** and hands off to the same
`setup_permission_repair_services.open_permission_repair()` hub used by Setup.

The canonical generic flow is:

1. preview configured access;
2. apply safe fixes;
3. use **Specific Channel** only to inspect one blocked target.

Existing ticket/setup recovery guidance points to that same route.

### Undo truth

A repair that changes zero overwrites no longer creates or displays an undo
token. Failed/no-op attempts are still audit-recorded, but no restore snapshot
is manufactured. **Undo Repair** remains disabled until a real change produced
a valid token.

### Startup diagnostics

The startup-health contract now tracks actual native boot owners:

- process health;
- Discord API safety;
- command runtime;
- public env-ID isolation;
- shared interaction runtime;
- Basic Verify runtime;
- public ticket panel runtime;
- centralized interaction handlers;
- Profile/Role interaction runtime;
- authoritative activity tracker.

Retired/lazy verification compatibility modules are no longer false mandatory
startup owners.

## Safety invariants

- actual guild owners must never be denied because Member cache shape is partial;
- non-owner users without resolved management authority remain denied;
- no repair path claims Manage Channels is the permission required for
  `set_permissions()`;
- no target is advertised as auto-fixable when the bot cannot edit permission
  overwrites there;
- reauthorization remains non-Administrator;
- no unrelated member/staff visibility is modified;
- no dormant startup-guard bulk loader is restored;
- diagnostics remains read-only.

## Validation required

- canonical owner/Manage Channels interaction authority tests;
- server-level Manage Roles prerequisite tests;
- channel-level Manage Permissions self-lockout tests;
- setup repair regression tests;
- startup diagnostics owner-contract tests;
- Python compile;
- full test suite;
- all GitHub workflow gates;
- currentness / mergeability / review / diff hygiene.

## Status

**VALIDATING — implementation frozen; exact-head CI and merge hygiene pending**

Branch: `fix/canonical-access-repair-runtime-20260924`

Base: current `main` after merged PR #313.

## Production acceptance after deploy

1. guild owner can open Diagnostics → Fix Channel Access;
2. a repairable channel performs the write successfully;
3. a self-locked channel clearly says exactly which Discord permission must be
   manually restored and does not expose a fake green repair action;
4. Setup Check and Specific Channel show the same prerequisite truth;
5. diagnostics no longer reports retired/lazy verification guards as missing
   startup owners.
