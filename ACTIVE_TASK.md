# ACTIVE TASK

## DS-SEC-ANTINUKE-RUNTIME-CONSOLIDATION — Make AntiNuke runtime ownership explicit

**Status:** FINAL EXACT-HEAD VALIDATION

**Branch:** `refactor/antinuke-runtime-bootstrap-consolidation`
**Base main:** `bbf36ee6a4859f55a2e23373313cb21ccb64686d`
**Validated Phase A head:** `984c4b4d75ef805d8fd59d92af97cabbc0460699`
**Validated Phase B implementation head:** `c8dff74f5a7bb38c95707b4040d1586dcbcb954f`

## Previous task locks closed

- PR #251 (`DS-MEMBER-JOIN-LOG-REGRESSION`) merged as `0c8d8ae1971656f0c5c2e9ef95ebd11a9c94fa97`, became `main`, and Discloud reported success.
- PR #253 (`DS-SEC-OWNER-POLICY-NORMALIZATION`) merged as `e895e875c1661d32424c968babca5ef098786fc1`, became `main`, and Discloud reported success.
- PR #254 (`DS-INVITE-HUMAN-EMBED-FALSE-POSITIVE`) merged as `bc0a14cf59bcddb7c7ac0a45498125921cae4023` and Discloud reported success.
- PR #255 (`DS-INVITE-INTERACTION-RESPONSE-FALSE-POSITIVE`) merged as `bbf36ee6a4859f55a2e23373313cb21ccb64686d` and Discloud reported success.

The Single Active Task Lock is this AntiNuke runtime-ownership consolidation only.

## Resync after emergency hotfixes

The consolidation branch was paused while the two live Invite Shield regressions were fixed. The previous consolidation head `e46c7b771088e464867ae5cb374cd0e714c30d72` was preserved at `backup/antinuke-runtime-bootstrap-pre-hotfix-sync`, then the consolidation changes were reapplied onto production `main` `bbf36ee6a4859f55a2e23373313cb21ccb64686d`.

The resync explicitly preserves `_install_invite_policy_message_surface_runtime()` before invite reconciliation. AntiNuke consolidation must not regress PRs #254/#255.

## Outcome target

Reduce the architectural risk created by AntiNuke being assembled through a long sequence of independently owned startup wrappers and overlapping runtime policy patches. Preserve validated security behavior while making bootstrap order and policy ownership explicit enough that later fixes do not silently depend on accidental monkey-patch order.

## Phase A — bootstrap ownership consolidation

Implemented:

- added `stoney_verify/anti_nuke_runtime_coordinator.py`
- moved the authoritative pre-app and post-app AntiNuke installation order into declarative layer tables
- centralized lazy import resolution, duplicate reporting, and per-layer fail-soft exception handling
- reduced `main.py` to one pre-app AntiNuke coordinator call and one post-app coordinator call
- preserved the exact behavioral module order and app-import boundary
- kept SpamGuard independent
- preserved Invite Shield message-surface setup before invite reconciliation
- migrated startup-order regressions away from obsolete `main.py` wrapper-string checks
- added focused coordinator tests for exact order, installer contracts, bot argument handling, duplicate results, fail-soft continuation, and bootstrap boundaries

### Phase A validation

The first Phase A CI attempt exposed only trailing blank-line whitespace in six migrated test files. Those files were cleaned and revalidated.

Exact Phase A head `984c4b4d75ef805d8fd59d92af97cabbc0460699` was 0 behind `main` and passed the complete workflow set:

- Dank Shield CI #2284: **success**
  - Python compile check: **success**
  - full unit test suite: **success**
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
- Application Command Size Diagnostics #1268: **success**
- Dank Design Regression CI #510: **success**
- Ticket Owner Emergency Override #855: **success**
- Profile Runtime Diagnostics #1017: **success**

Phase A therefore established a behavior-preserving centralized bootstrap boundary before Phase B edits.

## Phase B — overlapping policy ownership cleanup

### Deeper overlap audit

The overlap audit found that most apparently duplicated strict behavior is **intentional fail-closed staging**, not dead code:

- `anti_nuke_lockdown_runtime` and `anti_nuke_zero_damage_runtime` install conservative pre-app structural/guardian defaults.
- `anti_nuke_product_policy_runtime`, installed post-app, deliberately relaxes those defaults for healthy normal Contain and reapplies one-strike behavior only for Strict Lockdown where appropriate.
- the coordinator is fail-soft. If product-policy installation fails, deleting the pre-app conservative defaults would turn a product-policy startup failure into a fail-open security regression.
- therefore the pre-app strict processor keys, guardian overrides, and rollback guards remain intentionally present and are now documented as fail-closed fallback behavior.

One overlap is genuinely redundant:

- PR #253 made `anti_nuke_incident_runtime._owner_event_policy()` and `_process_owner_destructive_event()` the authoritative guild-owner severity classifier.
- that classifier independently chooses benign, bounded, or immediate policy and does not depend on the old blanket owner `threshold_override=1`.
- `anti_nuke_lockdown_runtime._patch_owner_first_strike()` only rewrote the argument to `1`; it no longer affected the authoritative outcome and obscured ownership.

### Phase B implementation

Removed only the proven redundant owner-severity overlap:

- removed lockdown `_OWNER_PATCH_FLAG`
- removed `_patch_owner_first_strike()`
- removed the incident import and owner patch call from the lockdown installer
- changed lockdown startup reporting to `owner severity=incident-owned`
- documented the remaining strict pre-app behavior as fail-closed fallback that product policy relaxes after app import
- replaced the obsolete lockdown owner-first-strike unit test with an ownership regression proving lockdown no longer patches owner severity and incident runtime contains the authoritative classifier

No threshold, containment, bot-add authorization, quarantine, rollback, trusted-staff, or Strict Lockdown semantics were intentionally changed.

## Canonical ownership after Phase B

- **incident runtime:** guild-owner severity classification and owner compromise/burst reporting
- **product-policy runtime:** healthy-state normal Contain versus optional Strict Lockdown semantics
- **lockdown runtime:** config-history/control-plane protection, bot delegation/integrity, and conservative pre-app fallback
- **zero-damage runtime:** expanded audit coverage, self-action hardening, compromise quarantine, and conservative pre-app fallback
- **gateway/guardian:** event attribution, action dispatch, rollback surfaces, and panic evidence
- **runtime coordinator:** installation order and pre/post-app bootstrap boundary only

## Phase B exact-head validation

Exact implementation head `c8dff74f5a7bb38c95707b4040d1586dcbcb954f` passed the complete workflow set:

- Dank Shield CI #2286: **success**
  - Python compile check: **success**
  - full unit test suite: **success**
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
- Application Command Size Diagnostics #1270: **success**
- Dank Design Regression CI #512: **success**
- Ticket Owner Emergency Override #857: **success**
- Profile Runtime Diagnostics #1019: **success**

## Final diff / ownership audit

At the validated Phase B head:

- branch was 0 commits behind current `main` `bbf36ee6a4859f55a2e23373313cb21ccb64686d`
- PR changed exactly 11 task files, limited to the coordinator/bootstrap, lockdown ownership cleanup, task record, and associated regression migrations/tests
- no unresolved review threads existed; the only PR comment was Supabase noting no `supabase` directory changes
- no Git conflict markers, debug breakpoints, or task TODO/FIXME artifacts were found in changed files
- `main.py` still installs Invite Shield message-surface protection before invite reconciliation, then AntiNuke pre-app coordinator before app import, AntiNuke post-app coordinator after app import, SpamGuard after post-app, and finally runs the bot
- lockdown contains no `_OWNER_PATCH_FLAG`, no `_patch_owner_first_strike()`, and no incident-runtime owner patch import
- incident runtime still contains the authoritative `_owner_event_policy()` and `_process_owner_destructive_event()`
- the conservative lockdown `_STRICT_PROCESS_ACTION_KEYS`, `_STRICT_GUARDIAN_ACTIONS`, and related rollback behavior remain deliberately intact as fail-closed pre-app fallback
- owner-policy normalization and Invite Shield regressions are covered by the full passing unit/audit suite

## Final exact-head gate

This bookkeeping commit changes the PR head SHA. The new final head must independently pass the complete required and companion workflow set before merge. Do not merge based only on the successful Phase B implementation-head wave.

After the final wave:

1. confirm the branch is still 0 behind current `main` with the same intended 11 files
2. confirm no new review blocker exists
3. mark PR #256 ready
4. merge using the exact validated final head SHA
5. verify the resulting `main` merge commit has that exact PR head as a parent
6. require `discloud/commit: success` before releasing the task lock

## Next step

Run the final exact-head workflow wave created by this bookkeeping update. If every workflow remains green and `main` has not drifted, merge PR #256 and verify production deployment.
