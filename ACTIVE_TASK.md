# ACTIVE TASK

## Active task / desired outcome

**P0-INT-001 — Verification Center role-mapping save integrity + native interaction guard**

Make the Verification Center's direct Role Mapping select both natively guarded and truthful about persistence. A failed guild-config write or unverifiable read must never be followed by a false “now uses this role” success message.

## Scope

In scope:

- `VerifyRoleSelect.callback` and its focused save path;
- native defer-before-save acknowledgement;
- existing owner/staff and role hierarchy checks;
- existing logical-role → config-key mapping;
- explicit-override `_save_role_config(..., explicit_override=True)`;
- authoritative post-save verification before success;
- focused regression coverage;
- P0 readiness ledger updates.

Out of scope:

- changing the broad semantics of `_save_role_config` for discovery/auto-create callers;
- canonical `/verify` behavior;
- member role mutations;
- Verify panel/setup routing;
- ticket/setup/design work;
- removal of the global framework interaction monkey patch.

## Status

**IMPLEMENTED — PR #279 open as draft; targeted source validation passed; full CI validation BLOCKED because configured GitHub Actions produced no runs**

## Findings / root cause

- `VerifyRoleSelect.callback` called `_save_role_config(..., explicit_override=True)` and then unconditionally sent a success message.
- `_save_role_config` intentionally swallows persistence exceptions for compatibility with best-effort discovery callers.
- Therefore the callback could report success after a failed database write.
- A native interaction guard alone could not fix that because the helper consumed the exception before the callback saw it.
- During branch review, a second same-root-cause hole was found: `get_guild_config(refresh=True)` preserves stale cached config when the database is unavailable. A failed save could therefore be “verified” from stale cache if the cached value happened to match the selected role.

## Execution path

`VerifyRoleSelect.callback`
→ `run_guarded_interaction(..., defer=True)`
→ `_save_mapping`
→ staff check
→ server-context check
→ `_bot_can_manage_role`
→ explicit-override `_save_role_config`
→ invalidate guild-config cache
→ forced `get_guild_config(..., refresh=True)`
→ compare persisted config key to selected role ID
→ success message only on exact match.

Unexpected failures stay inside the native interaction guard so staff receive a structured Error ID and cautious recovery guidance.

## Changes

- wrapped Role Mapping save execution with `run_guarded_interaction(..., defer=True, ephemeral=True)`;
- preserved existing staff, hierarchy, role-selection, config-key, and explicit-override behavior;
- added a forced post-save config read;
- invalidate the guild-config cache immediately before verification so stale cache cannot satisfy the persistence check;
- raise inside the guarded action when the saved role ID cannot be confirmed;
- send the existing human-readable success message only after exact persisted-ID verification;
- added focused static regression coverage for guard ownership, ordering, hierarchy preservation, explicit override, stale-cache invalidation, server-context fail-closed behavior, and no success-before-verification.

## Validation / results

Current branch: `audit/p0-int-verify-role-map-integrity`.

Current PR: **#279 — Guard Verification Center role mapping persistence**.

- branch is based on current `main` commit `f18075b84c705e16b792e0d40ac7daed0f814a61`;
- branch is 0 commits behind `main`;
- diff is limited to the Verification Center role-mapping production file, its focused regression test, and audit/task documentation;
- production flow was re-read against `public_verify_group._save_role_config` and `guild_config.get_guild_config`;
- stale-cache fallback behavior was found during review and corrected before PR validation;
- targeted modified-region Python AST parse passed;
- targeted source replay passed guard ownership and save → cache invalidate → refresh → verify → success ordering;
- PR #279 initial head `add8e6bd3d1fbc426158218b19ab33b8a3409c76` produced 0 workflow runs / 0 commit statuses even though `.github/workflows/ci.yml` listens to `pull_request`;
- exact PR head after the bookkeeping synchronize commit: `7a5aac5ea5130ad2c684fe8dbd91f2dbc0bebfde`;
- exact-head production blob: `2dfc0d4554f2d3cd17fe2658c951245c957736be`;
- exact-head focused test blob: `76ed972b0c21ecc0cb8248edd5ed44055320e22d`;
- exact-head task-record blob: `cc22aeae323453dccd46dfbf501561a7d9806882`;
- exact-head readiness-ledger blob: `1d231b7552b410356c9cf385bdd1957f2cb08ce0`;
- PR #279 has 0 unresolved review threads;
- the synchronize event also produced 0 workflow runs / 0 commit statuses, so configured PR CI did not instantiate on either observed head;
- no absent workflow is represented as passing.

## Cleanup / conflicts

- no changes to `_save_role_config` global semantics;
- no duplicate persistence implementation introduced;
- no role-discovery aliases or canonical verification commands changed;
- no unrelated runtime code included;
- existing owner/staff/hierarchy gates remain authoritative.

## Blockers / risks

- full completion still requires executable CI or equivalent full repository validation plus post-merge verification on `main`;
- GitHub Actions has previously failed to create/execute runners for nearby slices, so lack of runner execution must be recorded honestly rather than treated as green CI.

## Backlog

**P0 — production crash around Invite Shield reconciliation / rate-limit pressure**

Confirmed separately while this task was locked:

- deprecated `Message.interaction` access creates repeated warning amplification;
- startup invite reconciliation performs REST-backed history scans across many channels;
- observed production sweep checked 2,348 messages across 58 channels;
- the supplied reconcile line itself completed with `failed=0`, so the exact fatal crash line remains unproven from the available excerpt.

Do not investigate or modify that issue until the active task reaches its Definition of Done unless the user explicitly FORCE SWITCHes.

## Previous completed slice

**PR #277 — Guard Verification Center canonical dispatcher**

- merged as `35714aae0da9b4dcebfcb6d51abfade83f90beba`;
- verified on `main`;
- validated center blob: `c1ae1b0273871025b40d4fd9da657490d75176b8`;
- validated regression-test blob: `1b91918bbc4c1d0f8ee8f55683d5e5cce2ac55f1`.

## Next step

Obtain executable full-repository validation for exact head `7a5aac5ea5130ad2c684fe8dbd91f2dbc0bebfde`. Keep PR #279 draft until that blocker is cleared; then re-check the final diff/head, mark ready, merge, and verify the validated production/test blobs on `main`.
