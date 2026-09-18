# ACTIVE TASK

## DS-SEC-GUILD-UPDATE-SEVERITY — Stop routine server settings from becoming destructive AntiNuke evidence

**Status:** INVESTIGATION / IMPLEMENTATION

**Branch:** `fix/antinuke-guild-update-severity`
**Base main:** `be99ee673f72254eb389de7d77fedc4ff99900d9`

## Previous task closed

PR #256 (`DS-SEC-ANTINUKE-RUNTIME-CONSOLIDATION`) merged as `be99ee673f72254eb389de7d77fedc4ff99900d9`, became `main`, contains exact validated PR head `b19dea95e1752dbd8698ad2c4de31a48177a0491` as its second parent, and `discloud/commit` reported success.

The Single Active Task Lock is this guild-update severity normalization only.

## Problem

The current guardian intentionally ignores generic channel updates, but guild updates still use a broad "security field" allowlist. The zero-damage layer expands that list to include ordinary administration such as server name/icon/description, AFK settings, system-channel settings, locale, and premium progress bar.

Those routine fields therefore reach the destructive-event processor for non-owner staff. They also enter the guild-wide panic circuit because `guild_update` currently has high panic weight and is classified as severe. Two different staff members making ordinary server-setting edits close together can therefore contribute to an emergency panic even though no destructive authority change occurred.

Owner handling is already severity-aware after PR #253, so the broad guardian classification is now inconsistent with the authoritative owner policy.

## Root cause

- `anti_nuke_guardian_runtime._GUILD_UPDATE_SECURITY_FIELDS` mixes cosmetic/routine and actual security/authority fields.
- `anti_nuke_zero_damage_runtime._GUILD_FIELDS` expands that mixed set further with routine fields.
- `_on_audit_log_entry_create()` processes any guild update that changes any member of that set.
- `_panic_state()` treats processed `guild_update` events as high-risk/severe for all non-owner, non-bot actors, including trusted staff.

## Intended behavior

- Routine/cosmetic server profile and housekeeping edits must not enter destructive AntiNuke counters or the guild-wide panic circuit.
- Security-sensitive guild changes remain protected.
- Strict Lockdown may still apply first-strike behavior to security-sensitive guild updates that actually reach the processor.
- Owner severity policy from PR #253 remains unchanged.
- Sparse/unknown guild-update evidence must fail safely without inventing a destructive classification.

## Implementation scope

Normalize guardian guild-update field classification to match the existing severity model:

- routine/benign: name, icon, banner, splash, discovery splash, description, default notification level, AFK channel/timeout, system channel/flags, locale, premium progress bar
- bounded security: verification level, explicit content filter, rules channel, public updates channel, safety alerts channel, features, vanity URL
- immediate authority: owner transfer and MFA level

Update zero-damage coverage so it does not re-add routine fields as destructive evidence. Add focused regressions proving routine edits are ignored after the full guardian/zero-damage surface is installed while real security changes remain observable.

## Validation / merge gate

Before merge:

- focused guild-update severity regressions pass
- owner normalization regressions remain green
- AntiNuke panic/guardian/zero-damage/product-policy regressions remain green
- full Python compile + full unit suite + standalone/security/event-boundary audits pass
- Claim-first ticket security and Managed category SQL smoke pass
- all companion workflows pass on the exact final head
- branch is 0 behind current `main`
- final diff contains only task-related files
- no unresolved review blockers
- merge only the exact validated SHA
- verify resulting `main` merge parent and require `discloud/commit: success`

## Next step

Implement the smallest guardian/zero-damage field-classification fix and add focused regression coverage.
