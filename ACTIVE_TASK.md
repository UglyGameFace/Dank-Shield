# ACTIVE TASK

## Active task / desired outcome

**P0-AUTHORITY-RUNTIME-002 — restore live authority and component ownership across public controls**

Production evidence after PR #311 deployed shows that a fresh `/verify`
application-command interaction reaches Dank Shield and is acknowledged, but the
Verification Center returns:

`❌ Staff only. Server owners and configured staff are allowed.`

The user also reports that **Role Builder still does not work**. Repository
tracing shows both Role Builder doorways depend on the same central authority
truth:

- `/dank home` → **Roles & Profiles** uses
  `public_command_hub._admin_or_manage()`; a false result silently routes the
  server owner to the ordinary member profile entry instead of the staff builder;
- `/dank profile builder` and `/dank roles ...` use
  `public_setup_group._require_setup_permission()`, which delegates to the same
  `interaction_has_manage_guild_authority()` helper.

This is therefore the same authorization-truth failure for Role Builder access.

A newer live report says button interactions still show Discord's red
`Interaction failed` banner broadly. That symptom is not explained by authority
alone. PR #312 is still unmerged, and the earlier 4:04 PM screenshot happened
only about three minutes after PR #311 merged, so that screenshot did not prove
Discloud had completed the #311 redeploy.

Repository tracing also found a real partial-runtime defect: Profile/Role public
surfaces contain semantic raw buttons whose canonical business owner is the
module's global `on_interaction` listener. The tolerant command registrar could
fail that module and continue booting, leaving visible controls with no handler.
The older centralized verification/VC interaction listener was likewise allowed
to fail with only a warning.

## Single active task lock

Only this owner/admin authority task is active.

Do not reopen the completed component-lifecycle work unless new evidence points
back to it. Do not mix unrelated feature work into this branch.

## Previous task closed

PR #311 — production-wide Discord interaction reliability — merged to `main`
as `a67d94bc91e8380bb2b7758af52c0fcab0c376c9`.

Its exact PR head passed all workflow gates before merge.

## Production evidence

The 2026-09-24 4:04 PM Discord screenshot shows:

- `/verify` was accepted by Discord;
- Dank Shield sent an immediate ephemeral response;
- the response came from the staff/owner authorization gate in
  `public_verify_command_center._require_staff()`;
- therefore Gateway delivery, slash-command routing, and interaction
  acknowledgement were alive for this request.

The failed path is:

`/verify`
→ `public_verify_command_center._require_staff()`
→ `public_staff_scope.scoped_interaction_is_staff()`
→ owner/admin/configured-role authority resolution
→ false denial

## Root-cause class

The merged authority code correctly treated `guild.owner_id == user.id` as an
owner fast path, but tests assumed `guild.owner_id` was already populated.

For public application-command interactions that assumption is too narrow.
discord.py exposes additional authoritative runtime evidence:

- `guild.owner.id` when the owner object is available;
- `interaction.permissions`, Discord's resolved permissions for the invoking
  member in the interaction channel;
- cached `Member.guild_permissions` when a full Member object is available.

The old gate did not use interaction-resolved Administrator permission before
requiring a cached `discord.Member` for the fallback path.

## Repair

`public_owner_authority.py` is the single authority owner.

It now:

- resolves owner identity from `guild.owner_id`, falling back to
  `guild.owner.id`;
- accepts Discord-resolved `interaction.permissions.administrator` without
  requiring a cached Member object;
- accepts Discord-resolved `interaction.permissions.manage_guild` for public
  management surfaces;
- retains cached Member permission checks as a final compatible source;
- keeps non-owner/non-admin partial interactions fail-closed;
- exposes a small non-secret authority snapshot for production denial telemetry.

`public_staff_scope.scoped_interaction_is_staff()` now evaluates:

1. actual guild owner;
2. interaction-resolved Administrator;
3. existing configured staff-role policy.

`public_access_control.scoped_interaction_is_server_control()` uses the same
owner/Administrator truth before its existing role/bootstrap policy.

`public_verify_command_center._require_staff()` logs the authority snapshot only
when access is denied, so another live mismatch is diagnosable without guessing.

## Mandatory component ownership

The repair now treats interaction ownership as a boot contract:

- `install_profile_interaction_runtime(bot, strict=True)` runs before the
  tolerant split-command registrar;
- Profile Panel persistence and its canonical `on_interaction` listener must
  both register or startup fails closed;
- the shared component lifecycle runtime is critical and startup aborts if it
  cannot install;
- the centralized verification/VC interaction listener returns explicit
  readiness and `commands.py` fails closed if it cannot register;
- command registration may still tolerate unrelated optional UI/command errors,
  but it can no longer leave these known public component owners absent.

## Runtime deployment proof

A new host-independent runtime proof fingerprints the interaction-critical source
files and prefers a host-provided Git SHA when available. The component runtime
ready log now includes that proof. Public status reports include the same build
proof plus component ingress, stale-recovery, and unacknowledged counters.

This makes the next live acceptance diagnostic instead of inferential: we can
distinguish an old Discloud process from a current process that received a click
but failed to acknowledge it.

## Safety invariants

- no persisted database owner ID is trusted for live Discord authorization;
- no cross-guild environment role fallback is introduced;
- Manage Server is not promoted to ticket-staff authority;
- configured staff/control roles remain guild-scoped;
- non-owner partial interactions without resolved authority still fail closed;
- this is a central authority repair, not a `/verify`-only bypass.

## Tests

Coverage includes:

- both Role Builder doorways accept a resolved Administrator even without cached
  Member/owner state;
- the Roles & Profiles home route is locked to the central
  `_admin_or_manage()` authority contract before opening the builder;
- the direct `/dank profile builder` route is locked to
  `_require_setup_permission()`;
- owner identity with normal `guild.owner_id`;
- owner identity fallback through `guild.owner.id`;
- resolved Administrator with no cached owner/member state;
- resolved management authority through the shared public helper;
- non-owner partial interaction with no permissions fails closed;
- Verification Center accepts resolved Administrator without cached owner state;
- existing per-guild staff-role/config isolation tests remain required.

## Validation required

- exact branch/currentness inspection;
- Python compile;
- owner-authority focused tests;
- ticket staff-scope tests;
- full `pytest tests/`;
- standalone repository audits;
- all GitHub workflow gates;
- final diff/review/mergeability inspection.

## Status

**IN PROGRESS — authority + mandatory component ownership repair implemented; exact-head validation pending**

Branch: `fix/verify-owner-authority-runtime-20260924`

Base: `main@a67d94bc91e8380bb2b7758af52c0fcab0c376c9`

## Next step

Open the focused PR from current `main`, run the full exact-head validation
suite, repair only failures causally related to this authority task, and merge
only after every required gate is green.
