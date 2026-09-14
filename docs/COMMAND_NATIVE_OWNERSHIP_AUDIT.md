# Command / Bot Native Ownership Audit

## Scope

This audit covers the live Dank Shield command-tree ownership family that previously relied on:

- `startup_guards.command_safety`
- `startup_guards.auto_shard`
- `startup_guards.global_command_sync`
- `startup_guards.command_scope_dedupe`

It does not redesign the public command UX, feature callbacks, interaction locks, Discord API wrappers, ticket behavior, AntiNuke, or dormant startup-guard families.

## Public product contract preserved

The normal public application-command surface remains exactly:

- `/dank`
- `/mod`
- `/ticket`
- `/tickets`
- `/verify`
- `View Dank Profile` context menu

The direct `/dank` children remain exactly:

- `home`
- `purge`
- `setup`
- `upload`

Everything else remains reachable from the existing menu/control-center flows or the intentionally separate direct command families above.

## Old production ownership

Before this migration:

1. `main.py` imported `startup_guards.command_safety`.
2. `command_safety` transitively imported `auto_shard` and `global_command_sync`.
3. `auto_shard` could replace `discord.ext.commands.Bot` globally before `globals.py` created the bot.
4. `global_command_sync` replaced `discord.app_commands.CommandTree.sync` globally.
5. `command_safety` replaced `CommandTree.add_command` globally, swallowed `CommandLimitReached`, and wrapped `CommandTree.sync` again for budget logging.
6. `command_scope_dedupe` changed the beta-sync default and attached an additional ready listener for stale guild-copy cleanup.
7. `app.py` separately called the command tree's normal sync methods during ready maintenance.

Legitimate product policy therefore depended on import order and stacked framework mutation rather than one explicit bot/tree owner.

## Root correctness problems

### Global framework mutation

Changing `commands.Bot`, `CommandTree.add_command`, or `CommandTree.sync` affects every consumer in the Python process, including tests and unrelated future bot/tree instances. Ownership was wider than the product behavior being protected.

### Silent command loss

The old `command_safety` wrapper caught Discord's `CommandLimitReached`, logged it, returned `None`, and allowed boot to continue. That can produce an online bot whose visible command surface is incomplete.

### Split sync policy

Global sync limits, beta-copy cleanup, unchanged-sync state, and actual sync timing lived in different modules. `.env.example` advertised unchanged-sync settings whose implementation existed only in a dormant cleanup guard rather than the live owner.

## Native ownership after migration

### `stoney_verify/globals.py`

`globals.py` remains the single shared bot construction site and calls `create_discord_bot(...)` from `stoney_verify.command_runtime`.

No code replaces `discord.ext.commands.Bot` globally.

### `stoney_verify.command_runtime`

This canonical non-guard module owns Dank Shield's bot/tree instances only:

- `DankBot`
- `DankAutoShardedBot`
- `DankCommandTree`

`create_discord_bot(...)` selects the bot class from:

- `DISCORD_AUTO_SHARD`
- optional `DISCORD_SHARD_COUNT`

and injects `DankCommandTree` as that bot's tree class.

### Setup-hook ownership

Before normal `on_ready` maintenance, the native bot setup hook:

1. validates the final public command roots and `/dank` direct children;
2. fails startup if the required menu-first surface is incomplete;
3. clears stale guild-scoped copies only for configured cleanup guild IDs when public cleanup is enabled and beta sync is not explicitly enabled.

This closes the previous silent-missing-command failure mode without a global `add_command` wrapper.

### Tree-owned sync policy

`DankCommandTree.sync()` applies policy only to the shared Dank Shield tree:

- public command-surface validation;
- global command-budget enforcement;
- hard 100-command limit protection;
- configured `DANK_GLOBAL_COMMAND_SYNC_LIMIT` protection;
- optional `DANK_ALLOW_LARGE_GLOBAL_COMMAND_SYNC` override;
- native `DANK_SKIP_UNCHANGED_GLOBAL_SYNC` state-file behavior;
- native `DANK_FORCE_COMMAND_SYNC_ON_BOOT` override;
- `DANK_COMMAND_SYNC_STATE_FILE` persistence;
- configured stale guild-copy cleanup on guild sync.

The existing `app.py` sync call sites remain normal `bot.tree.sync(...)` calls. They now invoke the explicitly owned tree rather than a globally patched discord.py method.

### Beta-sync default

If `DANK_SYNC_BETA_GUILD_COMMANDS` is not explicitly configured, the canonical command runtime normalizes it to `false` before bot construction. Explicit operator choices are preserved.

## Retired guard behavior

The four historical modules remain temporarily as inert compatibility shims so old imports fail safely instead of reactivating behavior:

- `command_safety.py`
- `auto_shard.py`
- `global_command_sync.py`
- `command_scope_dedupe.py`

Importing them does not:

- replace `commands.Bot`;
- replace `CommandTree.add_command`;
- replace `CommandTree.sync`;
- attach a command cleanup `on_ready` listener;
- transitively activate other command guards.

Production `main.py` no longer imports `command_safety` or `command_scope_dedupe`.

## Dormant cleanup guard

`startup_guards/slash_command_cleanup.py` remains dormant and is not reactivated. Useful surface-hash / unchanged-sync behavior advertised by production configuration was reimplemented in the canonical command runtime without its global monkey patches.

## Regression contract

Exact-head validation for this migration must prove:

1. importing the four legacy guard modules leaves discord.py classes unchanged;
2. `create_discord_bot(...)` produces the native Bot or AutoShardedBot owner and a `DankCommandTree`;
3. the public six-command and four-child `/dank` contract passes when correct and fails closed on drift;
4. unchanged global sync state persists and is honored;
5. beta-sync defaults false unless explicitly overridden;
6. existing command surface, setup, invite, ticket, design, role, and event-boundary audits remain green;
7. no new startup guard or global framework monkey patch is introduced.

## Deliberately deferred

This audit does not consolidate the broad additive `commands_ext` registrar or remove every historical command-related file. Register-then-compact architecture is a separate product/architecture finding unless exact-head evidence shows it is required to keep this migration correct.
