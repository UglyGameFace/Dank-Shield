# ACTIVE TASK

## DS-AUD-GUILD-CONFIG-OWNERSHIP — Consolidate guild config persistence and runtime validation

**Outcome target:** `stoney_verify.guild_config` is the one canonical owner of per-guild configuration reads, writes, cache behavior, split-brain storage compatibility, public isolation, and saved Discord-ID validation. Setup modules may keep stable compatibility imports, but they must not implement a second Supabase persistence engine or mutate the canonical config module at import time.

**Status:** IN PROGRESS — implementation complete enough for exact-head CI; final validation/cleanup gate still open

**Branch:** `audit/guild-config-ownership`
**PR:** #239 (draft)
**Base main:** `2c31b5cade7c4fdd667a90a701c248508bb7d8eb`

## Scope

- `stoney_verify/guild_config.py`
- `stoney_verify/commands_ext/public_setup_config_writer.py`
- production startup ownership for `guild_config_runtime_validator`
- startup diagnostics/audit expectations affected by that retirement
- focused behavioral regression coverage for persistence, clearing, cache compatibility, and native runtime validation
- setup tests that still pinned the retired writer internals
- task record

No unrelated setup UI redesign, ticket redesign, AntiNuke redesign, Discord API safety migration, dormant-guard mass cleanup, or feature work belongs in this task.

## Findings / root cause

1. `stoney_verify.guild_config` already owned canonical per-guild config reads, cache state, env fallback isolation, and a generic writer.
2. `stoney_verify.commands_ext.public_setup_config_writer` had grown into a second full persistence engine with its own Supabase access, split-brain merge rules, overwrite policy, setup-completion invalidation, and clear behavior.
3. The setup writer was therefore not a thin compatibility layer. Setup behavior and canonical runtime behavior could diverge depending on which writer a caller imported.
4. Existing rows may contain the same value in flat columns plus `settings` and `config` JSON shapes. The former setup writer had explicit behavior to keep those shapes synchronized and to prevent stale JSON from resurrecting cleared values.
5. `startup_guards.guild_config_runtime_validator` was still a live `main.py` monkey patch that replaced `discover_runtime_guild_config` so stale saved roles/channels/categories were purged from Supabase before runtime discovery.
6. That validation behavior belongs with canonical config resolution, not in an import-time startup patch.
7. Direct callers of canonical `upsert_guild_config` historically use it for legitimate admin reassignment as well as fill-missing flows. Consolidation must preserve direct overwrite behavior while requiring explicit fill-only mode for discovery/auto-fill paths.
8. Historical setup-specific tests pinned private helpers and Supabase access on the second writer. Those tests must move with ownership instead of forcing the duplicate implementation to remain.
9. Consumers already import `get_cached_guild_config` and `env_fallback_allowed_for_guild`; canonical `guild_config.py` needed native public implementations so callers no longer rely on missing/exception-fallback behavior.
10. `public_setup_group.py` still carries a historical local writer fallback. The compatibility facade now binds the group's writer aliases immediately when the facade is imported, removing later registration-order dependence in the public command profile. The old implementation remains dormant for compatibility pending safe importer-proof cleanup.

## Execution path

Production flow before this branch:

1. `main.py` imported `guild_config_runtime_validator`.
2. Import executed its patch immediately and replaced `stoney_verify.guild_config.discover_runtime_guild_config`.
3. Feature/runtime callers resolved through the patched function for stale-ID cleanup and runtime discovery.
4. Setup flows generally imported `commands_ext.public_setup_config_writer`, which persisted directly through Supabase rather than through `guild_config.upsert_guild_config`.
5. Other feature/admin callers imported `guild_config.upsert_guild_config` directly, so multiple mutation engines governed the same row family.

Target flow on this branch:

1. `stoney_verify.guild_config` owns persistence, split-brain synchronization, clear behavior, cache state, public env-fallback policy, cached-config compatibility, and native saved-ID validation.
2. `public_setup_config_writer` remains only as a stable compatibility facade that annotates setup intent and delegates to the canonical owner.
3. Importing that facade immediately binds `public_setup_group` writer callbacks to the canonical facade, so normal public boot does not execute the historical local writer.
4. `main.py` no longer activates `guild_config_runtime_validator`.
5. Startup diagnostics/audit expectations no longer require that retired validator owner.
6. Runtime discovery calls the native canonical validator/discovery path directly.

## Changes so far

- Added canonical synchronous and asynchronous guild-config persistence entrypoints.
- Moved split-brain compatibility behavior into the canonical writer so writes keep flat, `settings`, and `config` shapes synchronized when those shapes exist.
- Added canonical explicit key-clearing behavior that removes stale values from flat and both JSON shapes.
- Added canonical config-write metadata/control handling so setup can request `setup_builder` semantics without owning persistence.
- Preserved historical overwrite semantics for direct canonical writes; explicit `fill_missing`/runtime-discovery modes block protected reassignment and now avoid unnecessary writes when everything is blocked.
- Added native `get_cached_guild_config` and `env_fallback_allowed_for_guild` compatibility APIs with public-guild isolation behavior.
- Converted `public_setup_config_writer` into a compatibility facade over canonical writer/clear APIs.
- Made the facade bind `public_setup_group._upsert_config_sync` / `_upsert_config` immediately on import to remove later setup-registration dependence.
- Removed production `main.py` activation of `guild_config_runtime_validator` and removed it from startup diagnostics expectations.
- Updated the explicit startup-owner audit tool accordingly.
- Retired the setup-owned split-brain test file and replaced it with native ownership tests.
- Migrated tests that still called removed setup-writer private helpers so completion invalidation and clear-payload behavior are asserted against canonical `guild_config` ownership.
- Added behavior-level coverage for split-brain writes, canonical clears, setup facade delegation, direct-write compatibility, explicit fill-missing protection/no-write behavior, native stale-ID purge, cached config compatibility, and env-fallback isolation.

## Validation / results

PR #239 exact head `73ec7657ccab9b49c60b9ea06ddfc5ca6dadd367` ran all five PR workflows:

- Profile Runtime Diagnostics #931: **success**
- Dank Design Regression CI #424: **success**
- Application Command Size Diagnostics #1182: **success**
- Ticket Owner Emergency Override #752: **success**
- Dank Shield CI #2181: **failure** in unit-test gate

The failing full-suite run compiled successfully and reported **1502 passed / 6 failed**. All six failures were in-scope ownership-test/test-migration issues:

1. fill-missing protection test expected an unnecessary write after a reassignment was correctly blocked;
2. stale-ID validation fixture contained two additional intentionally invalid voice-channel IDs but expected only the staff role to be reported;
3-4. two setup navigation tests still called removed private completion helpers on `public_setup_config_writer`;
5. voice reconciliation test still called removed private clear-payload helper on the setup writer;
6. voice reconciliation atomic-clear test monkeypatched Supabase internals on the facade rather than on canonical `guild_config`.

Those six test issues have been corrected without restoring duplicate persistence logic. Current implementation/test head before this task-record update: `55b0b32a4c2c2d81512d12e4062ba5bd56e483f0`.

Known local limitation: this environment cannot clone/run the repository locally because the runner cannot resolve GitHub. Repository CI is therefore the executable validation source.

## Cleanup / conflicts

- `public_setup_config_writer` no longer owns Supabase persistence.
- Setup private-helper tests no longer pin behavior to the retired writer implementation.
- The historical local writer remains present in `public_setup_group.py` as a dormant compatibility fallback; public-profile import ordering now binds its live callbacks to the canonical facade before setup use. Deleting the large fallback requires direct importer/reference proof and is not being done speculatively.
- The retired `guild_config_runtime_validator` module has not yet been deleted because exact importer/supersession references still require final review. Production ownership has been removed.
- `setup_service_modes.py` retains a direct-Supabase emergency branch used only if importing the canonical setup writer fails. It is a compatibility feature owner, not production startup ownership, and is not being rewritten without proof that the fallback is reachable/incorrect.
- `public_server_env_id_guard` remains live and unchanged because its boot-order behavior is a separate ownership migration.
- `discord_api_safety` remains live and unchanged.
- Dormant ticket/API guild-config compatibility guards are not being mass-deleted without importer proof.

## Blockers / risks

- A new exact-head full CI run must pass after the six test migrations.
- Canonical writer migration touches a high-centrality module, so standalone audit steps that were skipped after the failed unit gate still need to execute successfully.
- Final PR diff/importer review must verify no accidental unrelated changes and confirm the retired validator file can either remain dormant safely or be deleted with reference proof.

## Backlog

### Setup picker does not find expected channels/roles — USER REPORTED, NOT INVESTIGATED IN THIS TASK

User supplied screenshots showing `/dank setup` still opening the generic Discord resource picker and returning incomplete/incorrect results. The user specifically expected the previously intended dedicated Dank Shield setup picker rather than Discord's generic picker. Symptoms include the picker failing to surface expected resources and making setup frustrating/unusable. This is a separate setup-UI/resource-discovery issue and must not be investigated or patched on the active guild-config ownership branch unless it is proven to share this task's root cause.

## Next step

Freeze the new PR head, run all PR workflows on that exact commit, and inspect the full Dank Shield CI through the standalone audit steps that were previously skipped. If green, perform final diff/importer/cleanup review, update this record and the PR validation summary, then decide whether the draft can be marked ready for review.
