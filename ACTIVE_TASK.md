# ACTIVE TASK

## DS-SEC-ROLE-UPDATE-SEVERITY — Stop cosmetic role edits from becoming destructive AntiNuke evidence

**Status:** FINAL EXACT-HEAD VALIDATION

**Branch:** `fix/antinuke-role-update-severity`
**Base main:** `f40f8cd0b6f66dae0f622564c3a1fb7511316bbb`
**Validated implementation head:** `5605cc0914b653468aa2a101534e15baf20e9216`

## Previous task closed

PR #257 (`DS-SEC-GUILD-UPDATE-SEVERITY`) merged as `f40f8cd0b6f66dae0f622564c3a1fb7511316bbb`, became `main`, contains exact validated PR head `4ebbd1d99ff14af62c0aa5387e7623f138d178cc` as its second parent, and `discloud/commit` reported success.

The Single Active Task Lock is this role-update severity normalization only.

## Root cause

Guardian generic `role_update` classification mixed authority/hierarchy changes with routine presentation edits.

The old guardian classified all of these as destructive role-update evidence:

- dangerous permission changes
- role hierarchy position changes
- role name changes
- hoist changes
- mentionable changes
- color/colour changes
- icon changes
- unicode emoji changes

That disagreed with both the native AntiNuke listener, which only treats dangerous role permission mutations as destructive evidence, and the owner policy, which already treats role presentation edits as benign.

## Implemented behavior

Role updates now use severity-aware classification:

- **routine / ignored:** name, hoist, mentionable, colour/color, icon, unicode emoji
- **bounded security:** dangerous permission mutation/removal without permission addition
- **bounded hierarchy:** role position change
- **immediate escalation:** dangerous permission addition, still owned by the gateway-fast escalation path

Additional hardening:

- cosmetic role edits now contribute panic weight 0
- bounded permission/hierarchy mutations contribute panic weight 2
- dangerous permission additions retain immediate panic weight 4 when classified with full entry context
- guardian routine role fields are regression-locked to incident runtime's authoritative owner routine role fields
- audit-entry consumption remains after classification, so ignored cosmetic role edits are not prematurely consumed
- sparse recovery behavior remains unchanged

## Preserved behavior

- dangerous role permission additions still use gateway rollback/containment
- dangerous permission removals/mutations remain monitored
- role hierarchy changes remain monitored
- owner severity behavior from PR #253 remains unchanged
- guild-update severity normalization from PR #257 remains unchanged
- Strict Lockdown and fail-closed fallback behavior remain unchanged

## Exact implementation-head validation

Exact head `5605cc0914b653468aa2a101534e15baf20e9216` passed the complete workflow set:

- Dank Shield CI #2294: **success**
  - committed diff whitespace: **success**
  - Python compile: **success**
  - full unit suite: **success**
  - standalone tool checks: **success**
  - public setup/isolation audit: **success**
  - canonical public command surface audit: **success**
  - public command/startup friction audit: **success**
  - public invite permissions audit: **success**
  - setup safety audit: **success**
  - Dank Design Smart Auto-Detect audit: **success**
  - role truth ownership audit: **success**
  - event boundary ownership audit: **success**
  - Claim-first ticket security: **success**
  - Managed category SQL smoke test: **success**
- Application Command Size Diagnostics #1276: **success**
- Dank Design Regression CI #518: **success**
- Ticket Owner Emergency Override #865: **success**
- Profile Runtime Diagnostics #1025: **success**

## Final diff / review audit

At implementation head `5605cc0914b653468aa2a101534e15baf20e9216`:

- branch is 0 commits behind current `main` `f40f8cd0b6f66dae0f622564c3a1fb7511316bbb`
- PR is mergeable
- exactly 4 task-related files are changed:
  - `ACTIVE_TASK.md`
  - `stoney_verify/anti_nuke_guardian_runtime.py`
  - `tests/test_antinuke_competitive_hardening.py`
  - `tests/test_antinuke_owner_policy_normalization.py`
- no unresolved review blockers are present

## Final exact-head gate

This bookkeeping commit changes the PR head SHA. The new final head must independently pass the complete required and companion workflow set before merge.

After the final wave:

1. confirm the branch remains 0 behind current `main` with the same 4 files
2. confirm no review blocker appeared
3. mark PR #258 ready
4. merge using the exact validated final SHA
5. verify the resulting `main` merge commit has that exact PR head as a parent
6. require `discloud/commit: success`
7. release the Single Active Task Lock

## Next step

Run the final exact-head workflow wave created by this bookkeeping commit, then merge PR #258 only if every gate remains green.
