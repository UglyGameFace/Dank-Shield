# ACTIVE TASK

## DS-AUD-COMMAND-OWNERSHIP — Make the menu-first command surface canonical and retire command-tree/bot monkey patches

**Status:** IN PROGRESS — execution path mapped; implementation pending
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

Dank Shield already has the desired menu-first UX. This finding must preserve that product surface rather than create another control center.

Current canonical public application-command contract:

- `/dank`
- `/mod`
- `/ticket`
- `/tickets`
- `/verify`
- `View Dank Profile` context menu

The `/dank` group is intentionally compact. Its approved direct children are `home`, `purge`, `setup`, and `upload`; feature discovery and configuration belong behind the existing Home control-center UI.

The goal of this finding is to make that small menu-first surface the explicit runtime truth while removing hidden framework mutation underneath it.

## Current execution path and root cause

The user-facing menu is already compact, but command ownership is still layered:

1. `main.py` imports `startup_guards.command_safety`.
2. `command_safety` transitively imports `startup_guards.auto_shard` and `startup_guards.global_command_sync`.
3. `auto_shard` can globally replace `discord.ext.commands.Bot` with an `AutoShardedBot` subclass before `globals.py` creates the shared bot.
4. `global_command_sync` globally replaces `discord.app_commands.CommandTree.sync` to enforce a global sync limit.
5. `command_safety` globally replaces `CommandTree.add_command`, catches `CommandLimitReached`, records the skipped command, and returns `None`, allowing startup to continue with a missing user-facing command. It also wraps `CommandTree.sync` a second time for budget logging.
6. `main.py` separately imports `command_scope_dedupe`, which mutates the beta-sync env default and installs another `on_ready` listener for delayed guild-copy cleanup.
7. `app.py` already owns the actual slash-maintenance lifecycle and, outside `command_scope_dedupe`, is the only live runtime caller of `tree.sync()`.
8. `globals.py` contains the one canonical bot construction site.

The root cause is that legitimate bot-class selection, command-budget validation, sync policy, and duplicate-scope cleanup were implemented as startup patches around discord.py instead of being owned by the existing bot constructor and slash-maintenance path.

A second correctness problem exists in registration: `CommandLimitReached` can currently be swallowed so boot appears healthy even though a command disappeared. That violates the public-product requirement that users must not silently lose features.

## Evidence-backed native ownership target

- `stoney_verify/globals.py` owns Bot vs AutoShardedBot selection directly from `DISCORD_AUTO_SHARD` and optional `DISCORD_SHARD_COUNT`.
- A canonical non-guard command-runtime helper may own reusable command-surface hashing/budget/state helpers, but must not monkey-patch discord.py.
- `stoney_verify/app.py` owns when global/guild command sync happens, beta-guild sync defaults, stale configured guild-copy cleanup, and sync outcome logging.
- `stoney_verify/commands_ext/__init__.py` must never silently continue after `CommandLimitReached`; that error is fail-closed and visible.
- The existing `command_surface_contract.py` remains authoritative for the six-command public surface and compact `/dank` child set.

## Existing dormant behavior reviewed

`startup_guards/slash_command_cleanup.py` is not a live production owner, but it contains historical command-surface hashing and `DANK_SKIP_UNCHANGED_GLOBAL_SYNC` state-file behavior still advertised by `.env.example` and public launch documentation. During this finding, either migrate that useful behavior into the canonical command runtime or remove the false configuration contract. Do not reactivate the dormant guard or its monkey patches.

## Intended implementation scope

- `stoney_verify/globals.py`: direct Bot/AutoShardedBot construction; no `commands.Bot` class replacement.
- `stoney_verify/app.py`: native slash-sync/default/cleanup ownership.
- `stoney_verify/commands_ext/__init__.py`: fail closed on `CommandLimitReached` instead of tolerating a missing command.
- Add a canonical command-runtime helper only if needed to keep reusable non-patching budget/hash/state logic out of `app.py`.
- `main.py`: stop importing command ownership guards once their behavior is native.
- `startup_diagnostics.py`: remove retired command guard modules from the expected startup-owner contract.
- `startup_guards/__init__.py`: remove migrated command ownership from inert historical metadata where appropriate.
- Delete retired live owners after native behavior is covered:
  - `startup_guards/command_safety.py`
  - `startup_guards/auto_shard.py`
  - `startup_guards/global_command_sync.py`
  - `startup_guards/command_scope_dedupe.py`
- Update focused compatibility tests/tools that currently encode those modules as required startup owners.
- Add behavioral regression coverage for native bot selection, sync policy, unchanged-sync state, fail-closed command limits, stale guild-copy cleanup, and the unchanged six-command/menu-first contract.
- Add a focused ownership audit document and update `CLAUDE.md` to match the new boot contract.

## Deliberately unchanged

- Existing Home/control-center menu design and destinations.
- The six approved global application commands and approved `/dank` direct children.
- Ticket security, ticket panel persistence, verification, moderation, design, profile, setup, and feature callbacks behind the menu.
- Discord API retry/audit-log ownership, interaction action-lock ownership, guild-config safety, AntiNuke, schema, Supabase migrations, and DS-SEC-044.
- Broad `commands_ext` registrar consolidation beyond the specific `CommandLimitReached` correctness boundary. Register-then-compact cleanup remains a separate follow-up unless evidence proves it must change to complete this owner migration safely.

## Validation plan

- Prove discord.py 2.7.1 supports explicit Bot/AutoShardedBot construction without class replacement.
- Prove no production code assigns to `commands.Bot`, `CommandTree.add_command`, or `CommandTree.sync` after the migration.
- Prove beta guild sync defaults false without env mutation.
- Prove public global sync rejects command-budget overflow and public-surface drift visibly rather than returning a fake successful empty result.
- Prove intentional beta-guild sync still works when explicitly enabled.
- Prove stale guild command copies are only cleared for configured cleanup guild IDs when public cleanup is enabled.
- Prove unchanged global sync state is honored natively if the documented setting remains supported.
- Prove `CommandLimitReached` propagates/fails closed through the registrar.
- Prove canonical public roots remain exactly `dank`, `mod`, `ticket`, `tickets`, `verify`, plus `View Dank Profile`, and `/dank` keeps only `home`, `purge`, `setup`, `upload`.
- Run compile, full pytest, standalone tools, public command/setup/invite safety audits, Claim-first security, Managed SQL, and every applicable companion workflow on one frozen exact PR head.
- Before merge: exact file scope, clean diff, no unresolved reviews, no main drift, mergeable, no temporary/debug/workflow debris.
- After merge: canonical main equals the actual merge SHA; canonical CI and Ticket Owner pass on that SHA; gated Supabase promotion starts only after canonical CI, passes immutable-main/status/preview/apply, and main remains unchanged afterward.

## Current blockers

None known. Implementation has not yet been written on this branch.

## Next step

Implement native command/bot ownership from this exact base while preserving the existing menu-first contract. Validate behavior before opening the implementation PR; do not broaden into interaction locks, Discord API wrappers, dormant guard-family deletion, or feature registrar redesign.