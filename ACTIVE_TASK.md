# ACTIVE TASK

## DS-AUD-GUILD-CONFIG-OWNERSHIP — Consolidate guild config persistence and runtime validation

**Outcome target:** `stoney_verify.guild_config` is the one canonical owner of per-guild configuration reads, writes, cache behavior, split-brain storage compatibility, public isolation, and saved Discord-ID validation. Setup modules may keep stable compatibility imports, but they must not implement a second Supabase persistence engine or mutate the canonical config module at import time.

**Status:** IN PROGRESS — implementation branch active; PR/CI validation not complete

**Branch:** `audit/guild-config-ownership`
**Base main:** `2c31b5cade7c4fdd667a90a701c248508bb7d8eb`

## Scope

- `stoney_verify/guild_config.py`
- `stoney_verify/commands_ext/public_setup_config_writer.py`
- production startup ownership for `guild_config_runtime_validator`
- startup diagnostics/audit expectations affected by that retirement
- focused behavioral regression coverage for persistence, clearing, and native runtime validation
- task record

No unrelated setup UI redesign, ticket redesign, AntiNuke redesign, Discord API safety migration, dormant-guard mass cleanup, or feature work belongs in this task.

## Findings / root cause

1. `stoney_verify.guild_config` already owns canonical per-guild config reads, cache state, env fallback isolation, and a generic writer.
2. `stoney_verify.commands_ext.public_setup_config_writer` had grown into a second full persistence engine with its own Supabase access, split-brain merge rules, overwrite policy, setup-completion invalidation, and clear behavior.
3. The setup writer was therefore not a thin compatibility layer. Setup behavior and canonical runtime behavior could diverge depending on which writer a caller imported.
4. Existing rows may contain the same value in flat columns plus `settings` and `config` JSON shapes. The public setup writer had explicit behavior to keep those shapes synchronized and to prevent stale JSON from resurrecting cleared values.
5. `startup_guards.guild_config_runtime_validator` is still a live `main.py` monkey patch that replaces `discover_runtime_guild_config` so stale saved roles/channels/categories are purged from Supabase before runtime discovery.
6. That validation behavior belongs with canonical config resolution, not in an import-time startup patch.
7. Direct callers of canonical `upsert_guild_config` historically use it for legitimate admin reassignment as well as fill-missing flows. Consolidation must not silently change those direct calls into no-ops.
8. Historical setup-specific split-brain tests pinned the second writer rather than canonical ownership, so ownership tests need to move with the behavior.

## Execution path

Current production flow before this branch:

1. `main.py` imports `guild_config_runtime_validator`.
2. Import executes its patch immediately and replaces `stoney_verify.guild_config.discover_runtime_guild_config`.
3. Feature/runtime callers resolve through the patched function for stale-ID cleanup and runtime discovery.
4. Setup flows generally import `commands_ext.public_setup_config_writer`, which persists directly through Supabase rather than through `guild_config.upsert_guild_config`.
5. Other feature/admin callers import `guild_config.upsert_guild_config` directly, so two mutation engines govern the same row family.

Target flow on this branch:

1. `stoney_verify.guild_config` owns persistence, split-brain synchronization, clear behavior, cache state, and native saved-ID validation.
2. `public_setup_config_writer` remains only as a stable compatibility facade that annotates setup intent and delegates to the canonical owner.
3. `main.py` no longer activates `guild_config_runtime_validator`.
4. Startup diagnostics/audit expectations no longer require that retired validator owner.
5. Runtime discovery calls the native canonical validator/discovery path directly.

## Changes so far

- Added canonical synchronous and asynchronous guild-config persistence entrypoints.
- Moved split-brain compatibility behavior into the canonical writer so writes can keep flat, `settings`, and `config` shapes synchronized when those shapes exist.
- Added canonical explicit key-clearing behavior that removes stale values from flat and both JSON shapes.
- Added canonical config-write metadata/control handling so setup can request `setup_builder` semantics without owning persistence.
- Kept direct canonical writes compatible with historical overwrite behavior while allowing explicit `fill_missing`/runtime-discovery safety modes for auto-discovery paths.
- Converted `public_setup_config_writer` into a compatibility facade over the canonical writer/clear APIs.
- Removed production `main.py` activation of `guild_config_runtime_validator` and removed it from startup diagnostics expectations.
- Updated the explicit startup-owner audit tool accordingly.
- Retired the setup-owned split-brain test file and replaced it with native ownership tests.
- Added behavior-level coverage for canonical split-brain writes, canonical clears, setup facade delegation, direct-write compatibility, explicit fill-missing protection, and native stale-ID purge.

## Validation / results

Not yet validated on CI. Current branch head before PR creation: `ae29d6c4891a516d4ad9e8f0d6563a0334d1c128`.

Known local limitation: this environment cannot clone/run the repository locally because the runner cannot resolve GitHub. Validation must therefore come from repository CI on the branch/PR rather than being invented from a failed local clone.

## Cleanup / conflicts

- No second writer should remain in `public_setup_config_writer` after this migration.
- The retired runtime validator module has not yet been deleted because importer/supersession references still need exact review before file removal. Production ownership has been removed.
- `public_server_env_id_guard` remains live and unchanged in this task because public env-ID isolation is a separate ownership migration with its own boot-order behavior.
- `discord_api_safety` remains live and unchanged.
- Dormant ticket/API guild-config compatibility guards are not being mass-deleted in this task without importer proof.

## Blockers / risks

- CI has not yet compiled or executed the branch.
- Canonical writer migration touches a high-centrality module, so full repository tests and targeted setup/config tests are required before any completion claim.
- Schema compatibility fallback payloads must be proven against existing tests/workflows.

## Backlog

### Setup picker does not find expected channels/roles — USER REPORTED, NOT INVESTIGATED IN THIS TASK

User supplied screenshots showing `/dank setup` still opening the generic Discord resource picker and returning incomplete/incorrect results. The user specifically expected the previously intended dedicated Dank Shield setup picker rather than Discord's generic picker. Symptoms include the picker failing to surface expected resources and making setup frustrating/unusable. This is a separate setup-UI/resource-discovery issue and must not be investigated or patched on the active guild-config ownership branch unless it is proven to share this task's root cause.

## Next step

Open a draft PR from `audit/guild-config-ownership`, let repository CI compile and execute the new native ownership path, then fix only failures that are in-scope or required for compatibility/regression prevention. After one exact PR head is green, perform final diff/importer/cleanup review before considering merge readiness.
