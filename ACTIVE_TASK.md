# ACTIVE TASK

## DS-SEC-ROLE-UPDATE-SEVERITY — Stop cosmetic role edits from becoming destructive AntiNuke evidence

**Status:** INVESTIGATION / IMPLEMENTATION

**Branch:** `fix/antinuke-role-update-severity`
**Base main:** `f40f8cd0b6f66dae0f622564c3a1fb7511316bbb`

## Previous task closed

PR #257 (`DS-SEC-GUILD-UPDATE-SEVERITY`) merged as `f40f8cd0b6f66dae0f622564c3a1fb7511316bbb`, became `main`, contains exact validated PR head `4ebbd1d99ff14af62c0aa5387e7623f138d178cc` as its second parent, and `discloud/commit` reported success.

The Single Active Task Lock is this role-update severity normalization only.

## Problem

Generic role updates still use a broader destructive classifier than the canonical native and owner-policy paths.

The current guardian treats all of these as destructive `role_update` evidence:

- dangerous permission changes
- role hierarchy position changes
- role name changes
- hoist changes
- mentionable changes
- color/colour changes
- icon changes
- unicode emoji changes

That means ordinary role presentation edits can enter AntiNuke counters for non-owner staff even though:

- native `anti_nuke_on_guild_role_update()` only treats dangerous permission changes as destructive evidence
- the owner policy already classifies role name/hoist/mentionable/color/icon/emoji edits as benign
- dangerous permission additions already have a dedicated gateway-fast escalation path

## Root cause

`anti_nuke_guardian_runtime._generic_role_update()` mixes authority/hierarchy changes with cosmetic role settings. `_role_update_panic_weight()` also gives cosmetic updates a nonzero panic weight and assigns the same high weight to dangerous permission removals and hierarchy moves as to more acute authority escalation.

The policies therefore disagree across owner, native, gateway, and guardian paths.

## Intended behavior

- cosmetic role edits are ignored by destructive AntiNuke processing
- dangerous permission additions remain on the dedicated gateway escalation path
- dangerous permission removals/mutations remain bounded destructive evidence
- role hierarchy position changes remain bounded destructive evidence
- cosmetic role edits contribute zero panic weight
- bounded role security/hierarchy changes contribute bounded panic weight
- owner severity behavior from PR #253 remains unchanged
- sparse dangerous authority recovery remains intact

## Implementation scope

Normalize guardian role-update classification to the existing severity model:

- routine/benign: name, hoist, mentionable, colour/color, icon, unicode emoji
- bounded security: dangerous permission change without addition
- bounded hierarchy: role position change
- immediate escalation: dangerous permission addition, still owned by gateway

Add regression coverage locking guardian routine role fields to the owner-policy routine role fields so these classifiers cannot drift apart again.

## Validation / merge gate

Before merge:

- cosmetic role rename/color/hoist/etc. are ignored by guardian destructive processing
- dangerous permission removal remains processed
- role position change remains processed
- dangerous permission addition still routes through gateway escalation
- role panic weights match severity
- owner normalization and guild-update severity regressions remain green
- full Python compile, full unit suite, standalone/security/event-boundary audits pass
- Claim-first ticket security and Managed category SQL smoke pass
- all companion workflows pass on exact final head
- branch is 0 behind current `main`
- final diff contains only task-related files
- no unresolved review blockers
- merge only exact validated SHA
- verify resulting `main` merge parent and require `discloud/commit: success`

## Next step

Implement the smallest guardian classifier/panic-weight correction and focused regressions.
