# ACTIVE TASK

## Active task / desired outcome

**P0-SETTINGS-001A — Protection / Automod / Invite settings registry**

Create the first canonical settings-registry slice for Protection, Automod, and Invite Shield without moving persistence, changing policy, or creating a second cache/database layer.

## Status

**IMPLEMENTED — exact-head validation pending**

Branch: `audit/settings-registry-protection-20260922`

Base: current `main` after PR #295.

## Previous task closed

**P0-INVITE-RUNTIME-001** is complete.

PR #295 merged as:

`c13712e0131484b0c9d47d639887617490dbe595`

Exact implementation head:

`678ab19e8d8f7b0806e950720538639be2410f47`

Termux validation passed:

- invite safety audit;
- live/recovery ownership tests;
- Invite Shield UI/retirement tests;
- invite-policy/message-surface tests;
- startup ownership tests;
- full suite: **1804 passed, 79 warnings, 0 failures**.

Post-merge verification confirmed:

- five legacy invite runtime/override guards absent;
- startup metadata clean;
- canonical live owner present in `globals.py`;
- canonical recovery owner present in `invite_reconciliation_runtime.py`;
- retirement regression and native safety audit present.

Final invite-delete sweep confirmed remaining Automod/Spam cleanup paths delegate invite-containing messages to `invite_policy_engine`.

**P0-INVITE-001 is complete.**

## Root cause / ownership finding

Dank Shield already has strong persistence owners, but setting meaning is still fragmented.

### Existing persistence ownership

`guild_config.py` owns:

- guild-config reads/writes;
- cache/invalidation;
- public-server isolation;
- DB compatibility/fallback behavior.

`spam_guard.py` owns:

- the Spam Guard security-settings table;
- Spam Guard normalization/cache;
- runtime fallback behavior.

Those systems should remain storage owners.

### Missing ownership

Feature modules independently defined:

- defaults;
- bool coercion;
- canonical vs legacy key names;
- alias precedence;
- effective Invite Shield state;
- effective Link Shield state;
- Invite Shield target aliases.

This duplicated meaning across:

- Protection Center;
- Invite Policy Engine;
- invite reconciliation;
- Invite Scope settings;
- Spam Guard compatibility reads.

That fragmentation allows two screens/subsystems to interpret the same stored state differently even when persistence itself works.

## Native owner introduced

`stoney_verify/settings_registry.py` owns setting **meaning**, not storage.

Each registered setting declares:

- canonical key;
- type;
- default;
- feature owner;
- persistence owner;
- legacy aliases;
- alias precedence;
- allowed choice values when applicable.

The first family registers Protection / Automod / Invite Shield keys.

## Compatibility preserved

No persistence location changes.

Important historical precedence is explicit:

- Spam Guard persisted `spam_block_external_invites_only` and `spam_allow_server_invites` still win over unprefixed compatibility values when both exist.
- Invite target scope still prefers canonical guild-config keys, then current `spam_*` aliases, then old short aliases.
- effective Invite Shield state still honors:
  - `automod_block_invites`;
  - `invite_shield_enabled`;
  - `invite_hard_block_enabled`;
  - Spam Guard `automod_block_invites`;
  - `block_invites`.
- effective Link Shield still honors guild config and Spam Guard compatibility state.

## Scope

In scope:

- add `settings_registry.py`;
- register first Protection/Automod/Invite family;
- route Invite Scope normalization through registry aliases/defaults;
- route Protection Center effective shield state through registry helpers;
- route Invite Policy Engine effective shield state and target aliases through registry helpers;
- route invite-recovery preflight through the same helpers;
- route Spam Guard invite compatibility booleans through registry semantics;
- add focused registry compatibility/ownership tests;
- document schema-vs-storage ownership;
- update master production-readiness ledger.

Out of scope:

- changing DB schema;
- moving guild-config persistence;
- moving Spam Guard persistence;
- removing legacy stored keys;
- bulk-migrating every setting in the bot;
- changing Invite Shield policy;
- changing Automod presets;
- changing AntiNuke settings;
- changing setup/design/ticket/verification settings.

## Changes

- added typed `SettingSpec` registry;
- added canonical Protection/Automod/Invite specs;
- added explicit alias precedence support;
- added shared bool/ID coercion and nested config reads;
- added shared effective Invite Shield / Link Shield helpers;
- added shared Invite Scope normalization;
- migrated five readers to the registry while keeping their public APIs stable;
- added focused tests for defaults, aliases, precedence, nested storage, effective state, storage separation, and wiring;
- documented future one-family-at-a-time migration rules.

## Expected production behavior

**No intended behavior change.**

The registry centralizes interpretation only.

Existing DB/cache owners remain authoritative and existing compatibility aliases remain readable.

## Validation required

- exact-head `git diff --check`;
- Python compile;
- `tests/test_settings_registry_protection.py`;
- `tests/test_protection_invite_native_ui.py`;
- `tests/test_invite_live_enforcement.py`;
- `tests/test_invite_runtime_reconcile_194.py`;
- `tests/test_invite_policy_message_surface_runtime.py`;
- `tests/test_invite_policy_lookup_efficiency_195.py`;
- Spam Guard settings/default regressions;
- Protection Center regressions;
- full Python suite;
- final changed-file/review-thread inspection;
- merge with expected-head guard;
- post-merge registry/native-owner verification on `main`.

## Next step

Inspect exact branch diff, open focused draft PR, validate exact head through GitHub CI or Termux, then merge and verify before registering the next settings family.
