# ACTIVE TASK

## DS-AUD-PROCESS-HEALTH — Move process health to explicit native boot ownership

**Status:** CLOSED — implementation merge and production acceptance passed; bookkeeping closeout is being merged through protected `main`
**Implementation PR:** #225 — `Make process health an explicit boot owner`
**Validated final PR head:** `318c2adfe4c1d45d6f6caafbc8d2fdfeb1baa7f8`
**Canonical implementation merge:** `5ce360654892f25117b1d04d9df52e54a9707888`

## Outcome

The process-health service remains at its stable internal module path, `stoney_verify.startup_guards.process_health`, but it no longer owns production through hidden package side effects or a global Python import interceptor.

`main.py` is now the explicit boot owner:

- it calls `install_process_health()` before the other startup guards;
- it explicitly attaches process health to the known shared Discord bot;
- importing `stoney_verify.startup_guards` no longer imports or activates process health;
- importing the process-health module alone is inert;
- `builtins.__import__` is no longer replaced or used for bot discovery.

The real service behavior was preserved: synchronous and asyncio exception visibility, SIGTERM/SIGINT handling, atexit logging, boot/restart count, current/peak RSS, operation-queue health, periodic process heartbeat, and the external Healthchecks watchdog.

## Root cause

A legitimate infrastructure service was coupled to `startup_guards` package initialization and a process-wide `builtins.__import__` wrapper solely so it could poll `sys.modules` until `stoney_verify.app` or `stoney_verify.globals` exposed `bot`.

The entrypoint already owns boot order and the bot dependency is known explicitly, so global import interception was unnecessary hidden ownership.

## Final implementation scope

Exactly seven files changed in PR #225:

1. `ACTIVE_TASK.md`
2. `CLAUDE.md`
3. `docs/PROCESS_HEALTH_NATIVE_OWNERSHIP_AUDIT.md`
4. `main.py`
5. `stoney_verify/startup_guards/__init__.py`
6. `stoney_verify/startup_guards/process_health.py`
7. `tests/test_process_health_native_ownership.py`

No app, ticket, verification, moderation, schema, Supabase migration, AntiNuke, or product-command implementation was changed.

## Exact-head pre-merge validation

Frozen PR head `318c2adfe4c1d45d6f6caafbc8d2fdfeb1baa7f8` passed the complete applicable gate:

- Dank Shield CI #2119 / `34886315545` — success.
  - committed diff whitespace — success;
  - Python compile — success;
  - full repository unit suite — success;
  - standalone tool checks — success;
  - public setup/isolation — success;
  - canonical public command surface — success;
  - public command/startup friction — success;
  - public invite permissions — success;
  - setup safety — success;
  - Dank Design Smart Auto-Detect — success;
  - role-truth ownership — success;
  - event-boundary ownership — success;
  - Claim-first ticket security — success;
  - Managed category SQL smoke — success.
- Schema Authority SQL #46 / `34886315429` — success.
- Dank Design Regression CI #381 / `34886315441` — success.
- Ticket Category Menu Sanity #518 / `34886315660` — success.
- Application Command Size Diagnostics #1131 / `34886315440` — success.
- Ticket Owner Emergency Override #690 / `34886315476` — success.
- Profile Runtime Diagnostics #887 / `34886315585` — success.
- PR remained exactly seven intended files, mergeable, with no review threads or human review blocker.
- Canonical `main` remained exact base `f84549835335d2b0844ec61887f740d646c57094` before merge.

## Post-merge production acceptance

PR #225 merged through protected `main` as `5ce360654892f25117b1d04d9df52e54a9707888`.

Acceptance on that exact canonical SHA passed:

- Dank Shield CI #2120 / `34887880599` — success.
  - full repository unit suite and every standard audit step passed;
  - CI completed at `2026-09-14T19:47:19Z`.
- Schema Authority SQL #47 / `34887880531` — success on the same SHA.
- Ticket Owner Emergency Override #691 / `34887880511` — success on the same SHA.
- Ticket Category Menu Sanity #519 / `34887880570` — success on the same SHA.
- Deploy Supabase migrations #28 / `34889012749` — success on the same SHA.
  - promotion started at `2026-09-14T19:47:21Z`, after canonical CI completed successfully;
  - validated release checkout — success;
  - immutable current-main target verification — success;
  - required secrets — success;
  - Supabase CLI — success;
  - production project link — success;
  - migration status — success;
  - migration preview — success;
  - apply pending migrations — success.
- Canonical `main` was re-fetched after promotion and remained exactly `5ce360654892f25117b1d04d9df52e54a9707888`.

The process-health ownership finding is closed. Do not reopen it without new regression evidence.

## Remaining master-audit backlog

The remaining work is separate from process health and must continue one finding at a time from current repository evidence.

### 1. Command-tree / slash-command ownership

Current live ownership still includes stacked command-tree behavior:

- `startup_guards.command_safety` wraps `discord.app_commands.CommandTree.add_command` and `CommandTree.sync`;
- `startup_guards.global_command_sync` wraps `CommandTree.sync` again;
- `startup_guards.auto_shard` can replace `discord.ext.commands.Bot` with an `AutoShardedBot` subclass when enabled;
- `startup_guards.command_scope_dedupe` owns beta-sync defaults and late guild-copy cleanup.

This is the strongest next candidate. Preserve valid command-budget, safe-sync, shard-selection, and duplicate-scope behavior while moving it into canonical command/bot construction owners instead of stacked Discord.py monkey patches.

### 2. Interaction/UI action-lock ownership

`startup_guards.interaction_action_lock_guard` currently patches private Discord.py `discord.ui.View._scheduled_task` to observe or block duplicate component actions. Audit and migrate valid idempotency behavior into explicit component/action owners without depending on a private library method.

### 3. Dormant startup-guard consolidation

The historical startup inventory still contains many old compatibility files. Remove them only by verified family after proving import reachability, current canonical ownership, and regression safety. Do not mass-delete by filename.

Families still requiring dedicated evidence include command-surface compatibility, invite/spam, member/role/modlog, verification, ticket/VC, setup, and schema/config/queue helpers.

### 4. Parallel/dead implementation-tree audit

Current evidence corrects an older stale note:

- `events_new` is live and imported by current runtime paths;
- `tasks_new` is live through current staff/worker paths;
- `setup_new` is live through public setup recommendation flows.

They are **not** deletion candidates merely because of the `_new` suffix.

Still-unresolved dead/parallel candidates include `commands_new`, `db_new`, `core/`, and `utils_new`, subject to exact importer and behavior proof before consolidation/deletion. Active canonical trees such as `api_new`, `members_new`, `moderation_new`, `tickets_new`, and `verification_new` must remain protected.

### 5. Feature-owned compatibility/helper cleanup

Audit remaining helpers that are imported by canonical features rather than bulk startup loading, including Basic Verify compatibility, ticket forms/category helpers, setup permission repair, member lifecycle helpers, invite sanitization, and the Channel Builder font/helper chain. Preserve real behavior, retire superseded patch ownership.

A current unfinished product item also remains in `services/invite_cleanup_service.py`: configured `allowed_codes` are still represented by an empty set with `TODO: load from guild config later`. That requires a product-correctness pass rather than blind cleanup.

### 6. Dank Design behavioral coverage / architecture cleanup

`commands_ext/public_design_studio.py` remains a churn-prone subsystem. Rebuild/strengthen behavioral coverage before structural cleanup; do not restore brittle static source-shape tests.

### 7. Stale architecture/runbook truth

Some older audit/readiness documents now contradict current runtime evidence, including outdated claims about which `_new` trees are dead and blockers already resolved by later work. Reconcile documentation only after the corresponding runtime owners are proven so documentation follows code truth rather than steering it incorrectly.

## Separately suspended

- `DS-SEC-044` hostile re-entry production acceptance remains separately suspended and must not be resumed without explicit authorization.

## Next step

Merge this bookkeeping-only closeout through protected `main`, verify canonical post-closeout CI and gated production promotion on the exact closeout merge SHA, then re-fetch current `main` and open the next master-audit finding from live evidence. The leading candidate is the command-tree/slash-command ownership family, but selection must be confirmed against the post-closeout canonical repository before implementation begins.
