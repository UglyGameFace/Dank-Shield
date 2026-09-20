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

**IMPLEMENTED — source/diff inspection clean; exact-head PR validation pending**

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

- branch is based on current `main` commit `f18075b84c705e16b792e0d40ac7daed0f814a61`;
- branch is 0 commits behind `main`;
- diff is limited to the Verification Center role-mapping production file, its focused regression test, and audit/task documentation;
- production flow was re-read against `public_verify_group._save_role_config` and `guild_config.get_guild_config`;
- stale-cache fallback behavior was found during review and corrected before PR validation;
- exact-head GitHub CI/workflow execution has not yet been observed and must not be represented as passing.

## Cleanup / conflicts

- no changes to `_save_role_config` global semantics;
- no duplicate persistence implementation introduced;
- no role-discovery aliases or canonical verification commands changed;
- no unrelated runtime code included;
- existing owner/staff/hierarchy gates remain authoritative.

## Blockers / risks

- full completion still requires exact-head PR validation and post-merge verification on `main`;
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

Open the focused PR from `audit/p0-int-verify-role-map-integrity`, validate the exact final head and actual workflow execution state, inspect the final diff for accidental changes, then merge only if the evidence remains clean and verify the merged blobs on `main`.
