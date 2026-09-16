# ACTIVE TASK

## DS-AUD-GUILD-CONFIG-OWNERSHIP — Consolidate guild config persistence and runtime validation

**Outcome target:** `stoney_verify.guild_config` is the canonical owner of normal per-guild configuration persistence, cache behavior, public isolation, split-brain storage compatibility, and saved Discord-ID validation. Setup compatibility surfaces may delegate to it, but normal production paths must not run a competing guild-config mutation engine or depend on an import-time validator patch.

**Status:** FINAL VALIDATION — implementation/cleanup complete; exact-head CI must pass before merge-readiness is claimed

**Branch:** `audit/guild-config-ownership`
**PR:** #239 (draft)
**Base main:** `2c31b5cade7c4fdd667a90a701c248508bb7d8eb`

## Scope

- canonical `stoney_verify/guild_config.py` persistence/cache/runtime validation
- `commands_ext/public_setup_config_writer.py` ownership consolidation
- production boot ordering where modules can copy setup writer callbacks
- Setup Recovery guild-config mutations
- retirement of `guild_config_runtime_validator` as a production startup owner
- startup diagnostics/audit expectations affected by that retirement
- behavioral regression coverage and stale tests tied to the superseded writer
- task/PR bookkeeping

No unrelated setup-picker redesign, ticket redesign, AntiNuke redesign, Discord API safety migration, or dormant-startup-guard mass cleanup belongs in this task.

## Findings / root cause

1. `stoney_verify.guild_config` already owned the main runtime resolver/cache, but setup also had a second complete Supabase persistence engine in `public_setup_config_writer.py`.
2. Existing rows can expose the same config through flat columns and legacy JSON compatibility buckets. Canonical reads merge `settings`, `config`, `metadata`, and `meta`, so writes/clears that update fewer shapes can let stale values reappear later.
3. `guild_config_runtime_validator` was a live `main.py` monkey patch replacing canonical runtime discovery to purge stale saved Discord IDs.
4. `public_setup_group.py` still contains a historical local writer implementation. Rebinding its globals only during later setup registration was insufficient because several modules import `_upsert_config` by value and can permanently retain whichever function existed at their own import time.
5. Setup Recovery had its own live direct `guild_configs` mutation path, bypassing canonical split-brain synchronization and cache behavior.
6. Direct canonical callers historically use `upsert_guild_config` for intentional admin reassignment. Consolidation therefore must preserve normal direct overwrite semantics while discovery/auto-fill paths opt into explicit fill-only modes.
7. Cache/read failure isolation previously cleared every key ending in `_id`, accidentally clearing `guild_id` itself. Resource IDs must be isolated without erasing the identity of the guild whose config object is being returned.
8. Existing consumers already import `get_cached_guild_config` and `env_fallback_allowed_for_guild`; canonical native implementations were required instead of relying on missing-function fallback behavior.
9. `setup_service_modes.py` still contains an emergency raw-Supabase branch, but its normal path imports the canonical setup facade. It is a compatibility feature helper, not a normal production guild-config writer.
10. The retired validator's only remaining code importer is another dormant compatibility guard reached through dormant setup-health compatibility code. It is not on the verified production boot path.

## Verified production execution path

1. `main.py` no longer imports `guild_config_runtime_validator`.
2. `app.py` imports core runtime modules in its existing deliberate order and then imports `commands.py` before events.
3. `commands.py` imports the `commands_ext` package, which only defines module metadata/helpers at package import time; it does not eagerly import the configured command modules.
4. Before importing command modules that can copy `public_setup_group._upsert_config`, `commands.py` explicitly imports `public_setup_config_writer` and requires `apply_public_setup_writer_patch()` to succeed.
5. The facade binds `public_setup_group._upsert_config_sync` / `_upsert_config` to canonical delegates.
6. Later command-module imports therefore copy the canonical facade callback, not the historical group-local writer.
7. Setup Recovery clears/writes guild config through canonical clear/upsert APIs. Ticket-choice persistence remains feature-owned because `ticket_categories` is a different table.
8. Native `discover_runtime_guild_config` validates/purges stale saved role/channel/category IDs before optional runtime discovery.

## Changes

- Added/expanded canonical sync + async guild-config persistence entrypoints.
- Canonical writes synchronize flat columns and every existing compatibility JSON bucket: `settings`, `config`, `metadata`, and `meta`.
- Canonical clear APIs remove stale keys from flat and every existing compatibility JSON bucket.
- Added canonical protected-write modes, write-source metadata, setup completion invalidation controls, and fill-only no-write behavior.
- Preserved historical direct canonical overwrite behavior for intentional admin mutations.
- Added native `get_cached_guild_config` and `env_fallback_allowed_for_guild` compatibility APIs.
- Preserved `guild_id` on isolated/unavailable fallback objects while clearing resource IDs.
- Converted `public_setup_config_writer` from a second Supabase engine into a thin setup-intent facade.
- Bound the canonical setup writer before command consumers can copy setup callbacks by value; bootstrap fails closed if that binding cannot be established.
- Routed Setup Recovery guild-config mutations through canonical APIs and behaviorally verified snapshot-save → clear → final-write ordering.
- Removed `guild_config_runtime_validator` from production `main.py` startup ownership and startup diagnostics expectations.
- Updated architecture guardrails to identify native guild-config ownership and prevent restoration of the retired boot patch.
- Replaced/migrated tests that pinned private helpers on the old setup writer with canonical behavioral coverage.

## Validation / results

Earlier exact-head CI exposed six in-scope migration/test regressions; all were corrected without restoring duplicate persistence logic.

A later exact head reached **1509 passed / 1 failed**. The only failure was `test_db_read_failure_is_distinct_from_genuine_unconfigured_guild`, whose legacy assertion treated `guild_id` as a resource ID and required it to be `None`. That assertion has now been corrected to require:

- `guild_id == "123"`
- `source == "unavailable:db_read_failed"`
- `use_env_fallbacks is False`
- all other `*_id` resource fields are `None`

The implementation/test head immediately before this task-record commit is `d0cd04485c992227091ca40a7357326baea2c336`.

Companion workflows on the preceding implementation heads have repeatedly passed:

- Ticket Panel Single Owner
- Ticket Owner Emergency Override
- Dank Design Regression CI
- Application Command Size Diagnostics
- Profile Runtime Diagnostics
- Managed category SQL smoke
- Claim-first ticket security
- Python compile/diff whitespace

**Required final evidence:** all PR workflows, including the complete Dank Shield unit + standalone audit lane, must pass on the exact final head containing this record.

Known local limitation: the local runner cannot resolve GitHub for a repository clone, so GitHub Actions is the executable repository-validation source for this task.

## Cleanup / conflicts

- `public_setup_config_writer` no longer owns direct Supabase persistence.
- The live Setup Recovery config mutation bypass is removed.
- Normal public boot cannot copy the historical group-local writer before canonical binding.
- `public_setup_group.py` still physically contains its historical writer as a dormant compatibility fallback. Removing a large shared setup module's fallback solely for aesthetic cleanup is deferred without stronger importer/runtime proof; normal production ownership no longer uses it.
- `setup_service_modes.py` retains an emergency direct-Supabase fallback only if canonical setup-writer import is unavailable; normal behavior uses the canonical facade. No evidence showed that emergency branch participating in production boot.
- The retired validator file remains present but dormant. Deleting it would require following the dormant verification/setup-health compatibility chain and is outside this focused ownership migration.
- `public_server_env_id_guard` and `discord_api_safety` remain unchanged and live under their existing ownership.
- No unrelated setup UI/picker changes are included.
- PR #239 has no review threads or submitted reviews blocking the change.
- Branch remained ahead of and not behind `main` during the final implementation review; re-check before marking ready.

## Blockers / risks

- Final exact-head CI is still required after this task-record update.
- If final CI fails, only failures sharing this ownership root cause or required for compatibility/regression prevention belong in this task.
- No production/live-server acceptance has been claimed from repository CI alone.

## Backlog

### Setup picker does not find expected channels/roles — USER REPORTED, NEXT AUDIT ITEM

User supplied screenshots showing the Fix Access/setup flow opening Discord's generic channel/category picker, failing to surface expected resources, and showing `This interaction failed`. The user expected the previously intended dedicated Dank Shield picker. This has deliberately not been investigated on PR #239 because it is a separate setup UI/resource-discovery task.

When this guild-config ownership task truly closes, recover the actual picker execution path first: locate the screen/callback creating the selector, verify whether the shared/dedicated Dank picker implementation is still authoritative, determine why this path uses Discord's generic resource picker or fails its interaction, and then fix the smallest complete root cause with mobile behavior/regression coverage.

## Next step

Run all PR workflows on the exact final head containing this record. If every required check passes, re-check main drift, review threads, changed-file scope, and PR metadata; update the PR validation summary without moving the head; mark PR #239 ready for review. Do not start the setup-picker task until this task is actually closed/handed off.
