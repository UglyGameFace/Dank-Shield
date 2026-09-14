# ACTIVE TASK

## DS-AUD-COMMAND-OWNERSHIP — Make the menu-first command surface canonical and retire command-tree/bot monkey patches

**Status:** IN PROGRESS — native owner implemented; draft PR / exact-head validation pending
**Branch:** `audit/menu-command-native-ownership`
**Base main:** `fa585c111caf2386c71380e5de9aa44694e6ece4`

## Previous finding closure

`DS-AUD-PROCESS-HEALTH` is closed and must not be reopened without new regression evidence.

Its bookkeeping closeout PR #226 merged as `fa585c111caf2386c71380e5de9aa44694e6ece4`. Acceptance on that exact canonical SHA passed:

- Dank Shield CI #2123 / `34893184698` — success, including the full repository suite and every standard audit step; completed `2026-09-14T20:40:32Z`.
- Ticket Owner Emergency Override #694 / `34893184749` — success on the same SHA.
- Deploy Supabase migrations #29 / `34894355557` — success on the same SHA; started `2026-09-14T20:40:34Z` after canonical CI completed.
- Immutable current-main verification, required secrets, Supabase CLI, project link, migration status, preview, and apply all passed.
- Canonical `main` remained exactly `fa585c111caf2386c71380e5de9aa44694e6ece4` after promotion.

## Product outcome

Dank Shield already has the desired menu-first UX. This finding preserves that product surface rather than creating another control center.

Canonical public application-command contract:

- `/dank`
- `/mod`
- `/ticket`
- `/tickets`
- `/verify`
- `View Dank Profile` context menu

Approved direct `/dank` children remain exactly `home`, `purge`, `setup`, and `upload`. Feature discovery/configuration continues through the existing Home control-center UI.

## Root cause / old execution path

Before this branch, command ownership was layered around discord.py:

1. `main.py` imported `startup_guards.command_safety`.
2. `command_safety` transitively imported `auto_shard` and `global_command_sync`.
3. `auto_shard` could globally replace `discord.ext.commands.Bot`.
4. `global_command_sync` globally replaced `CommandTree.sync`.
5. `command_safety` globally replaced `CommandTree.add_command`, swallowed `CommandLimitReached`, and wrapped `CommandTree.sync` again.
6. `command_scope_dedupe` mutated the beta-sync env default and attached another `on_ready` listener for stale guild-copy cleanup.
7. `app.py` separately owned the actual slash-maintenance calls.

This mixed legitimate policy with process-wide framework mutation and allowed command registration to appear healthy after a missing command was silently dropped.

## Final native ownership design on this branch

Further implementation evidence showed `app.py` did not need a risky rewrite. Discord.py already allows the shared bot to own a custom command-tree class. The smallest complete design is therefore:

- `stoney_verify/globals.py` remains the one bot construction site and calls `create_discord_bot(...)`.
- New canonical `stoney_verify/command_runtime.py` owns only Dank Shield's bot/tree instances:
  - `DankBot` / `DankAutoShardedBot` choose sharding directly from `DISCORD_AUTO_SHARD` and optional `DISCORD_SHARD_COUNT`;
  - `DankCommandTree` owns global sync budget enforcement, public surface validation, unchanged-sync state, and configured guild-copy cleanup;
  - `_DankCommandOwnerMixin.setup_hook()` validates the final six-command/menu-first tree before `on_ready` and clears configured stale guild copies before normal ready maintenance;
  - documented `DANK_SYNC_BETA_GUILD_COMMANDS` default false is normalized by the canonical runtime instead of a startup guard;
  - documented `DANK_SKIP_UNCHANGED_GLOBAL_SYNC`, `DANK_FORCE_COMMAND_SYNC_ON_BOOT`, and `DANK_COMMAND_SYNC_STATE_FILE` behavior is live natively rather than stranded in a dormant patch file.
- `app.py` may continue calling `bot.tree.sync()` normally; because `bot.tree` is the one explicit `DankCommandTree`, policy applies only to this bot instance instead of replacing discord.py globally.
- The explicitly dangerous zero-command global wipe remains available only when both legacy clear and dangerous-clear flags are enabled.

## Implemented scope so far

### Native owner

Added `stoney_verify/command_runtime.py` with:

- explicit Bot vs AutoShardedBot construction;
- custom `DankCommandTree` instance ownership;
- canonical command-budget snapshot;
- public six-root + `/dank` direct-child fail-closed validation;
- command-surface hashing;
- persisted unchanged-sync state;
- configured stale guild-copy cleanup;
- public-safe beta-sync default normalization;
- setup-hook validation before ready.

### Canonical bot construction

Updated `stoney_verify/globals.py` so the shared bot is constructed through `create_discord_bot(...)`. It no longer depends on a process-wide replacement of `discord.ext.commands.Bot`.

### Retired runtime guards

The following files are now inert compatibility shims and no longer mutate discord.py or attach command cleanup listeners:

- `startup_guards/command_safety.py`
- `startup_guards/auto_shard.py`
- `startup_guards/global_command_sync.py`
- `startup_guards/command_scope_dedupe.py`

`command_safety` no longer imports the other two guards, so importing it cannot transitively reactivate command framework patches.

### Production boot cleanup

Updated `main.py` so production boot no longer imports `command_safety` or `command_scope_dedupe` at all.

Updated `startup_diagnostics.py` so the retired command guard modules are no longer treated as required production startup owners.

Removed migrated `command_safety` from the inert historical guard inventory.

Updated the explicit-main startup ownership tool and the old historical trace-loader assertion so they do not require migrated command guards back into production.

### Behavioral coverage added

Added `tests/test_command_runtime_native_ownership.py` covering:

- importing all four legacy command guard modules leaves `commands.Bot`, `CommandTree.add_command`, and `CommandTree.sync` unchanged;
- native Bot vs AutoShardedBot selection and custom `DankCommandTree` construction;
- fail-closed six-command/menu-first surface validation;
- real persisted unchanged-global-sync behavior;
- beta-guild sync default normalization without overriding an explicit operator choice.

## Correctness boundary

The old `CommandLimitReached` swallowing path is gone because production no longer imports the patch that intercepted `CommandTree.add_command`. A registrar can still catch an ordinary discord.py `CommandLimitReached` as a generic exception during additive module loading, but the canonical setup hook now validates the final required public tree before `on_ready`; any missing required root or `/dank` child raises and prevents normal startup from being reported ready.

Exact-head CI must prove this fail-closed boundary is sufficient. If evidence shows the registrar can lose a non-contract command without surfacing an actionable error, change only that proven boundary; do not broaden this finding into a full registrar redesign.

## Deliberately unchanged

- Existing Home/control-center menu design and destinations.
- The six approved global application commands and approved `/dank` direct children.
- `app.py` import order, API/worker startup, ticket/member startup maintenance, and normal `bot.tree.sync()` call sites.
- Ticket security, ticket panel persistence, verification, moderation, design, profile, setup, and feature callbacks behind the menu.
- Discord API retry/audit-log ownership, interaction action-lock ownership, guild-config safety, AntiNuke, schema, Supabase migrations, and DS-SEC-044.
- Broad `commands_ext` registrar consolidation / register-then-compact architecture. That remains a separate follow-up unless exact-head evidence proves it is required for this migration.
- Dormant `slash_command_cleanup.py` remains dormant; no patch from that file was reactivated.

## Validation plan / pending proof

- Compile the branch and prove discord.py 2.7.1 accepts the custom tree class for both Bot and AutoShardedBot.
- Run the new native ownership behavioral tests.
- Prove no production code assigns to `commands.Bot`, `CommandTree.add_command`, or `CommandTree.sync` after this migration.
- Prove the public surface remains exactly the six canonical application commands and `/dank` children `home`, `purge`, `setup`, `upload`.
- Prove public startup fails before ready on required surface drift.
- Prove intentional beta-guild sync remains possible when explicitly enabled.
- Prove stale guild copies are cleared only for configured cleanup IDs.
- Prove unchanged global sync state is honored natively.
- Run full pytest, standalone tools, public command/setup/invite safety audits, Claim-first security, Managed SQL, and every applicable companion workflow on one frozen exact PR head.
- Inspect exact file scope/diff, reviews, mergeability, and main drift before merge.
- After merge, require canonical main CI and Ticket Owner on the merge SHA, then gated Supabase promotion on that same SHA after CI success with immutable-main/status/preview/apply checks, and verify main remains unchanged afterward.

## Current blockers

None known. Local/PR CI has not yet validated the custom discord.py bot/tree construction, so the branch is not merge-ready.

## Next step

Open the implementation as a draft PR from the current branch, run exact-head CI, and fix only evidence-backed failures. Do not broaden into interaction locks, Discord API wrappers, feature registrar redesign, dormant guard-family deletion, or DS-SEC-044.
