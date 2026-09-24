# ACTIVE TASK

## Active task / desired outcome

**P0-AUTHORITY-RUNTIME-002 — stop legitimate owner/admin interactions from being denied by public management gates**

Production evidence after PR #311 deployed shows that a fresh `/verify`
application-command interaction reaches Dank Shield and is acknowledged, but the
Verification Center returns:

`❌ Staff only. Server owners and configured staff are allowed.`

This is an authorization-truth failure, not an interaction-delivery failure.

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

## Safety invariants

- no persisted database owner ID is trusted for live Discord authorization;
- no cross-guild environment role fallback is introduced;
- Manage Server is not promoted to ticket-staff authority;
- configured staff/control roles remain guild-scoped;
- non-owner partial interactions without resolved authority still fail closed;
- this is a central authority repair, not a `/verify`-only bypass.

## Tests

Coverage includes:

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

**IN PROGRESS — central repair implemented; validation pending**

Branch: `fix/verify-owner-authority-runtime-20260924`

Base: `main@a67d94bc91e8380bb2b7758af52c0fcab0c376c9`

## Next step

Open the focused PR from current `main`, run the full exact-head validation
suite, repair only failures causally related to this authority task, and merge
only after every required gate is green.
