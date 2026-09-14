# ACTIVE TASK

## DS-AUD-STARTUP-OWNERSHIP — Retire dead bulk startup loader and document real runtime ownership

**Status:** IN PROGRESS — INSPECTION COMPLETE, IMPLEMENTATION NOT YET VALIDATED
**Branch:** `audit/startup-guard-runtime-ownership`
**Base / canonical main at task start:** `85cdac93bcb6fdd47243819ff8ea1a8ca4eada65`
**Previous finding:** Persistent interaction compatibility — CLOSED after PR #216 + closeout PR #220 and post-closeout production acceptance.

## Previous finding final acceptance

PR #220 merged as canonical `main` `85cdac93bcb6fdd47243819ff8ea1a8ca4eada65`.

Post-closeout acceptance on that exact SHA:

- Dank Shield CI #2104 / `34859998681` — success.
  - Python compile check — success.
  - Full unit suite — success.
  - Standalone `tools/test_*.py` checks — success.
  - Public setup/isolation, command-surface, startup-friction, invite-permissions, setup-safety, Dank Design, role-truth, event-boundary audits — success.
  - Claim-first ticket security — success.
  - Managed category SQL smoke test — success.
- Ticket Owner Emergency Override #675 / `34859998737` — success on the same SHA.
- Deploy Supabase migrations #23 / `34860949428` — success.
  - Triggered by `workflow_run` only after CI #2104 completed successfully.
  - Targeted the same canonical SHA.
  - Immutable current-main target verification — success.
  - Migration status, preview, and apply — success.
- `main` was re-fetched after promotion verification and had not moved.

The persistent-interaction finding is therefore closed and must not be reopened without new regression evidence.

## Outcome

Determine and make explicit which startup-guard modules actually execute in production, formally retire the dangerous dormant bulk-loader mechanism, remove stale host-hook ownership that can no longer work, and make diagnostics report the real explicit startup contract instead of pretending every module in a historical registry should be loaded.

This task must not reactivate the historical bulk loader or broaden startup behavior.

## Scope

In scope:

- `main.py` explicit startup-guard imports.
- `sitecustomize.py` and `usercustomize.py` host-auto-import behavior.
- `stoney_verify/app.py` and the command/API import paths that transitively load startup-guard helpers.
- `stoney_verify/startup_guards/__init__.py` bulk-loader machinery and historical registry.
- `stoney_verify/startup_diagnostics.py` and its public `/dank diagnostics` consumer.
- Stale tests/tools that encode the false assumption that registry membership equals runtime activation.
- Documentation needed to stop future code from reactivating the dead loader.

Out of scope unless required for correctness of this finding:

- AntiNuke / DS-SEC-044.
- Full Channel Builder product redesign.
- Dank Design restructuring.
- Broad deletion of every dormant guard file.
- `commands_new`, `db_new`, `tasks_new`, `core/`, or other dual implementation tree cleanup.
- Schema changes.

## Inspection findings / root cause

### 1. The bulk loader is not a production boot owner

`load_startup_guards()` / `load_all_startup_guards()` has no normal boot call from `main.py`, `app.py`, `sitecustomize.py`, or `usercustomize.py`.

The only executable non-test path that can invoke the bulk loader is the diagnostic CLI path in `stoney_verify/startup_diagnostics.py` when explicitly run with `--load` / `load_missing=True`. The public `/dank diagnostics` command calls diagnostics with loading disabled.

### 2. `_STARTUP_GUARDS` is a historical activation list, not runtime truth

The current tuple contains 76 modules and mixes:

- modules that are independently live through explicit owners;
- modules loaded only transitively by a live owner;
- old monkey patches / compatibility layers;
- schema bootstrap guards;
- command-tree/listener/API patchers;
- modules that have no current live importer.

The loader records `_LOADED` only when *it* imports a module. Explicitly live modules imported by `main.py`, host hooks, command modules, or API modules are therefore invisible to that bookkeeping. `/dank diagnostics` consequently reports loader state, not actual runtime ownership.

Blindly reactivating the loader would execute many dormant side effects and can create duplicate command/listener/import-hook ownership.

### 3. Real live boot roots are explicit and much smaller

Direct `main.py` startup owners:

- `stoney_verify.startup_guards.discord_api_safety`
- `stoney_verify.startup_guards.command_safety`
- `stoney_verify.startup_guards.command_scope_dedupe`
- `stoney_verify.startup_guards.public_server_env_id_guard`
- `stoney_verify.startup_guards.guild_config_runtime_validator`
- `stoney_verify.startup_guards.interaction_action_lock_guard`

Transitive from `command_safety`:

- `stoney_verify.startup_guards.auto_shard`
- `stoney_verify.startup_guards.global_command_sync`

Package/host-owned paths:

- importing `stoney_verify.startup_guards` imports `process_health`; that module installs its process/import/signal/ready-listener safety at import time.
- `sitecustomize.py` imports `runtime_safety` and calls `load_runtime_safety()`.
- `runtime_safety` transitively imports `public_startup_scope`.
- `sitecustomize.py` imports and applies `basic_verification_mode_guard`.
- `basic_verification_mode_guard` imports/applies `id_verify_allowlist_guard`.
- `id_verify_allowlist_guard` imports `unverified_ticket_panel_flow` as part of the canonical ID-ticket compatibility path.

Live feature-owned startup-guard modules also exist outside the bulk loader contract. Examples confirmed from current public/runtime import paths:

- `commands_ext/public_member_lifecycle_runtime.py` installs `member_lifecycle_router_guard`.
- `commands_ext/public_setup_compact.py` imports `ticket_category_setup_guard` as the managed ticket-category owner.
- `commands_ext/public_ticket_panel_clean.py` uses `ticket_forms_foundation_guard` for ticket-form behavior.
- `api_new/channel_builder_routes.py` imports/applies `channel_builder_full_font_catalog_guard` and uses `setup_channel_font_mode_guard`.
- `channel_builder_full_font_catalog_guard` loads the queue-backed rename and menu-clarity helpers.
- invite cleanup/policy, setup repair, spam setup, and join-removal code import specific startup-guard helper modules on demand.

These are feature ownership paths, not evidence that the bulk loader should exist.

### 4. `sitecustomize.py` contains obsolete bulk-loader compatibility code

`sitecustomize.py` imports `stoney_verify.startup_guards` and conditionally aliases `load_all_startup_guards = load_startup_guards`.

Current `startup_guards/__init__.py` already defines that alias, and no production path should call either function. This block preserves the wrong architecture and is safe to remove once the bulk loader is retired.

### 5. `usercustomize.py` contains a proven dead host-hook path

`usercustomize.py` dynamically imports `panel_menu_retry_guard`.

`panel_menu_retry_guard` immediately depends on `public_ticket_panel_clean_hardening`, but that module was intentionally removed by DS-BACKLOG-027 after its behavior moved into the canonical ticket-panel owner. Existing ticket audits explicitly require that removed file to stay absent.

Therefore the `usercustomize` retry hook cannot successfully install today. Removing that stale hook and its now-unreachable guard changes no working production behavior and removes a misleading host-auto-import owner.

### 6. Channel Builder is wired today; the handoff/CLAUDE warning is stale

Current `main` contradicts the old architectural warning:

- `stoney_verify/startup_guards/channel_builder_api_guard.py` is already removed.
- `stoney_verify/api_new/server.py` directly imports `register_channel_builder_routes`.
- `start_api()` directly calls `register_channel_builder_routes(app, sys.modules[__name__])`.
- `stoney_verify/app.py` calls `start_api(bot)`.
- `tools/audit_channel_builder_queue.py` requires direct registration and explicitly requires the obsolete Channel Builder bridge guards/patcher/workflow to remain absent.

Channel Builder is therefore **not** currently dependent on the dormant bulk loader. Full Channel Builder product work remains separate from this task.

## Dormant bulk-loader families

The historical registry includes broad dormant families whose activation must remain prohibited unless a future dedicated audit establishes an explicit owner:

- schema/bootstrap and config mutation guards;
- legacy command-surface and command-pruning guards;
- Spam Guard/invite compatibility patches;
- old member lifecycle/modlog compatibility patches;
- protection-center/import/setup compatibility layers;
- legacy ticket/category/sync/panel compatibility patches;
- VC compatibility patches;
- event/shard/job-dedupe import-hook patches;
- old panel/bootstrap/runtime compatibility modules.

Some files in these families may still be imported deliberately by a feature owner. This task will not mass-delete files from naming or registry membership alone.

## Implementation plan

1. Retire the executable bulk-loader API from `startup_guards/__init__.py`.
2. Keep the old module list, if retained at all, as clearly named inert historical metadata only. It must not be callable as an activation plan.
3. Preserve currently live explicit owners; do not add a new guard or activate a dormant one.
4. Remove the obsolete bulk-loader compatibility alias block from `sitecustomize.py`.
5. Remove the dead `panel_menu_retry_guard` host hook from `usercustomize.py` and retire that guard file only after reference verification.
6. Rewrite startup diagnostics to inspect the explicit required boot-owner modules already present in `sys.modules`. Diagnostics must never import missing guards as a repair action.
7. Update behavioral/static regression tests so they enforce explicit ownership and loader retirement rather than registry activation.
8. Correct stale architecture documentation, including the already-direct Channel Builder route ownership.
9. Do not combine broader dormant-file deletion or Channel Builder product changes into this PR.

## Validation plan

Required before completion:

- focused startup ownership/diagnostics tests;
- explicit proof that bulk loader symbols are absent and cannot be invoked;
- explicit proof that `main.py` retains only the intended direct startup modules;
- explicit proof that host hooks do not import the retired loader or dead panel retry shim;
- Channel Builder direct-registration audit;
- ticket category/ticket-panel audits affected by the stale shim cleanup;
- `python -m compileall`;
- `git diff --check`;
- full repository pytest suite;
- standalone `tools/test_*.py` checks;
- public setup/isolation audit;
- canonical public command-surface audit;
- command/startup-friction audit;
- invite permissions audit;
- setup safety audit;
- Dank Design audit;
- role-truth audit;
- event-boundary audit;
- Claim-first ticket security;
- Managed category SQL smoke test;
- all applicable companion workflows.

Also prove no duplicate listener, persistent-view, command-tree, import-hook, or API-route owner is introduced and no public command disappears.

## Cleanup / conflicts

- No code change has reactivated the dormant loader.
- No new startup guard, root `runtime_*_patch.py`, host hook, discord.py monkey patch, schema migration, or `*_new` tree is permitted by this task.
- Channel Builder direct registration is already canonical and must not be rewired through startup guards.
- The dormant guard-file tree remains a later cleanup surface after ownership is proven module-by-module.

## Blockers / risks

- Live production still contains several explicit legacy monkey-patch owners (`runtime_safety`, command wrappers, process-health import hook). They are real live paths and must not be deleted merely because the bulk loader is retired.
- `startup_guards` package import currently has a `process_health` side effect. This task will preserve that behavior unless a separate validated migration proves an equivalent explicit owner and boot-order safety.
- Host-hook import ordering is delicate; changes are limited to provably obsolete loader/panel-retry paths.

## Backlog

- Separate follow-up: audit/consolidate remaining live monkey-patch owners into their canonical modules where safe.
- Separate follow-up: dual/dead implementation trees (`commands_new`, `db_new`, `tasks_new`, `core/`, etc.).
- Separate follow-up: dormant guard-file deletion by proven no-import/supersession evidence.
- Separate follow-up: stale Channel Builder runbook/workflow path filters beyond what is necessary to correct this finding.
- DS-SEC-044 hostile re-entry production acceptance remains suspended.

## Next step

Implement the smallest loader-retirement/host-hook cleanup described above, add focused ownership regressions, then validate the exact branch head before opening or advancing the PR.
