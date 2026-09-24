# ACTIVE TASK

## Active task / desired outcome

**P0-OWNER-STAFF-001 — server owner must never be denied by staff gates**

Repair the permission path that returned **❌ Staff only.** to the actual Discord
server owner when opening the compact `/verify` Verification Center.

## Production symptom

The server owner invoked `/verify` in-guild and Dank Shield replied:

`❌ Staff only.`

That is invalid behavior. The Discord server owner is always authoritative for
server-management surfaces and must not depend on configured staff-role state.

## Status

**ROOT CAUSE IDENTIFIED — repair implemented; validation pending**

Branch: `fix/server-owner-staff-authority-20260924`

Base: `main@f7b20ab3bde020493698698c8303709d61660cf0`

## Root cause

Dank Shield's public staff isolation layer treated these as staff:

- members whose resolved `guild_permissions.administrator` is true;
- members holding a staff/VC-staff role configured for that guild.

It did **not** explicitly grant the actual Discord guild owner before those
permission/role checks.

That was unnecessarily brittle. In normal cached `discord.Member` state,
discord.py derives all guild permissions for the owner, but the product contract
must not make owner authority depend on that derived permission object or on a
fully populated member/role cache.

The compact Verification Center then gated `/verify` through the shared staff
checker and surfaced the false negative as **Staff only.**

There was a second architectural weakness: the shared staff helper is patched at
startup. Modules that copy a helper by value can retain an older function
reference. Owner authority therefore needs to be true in both the canonical
per-guild scope and the baseline shared helper rather than relying on monkey-patch
timing.

## Changes

### `stoney_verify/commands_ext/public_staff_scope.py`

- added authoritative guild-owner identity check using
  `interaction.guild.owner_id == interaction.user.id`;
- owner check happens before Discord member-type, permission, staff-role, or
  config-cache requirements;
- added `scoped_interaction_is_staff()` so interaction-aware permission checks
  retain guild context even if member state is partial;
- shared `common._staff_check` patch now binds that interaction-aware helper
  instead of discarding guild context.

### `stoney_verify/commands_ext/common.py`

- baseline shared `_staff_check()` now grants the actual guild owner directly;
- then delegates to the canonical per-guild staff scope;
- retains the legacy fallback only if the canonical helper is unavailable.

This makes already-imported references owner-safe instead of depending on later
function rebinding.

### `stoney_verify/commands_ext/public_verify_command_center.py`

- Verification Center now imports and calls the canonical
  `scoped_interaction_is_staff()` helper directly;
- denial copy now clarifies that server owners and configured staff are allowed.

### `stoney_verify/globals.py`

- legacy `is_staff()` now explicitly recognizes the actual guild owner before
  administrator/env-role checks so stale imported references cannot deny owners.

### Tests

`tests/test_ticket_staff_scope_runtime.py` now verifies:

- guild owner succeeds even without role/config/permission resolution;
- non-owner partial user state still fails closed;
- shared `common._staff_check` recognizes the owner;
- Verification Center specifically accepts the owner;
- canonical common binding uses the interaction-aware staff helper.

## Scope / compatibility

Preserved:

- per-guild configured staff-role isolation;
- administrator access;
- cold-cache fail-closed behavior for non-owners;
- ticket claim/security wrappers;
- no beta guild role leakage;
- no hardcoded user/guild IDs.

Not changed:

- Basic Verify green-button role mutation;
- verification-mode policy;
- AntiNuke;
- Server Stats;
- Exit Card rendering.

## Validation required

- Python compile;
- focused owner/staff tests;
- ticket staff-scope and claim-first regressions;
- Verification Center tests;
- full `pytest tests/`;
- standalone repository audits;
- GitHub workflow gates;
- final diff/currentness/review-thread inspection.

## Blockers / risks

The screenshot proves a false owner denial but does not by itself prove whether
the live interaction arrived with partial member permissions or hit a copied
pre-patch helper. The repair deliberately removes both failure modes by making
owner identity authoritative at each shared boundary.

## Backlog

- Basic Verify green-button acceptance still needs live confirmation separately.
  The `/verify` Staff-only response was a different failure path.

## Next step

Open an isolated draft PR, run exact-head CI and focused owner/staff regressions,
then make it merge-ready only after the full repository gates pass.
