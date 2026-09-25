# Active Task

## Active task / desired outcome

**P0-ACCESS-REPAIR-SELF-LOCKOUT-007 — make Repair Bot Access follow Discord's documented permission model and recover already-locked channels safely**

Desired outcome: Diagnostics / Repair Bot Access must distinguish between
repairable drift and a true Discord MANAGE_ROLES self-lockout. It must never
pretend an API route can bypass Discord's permission checks. When recovery
authority is explicitly granted, it should repair the affected Dank Shield
overwrites in one scoped pass without rewriting unrelated member/staff access.

## Scope / single active task lock

Only the access-repair self-lockout path is active:

- verify the Discord permission model against current official Discord docs;
- remove invalid assumptions introduced by PR #319;
- keep parent-category bot overwrite seeding only where Discord actually permits
  overwrite edits;
- preserve normal public installation as non-Administrator;
- provide a clearly separated emergency recovery path for already-locked
  channels, with Administrator treated as temporary and explicit;
- keep AntiNuke from treating Discord's own managed Dank Shield role
  authorization update as a hostile third-party role escalation;
- preserve all unrelated role/member overwrites;
- validate focused and full regression coverage before merge.

Do not broaden into verification interaction failures, ticket redesign, general
AntiNuke redesign, or test-suite consolidation.

## Status

**IMPLEMENTATION COMPLETE FOR REPOSITORY VALIDATION — exact-head CI running; live Discord OAuth acceptance remains required**

Branch: `fix/access-repair-emergency-recovery-20260925`

Draft PR: **#321 — Fix Discord access repair self-lockout recovery**

Base: current `main` after merged PR #319. Branch was confirmed current with `main` before final validation.

## Authoritative Discord documentation findings

Checked against the current official Discord Developer Documentation on
2026-09-25.

1. **Edit Channel Permissions requires MANAGE_ROLES.**
   Discord's Channel Resource says the permission-overwrite endpoint requires
   `MANAGE_ROLES`.
2. **Modify Channel does not bypass that requirement.**
   Discord says `MANAGE_CHANNELS` is required to modify a guild channel, but
   if the request modifies permission overwrites, `MANAGE_ROLES` is also
   required.
3. **Syncing a child back to its category also does not bypass it.**
   Discord's guild channel-position endpoint says `lock_permissions` requires
   `MANAGE_ROLES`.
4. **Administrator is the documented overwrite bypass.**
   Discord's permission table says `ADMINISTRATOR` allows all permissions and
   bypasses channel permission overwrites. Discord's permission-computation
   pseudocode returns all permissions before applying channel overwrites when
   Administrator is present.
5. **Unsynced child channels do not inherit later category changes.**
   Discord documents category behavior as permission syncing, not live
   inheritance. Once a child is desynced, changes to its parent category no
   longer update that child.
6. **Normal channel creation must not manufacture MANAGE_ROLES overwrites.**
   Discord's Create Guild Channel endpoint explicitly says setting
   `MANAGE_ROLES` in channel permission overwrites is only possible for guild
   administrators.
7. **Bot OAuth authorization can request a permission bitfield and existing
   authorizations can be re-approved.**
   Discord documents the `permissions` parameter in the bot authorization
   flow, and its OAuth documentation says existing authorizations can be
   re-approved. For passthrough `bot` scope, authorization is always required.
   However, the docs do **not** explicitly promise the exact mutation behavior
   of reauthorizing a bot that is already installed in the same guild. That
   specific existing-install recovery behavior remains a required live
   acceptance test and must not be presented as already proven.

Official sources:

- https://docs.discord.com/developers/topics/permissions
- https://docs.discord.com/developers/resources/channel
- https://docs.discord.com/developers/resources/guild
- https://docs.discord.com/developers/topics/oauth2

## Root cause

The production screenshots show Dank Shield has server-level Manage Roles but
some child channels/categories deny or otherwise remove effective
Manage Permissions (Discord's client label for MANAGE_ROLES).

For those targets:

- `set_permissions` cannot repair the overwrite because Discord requires
  effective MANAGE_ROLES;
- `channel.edit(permission_overwrites=...)` cannot bypass it because Discord
  also requires MANAGE_ROLES whenever Modify Channel changes overwrites;
- `Sync Now` / `lock_permissions` cannot bypass it because Discord also
  requires MANAGE_ROLES for permission syncing;
- a correct parent-category overwrite is useful as a repair template, but an
  already-desynced child does not automatically inherit it.

Therefore a target that has already removed Dank Shield's effective
MANAGE_ROLES is a real platform-enforced self-lockout, not merely a library
guard.

## Changes in progress

- Removed the invalid idea that `MANAGE_CHANNELS` can be used as an overwrite
  repair bypass.
- Removed normal `manage_roles=True` channel/category baseline grants added by
  #319; Discord documents those grants as Administrator-only during channel
  creation.
- Normal repair preserves any existing explicit Dank Shield Manage Permissions
  value instead of manufacturing or clearing it.
- Parent-category seeding remains bot-only and preserves unrelated overwrites,
  but normal repair excludes MANAGE_ROLES from the copied template.
- Added an underlying-permission resolver for emergency recovery so temporary
  Administrator does not hide which channel overwrites are still broken.
- Emergency recovery is isolated from normal installation. Its purpose is to
  obtain documented Administrator overwrite bypass authority long enough to
  repair Dank Shield's own affected overwrites, then explicitly remove
  Administrator again.
- AntiNuke guards are being added so Discord's own managed Dank Shield role
  authorization changes are not treated as hostile third-party escalation.
- No full `sync_permissions=True` category sync is introduced.

## Important unvalidated assumption / blocker

Discord documents that bot authorization requests a permission bitfield and
that existing authorizations can be re-approved, but its public docs do not
explicitly state the exact managed-role mutation behavior when the same bot is
already installed in the target guild.

Therefore:

- the emergency OAuth recovery link may be implemented and regression-tested as
  a request generator;
- it must **not** be claimed production-working until live Discord acceptance
  proves the existing installation receives Administrator as requested;
- if Discord does not update the existing managed bot role through that flow,
  the fallback remains owner/admin intervention in Discord, because the API
  provides no documented self-bypass for an already-locked target.

## Validation required / current evidence

A superseded PR-head run reached **1997 passed / 3 failed / 9 warnings**. The
three failures were regression-test expectation/fixture issues found while the
implementation was still moving:

- an obsolete #319 assertion still expected normal parent seeding to copy
  `manage_roles=True`; the documented model now correctly expects it to remain
  unchanged during normal repair;
- one recovery-label assertion expected a display name while the canonical UI
  helper returns a channel mention;
- one activity-scope test omitted the required `manual_actions` accumulator.

All three test issues were corrected before the final-head run.

Before any merge, the **final exact head** must still prove:

- committed diff whitespace passes;
- all changed Python modules compile;
- focused access-repair tests pass;
- focused AntiNuke managed-role tests pass;
- full `pytest tests/` passes;
- all standalone repository audits pass;
- public invite audit proves normal installation still excludes Administrator;
- all GitHub workflow groups are green;
- no normal setup baseline contains `manage_roles=True` channel overwrites;
- normal setup/repair preserves an existing explicit Dank Shield Manage
  Permissions allow or deny instead of silently changing it;
- temporary-Administrator recovery may resolve only Dank Shield's own required
  access denies and does not mutate unrelated role/member overwrites;
- temporary Administrator remains visible in the UI until normal permissions
  are restored;
- final diff contains no unrelated, generated, secret-bearing, debug, or
  conflict-artifact changes.

Repository CI cannot prove Discord's undocumented behavior for reauthorizing an
already-installed bot. That remains the production acceptance gate after the
repository head is green.

## Cleanup / conflicts

- PR #319 is merged and its repository CI passed, but its live acceptance
  exposed this remaining platform constraint.
- PR #320 was closed unmerged because the task was not actually complete.
- No new test file is being created for this fix; regression coverage is being
  added to existing subsystem test modules.

## Backlog

- Test-suite organization/consolidation remains separate.
- Verification/ticket interaction failures remain separate unless this exact
  permission root cause directly controls them.

## Next step

Freeze runtime changes and run the full exact-head PR #321 validation suite.
Fix only failures that are part of this access-repair root cause. If every
repository gate passes, keep the PR draft until the live Discord acceptance
sequence proves whether reauthorizing the already-installed bot actually
updates its managed integration role as requested.

Production acceptance sequence:

1. Deploy the exact validated PR head.
2. Open Diagnostics → Repair Bot Access on the known affected server.
3. Confirm already self-locked targets show **Temporary Admin Recovery** rather
   than a fake safe-fix button.
4. Authorize the guild-pinned temporary recovery once.
5. Press **Preview Again** and confirm the underlying  activity/access gaps are
   still listed despite Administrator being active.
6. Run **Fix All Safe Access** and confirm only Dank Shield's own target
   overwrites change.
7. Use **Restore Normal Permissions**, then Preview Again.
8. Confirm Administrator is gone, the repaired targets remain accessible, and
   unrelated member/staff overwrites are unchanged.

If Discord does not update the existing managed bot role in steps 4 or 7, do
not claim the OAuth recovery path works; retain the documented manual
Administrator fallback instead.
