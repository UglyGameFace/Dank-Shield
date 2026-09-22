# ACTIVE TASK

## Active task / desired outcome

**P0-SETTINGS-001B — Spam Guard core settings + canonical setup persistence**

Make the settings registry the semantic owner for Spam Guard core settings while keeping `spam_guard.py` as the single persistence/cache/diagnostics owner for `guild_security_settings`.

Remove the duplicate Spam Guard storage/cache path from the setup compatibility UI.

## Status

**IMPLEMENTED — exact-head validation pending**

Branch: `audit/settings-registry-spamguard-20260922`

Base: current `main` after PR #296.

## Previous task closed

**P0-SETTINGS-001A** is complete.

PR #296 merged as:

`e949f9651f58e3fce3fccbaff3aca9789da813b2`

Exact implementation head:

`91b54aac57d1a75574fd687f457fcef6385787d8`

Termux validation passed:

- diff integrity;
- Python compile;
- settings-registry focused tests;
- Protection/Invite compatibility;
- Spam Guard settings regressions;
- Protection Center regressions;
- full suite: **1814 passed, 79 warnings, 0 failures**.

Post-merge verification confirmed:

- registry present on `main`;
- registry remains schema-only;
- Invite Scope wired to registry;
- Protection Center effective shield state wired;
- Invite Policy Engine wired;
- recovery preflight wired;
- Spam Guard compatibility reads wired;
- registry regression coverage present.

## Root cause / ownership finding

Spam Guard still had split semantic and persistence ownership after the first registry slice.

### Canonical Spam Guard runtime owner

`spam_guard.py` already owns:

- `guild_security_settings` database writes;
- DB readback verification;
- runtime fallback/cache;
- persistence diagnostics;
- bootstrap rows for new guilds;
- enforcement behavior.

### Duplicate setup owner

`startup_guards/setup_service_modes.py` remained production-reachable through the public Spam Guard setup UI and health tooling. It independently owned:

- a second Spam Guard default dictionary;
- broader/different numeric bounds;
- a setup-only 60-minute timeout default while runtime default was 30;
- a private runtime-cache read;
- direct raw Supabase writes to `guild_security_settings`;
- a fallback minimal DB payload;
- direct mutation of Spam Guard's private runtime cache.

That allowed setup and runtime to disagree about saved values and persistence state.

## Canonical ownership after this slice

### Setting meaning

`settings_registry.py` owns:

- Spam Guard semantic keys;
- persisted `spam_*` aliases and precedence;
- exact defaults;
- exact numeric bounds;
- allowed/exempt ID normalization;
- allowed invite code compatibility;
- Safe/Strict/Off presets.

### Persistence/cache/diagnostics

`spam_guard.py` remains the sole owner via:

- `get_spam_settings`;
- `save_spam_settings`;
- canonical DB payload/readback;
- runtime cache;
- persistence diagnostics.

### Setup compatibility UI

`setup_service_modes.py` remains a UI/navigation compatibility helper only.

It now reads and saves through the canonical Spam Guard service.

## Scope

In scope:

- register Spam Guard core schema in settings registry;
- preserve persisted-column precedence;
- preserve exact runtime defaults/bounds;
- share presets with Protection Center;
- route Spam Guard normalizer/defaults through registry;
- route setup Spam Guard reads through `get_spam_settings`;
- route setup Spam Guard writes through `save_spam_settings`;
- remove duplicate setup DB/cache helpers;
- fix setup-only default drift from 60m to canonical 30m;
- update focused regressions and ownership docs.

Out of scope:

- changing Spam Guard enforcement rules;
- changing table schema;
- removing `setup_service_modes.py` UI exports;
- changing setup service-selection flags;
- migrating AntiNuke/design/ticket/verification settings;
- changing Invite Shield policy.

## Expected production behavior

Spam Guard policy/enforcement behavior is unchanged.

Setup now displays and saves the same normalized Spam Guard state that the runtime actually uses.

A setup save receives the same persistence/readback/cache handling as every other Spam Guard save.

## Validation required

- exact-head `git diff --check`;
- Python compile;
- `tests/test_settings_registry_spamguard.py`;
- `tests/test_settings_registry_protection.py`;
- `tests/test_spam_guard_default_on_behavior.py`;
- `tests/test_spam_guard_default_on_bootstrap_behavior.py`;
- `tests/test_external_healthchecks_watchdog_static.py`;
- `tests/test_setup_service_navigation_native.py`;
- `tests/test_setup_service_modes_native_no_patch.py`;
- Protection Center regressions;
- invite-policy regressions;
- full Python suite;
- exact-head review-thread/diff inspection;
- merge with expected-head guard;
- post-merge ownership verification on `main`.

## Next step

Inspect the exact branch diff, open a focused draft PR, validate the exact head through GitHub CI or Termux, then merge and verify before moving to the next settings family.
