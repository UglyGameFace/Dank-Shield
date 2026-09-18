# ACTIVE TASK

## DS-SEC-GUILD-UPDATE-SEVERITY — Stop routine server settings from becoming destructive AntiNuke evidence

**Status:** FINAL EXACT-HEAD VALIDATION

**Branch:** `fix/antinuke-guild-update-severity`
**Base main:** `be99ee673f72254eb389de7d77fedc4ff99900d9`
**Validated implementation head:** `fca7fe03a1e0992bcccc24a0ae5c0d744ac9424e`

## Previous task closed

PR #256 (`DS-SEC-ANTINUKE-RUNTIME-CONSOLIDATION`) merged as `be99ee673f72254eb389de7d77fedc4ff99900d9`, became `main`, contains exact validated PR head `b19dea95e1752dbd8698ad2c4de31a48177a0491` as its second parent, and `discloud/commit` reported success.

The Single Active Task Lock is this guild-update severity normalization only.

## Root cause

Routine server-profile and housekeeping edits were still classified as destructive `guild_update` evidence for non-owner staff.

- guardian's guild-update "security" field set mixed real security fields with name/icon/banner/description
- zero-damage expanded that set further with AFK, system-channel, locale, and premium-progress-bar fields
- any matching field reached the destructive-event processor
- `guild_update` also entered the guild-wide panic circuit at high/severe weight

That allowed legitimate server-setting work by multiple staff to contribute to an AntiNuke emergency despite no destructive authority change. Owner handling was already severity-aware after PR #253, so this was an inconsistent non-owner path.

## Implemented behavior

Guild-update classification now matches the existing owner severity model:

- **routine / ignored:** name, icon, banner, splash, discovery splash, description, default notification level, AFK channel/timeout, system channel/flags, locale, premium progress bar
- **bounded security:** verification level, explicit content filter, rules channel, public updates channel, safety alerts channel, features, vanity URL
- **immediate authority:** owner transfer and MFA level

Additional hardening:

- zero-damage no longer re-promotes routine fields into destructive guild-update evidence
- guild-update panic weight is severity-aware:
  - routine = 0
  - bounded security = 2
  - immediate authority = 4
- severe coordination requires severe weight >= 3, so bounded guild-security changes do not masquerade as immediate severe events
- distributed real security-setting mutations still reach the weighted multi-actor panic ceiling
- guardian routine/bounded/immediate field sets are regression-locked to the incident runtime's owner-severity field sets

## Preserved behavior

- verification/content-filter/rules/public-updates/safety-alerts/features/vanity changes remain AntiNuke evidence
- owner transfer and MFA changes remain highest-severity guild-update evidence
- Strict Lockdown still applies to security-sensitive guild updates that reach the processor
- owner severity normalization from PR #253 remains unchanged
- destructive channel/role/overwrite/webhook/etc. policy remains unchanged
- sparse/unknown guild updates are not promoted without concrete security-field evidence

## Regression correction during validation

Implementation head `a5b74ba2045116af13033581743b1ff5199d1c22` exposed one stale test expectation in the full unit suite:

- `test_identity_or_security_guild_update_is_enforced` still expected routine field `name` in the security target label
- actual behavior correctly emitted only `Server settings • verification_level`
- CI result was **1 failed, 1637 passed**
- the stale assertion was updated to require `name` absent and `verification_level` present

No production logic change was needed for that failure.

## Exact implementation-head validation

Exact corrected head `fca7fe03a1e0992bcccc24a0ae5c0d744ac9424e` passed the complete workflow set:

- Dank Shield CI #2291: **success**
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
- Application Command Size Diagnostics #1274: **success**
- Dank Design Regression CI #516: **success**
- Ticket Owner Emergency Override #862: **success**
- Profile Runtime Diagnostics #1023: **success**

## Final diff / review audit

At implementation head `fca7fe03a1e0992bcccc24a0ae5c0d744ac9424e`:

- branch is 0 commits behind current `main` `be99ee673f72254eb389de7d77fedc4ff99900d9`
- PR is mergeable
- exactly 6 task-related files are changed:
  - `ACTIVE_TASK.md`
  - `stoney_verify/anti_nuke_guardian_runtime.py`
  - `stoney_verify/anti_nuke_zero_damage_runtime.py`
  - `tests/test_antinuke_competitive_hardening.py`
  - `tests/test_antinuke_owner_policy_normalization.py`
  - `tests/test_antinuke_zero_damage_runtime.py`
- no unresolved review threads exist
- the only PR comment is Supabase's non-blocking notice that no `supabase` directory changed

## Final exact-head gate

This bookkeeping update changes the PR head SHA. The new final head must independently pass the complete required and companion workflow set before merge.

After that final wave:

1. confirm the branch remains 0 behind current `main` with the same 6 files
2. confirm no review blocker appeared
3. mark PR #257 ready
4. merge using the exact validated final SHA
5. verify the resulting `main` merge commit has that exact PR head as a parent
6. require `discloud/commit: success`
7. release the Single Active Task Lock

## Next step

Run and verify the final exact-head workflow wave created by this bookkeeping commit, then merge PR #257 only if every gate remains green.
