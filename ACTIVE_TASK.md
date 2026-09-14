# ACTIVE TASK

## DS-AUD-STARTUP-OWNERSHIP — Retire dead bulk startup loader and document real runtime ownership

**Status:** IMPLEMENTATION VALIDATED — FINAL BOOKKEEPING HEAD REVALIDATION REQUIRED
**Branch:** `audit/startup-guard-runtime-ownership`
**PR:** #221 — `Retire dormant startup guard bulk loader`
**Base / canonical main at task start:** `85cdac93bcb6fdd47243819ff8ea1a8ca4eada65`
**Validated implementation head:** `0a48b5b0d33c3465651467af932b3aa3c404a5a8`

## Previous finding acceptance

Persistent interaction compatibility is CLOSED after PR #216, closeout PR #220, canonical CI #2104, Ticket Owner Emergency Override #675, and gated Deploy Supabase migrations #23 all succeeded on canonical `main` `85cdac93bcb6fdd47243819ff8ea1a8ca4eada65`.

## Outcome

Make production startup ownership explicit and safe without reactivating the historical startup-guard bulk loader. Remove only proven dead loader/host-hook machinery, preserve verified live owners, and make diagnostics report the real startup contract instead of registry fiction.

## Root cause / findings

- `load_startup_guards()` / `load_all_startup_guards()` had no normal production boot caller.
- The historical `_STARTUP_GUARDS` tuple contains 76 modules and is not runtime truth.
- The only remaining executable bulk-loader escape hatch was diagnostics `--load`; public `/dank diagnostics` did not use it.
- Blindly activating the tuple would run old monkey patches, listeners, command-tree mutations, compatibility layers, and schema-era code with duplicate-ownership risk.
- Direct `main.py` startup-guard ownership is exactly:
  - `discord_api_safety`
  - `command_safety`
  - `command_scope_dedupe`
  - `public_server_env_id_guard`
  - `guild_config_runtime_validator`
  - `interaction_action_lock_guard`
- `command_safety` transitively owns `auto_shard` and `global_command_sync`.
- Importing `stoney_verify.startup_guards` still owns the intentionally preserved `process_health` package side effect.
- `sitecustomize.py` owns `runtime_safety` / `public_startup_scope` and Basic Verify compatibility through `basic_verification_mode_guard` -> `id_verify_allowlist_guard` -> `unverified_ticket_panel_flow`.
- Canonical feature modules deliberately import selected startup-guard helpers; those feature-owned imports are not evidence for a bulk loader.
- `usercustomize.py` -> `panel_menu_retry_guard` -> removed `public_ticket_panel_clean_hardening` was a proven dead host path.
- Channel Builder is already directly wired through `stoney_verify.app` -> `api_new.server.start_api()` -> `register_channel_builder_routes(...)`; the old `channel_builder_api_guard` bridge is removed and must stay removed.

## Implemented

- Removed executable `load_startup_guards()` / `load_all_startup_guards()` and loader-only state from `stoney_verify/startup_guards/__init__.py`.
- Retained the old 76-module list only as `LEGACY_DORMANT_STARTUP_GUARDS` historical metadata; nothing iterates it during normal boot.
- Reworked startup diagnostics to observe verified startup owners already present in `sys.modules`; diagnostics never import missing guards as a repair action.
- Retired the diagnostics `--load` activation behavior; the flag is read-only/inert.
- Removed obsolete bulk-loader alias compatibility from `sitecustomize.py`.
- Removed the dead `usercustomize.py` panel retry hook and deleted `panel_menu_retry_guard.py` after reference/owner verification.
- Updated ticket-category and public-command audits so they validate canonical live owners instead of historical registry membership.
- Added regression coverage that requires the exact six direct startup-guard owners in `main.py` and prevents the retired loader/host shim from returning.
- Added `docs/STARTUP_GUARD_RUNTIME_OWNERSHIP_AUDIT.md` with live boot, host, transitive, feature-owned, and dormant-family classification.
- Corrected `CLAUDE.md`, including the stale Channel Builder warning.
- Removed leftover unused AST audit machinery discovered during final diff review.

## Safety / cleanup evidence

- `main.py` boot order was not changed.
- `stoney_verify/app.py` import order was not changed.
- No schema migration was added or modified.
- No AntiNuke / DS-SEC-044 behavior was changed.
- No new startup guard, root `runtime_*_patch.py`, host hook, discord.py monkey patch, persistent-view owner, or `*_new` tree was introduced.
- No dormant guard family was bulk-activated or mass-deleted.
- Channel Builder direct route registration remains canonical and has one installer.
- The live `process_health`, runtime-safety, command-tree, verification, ticket, invite, member-lifecycle, and Channel Builder feature-owned paths identified by the audit remain intentionally owned.
- PR #221 has no unresolved review threads.
- Canonical `main` was re-fetched during validation and remained `85cdac93bcb6fdd47243819ff8ea1a8ca4eada65`.

## Exact implementation-head validation

Validated implementation head: `0a48b5b0d33c3465651467af932b3aa3c404a5a8`.

All workflows on that exact head succeeded:

- Dank Shield CI #2107 / `34864527365` — success.
  - committed diff whitespace — success.
  - Python compile — success.
  - full repository unit test suite — success.
  - standalone `tools/test_*.py` checks — success.
  - public setup/isolation audit — success.
  - canonical public command-surface audit — success.
  - public command/startup-friction audit — success.
  - public invite permissions audit — success.
  - setup safety audit — success.
  - Dank Design Smart Auto-Detect audit — success.
  - role-truth ownership audit — success.
  - event-boundary ownership audit — success.
  - Claim-first ticket security — success.
  - Managed category SQL smoke test — success.
- Ticket Category Menu Sanity #508 / `34864527314` — success.
- Dank Design Regression CI #373 / `34864527068` — success.
- Schema Authority SQL #38 / `34864527393` — success.
- Ticket Owner Emergency Override #678 / `34864527353` — success.
- Application Command Size Diagnostics #1123 / `34864527099` — success.
- DS Backlog 027 Validation #74 / `34864526991` — success.
- Profile Runtime Diagnostics #879 / `34864527142` — success.

This `ACTIVE_TASK.md` bookkeeping commit changes the PR head. The new bookkeeping head must therefore pass exact-head CI and all applicable companion workflows before PR #221 is marked ready or merged.

## Remaining blockers / risks

- Live production still contains explicitly owned legacy patch/import-hook behavior (`process_health`, `runtime_safety`, `public_startup_scope`, command-tree wrappers, and selected feature helpers). They are real live paths and belong to separate owner-migration audits, not this loader-retirement PR.
- The historical dormant guard inventory remains a future deletion/consolidation surface; files must be proven unused/superseded individually before removal.
- DS-SEC-044 hostile re-entry production acceptance remains suspended and is not part of this task.

## Backlog after this finding closes

- Audit/consolidate remaining live monkey-patch/import-hook owners into canonical modules one subsystem at a time.
- Audit dual/dead implementation trees (`commands_new`, `db_new`, `tasks_new`, `core/`, and other parallel trees).
- Retire dormant guard files only with importer/supersession evidence.
- Review stale Channel Builder runbook/workflow path references as a separate product/runtime task if still unresolved.

## Next step

Revalidate the exact bookkeeping head created by this record. If every required and companion workflow is green, re-fetch `main`, verify mergeability/review state/file scope, mark PR #221 ready, and merge only that exact validated head through protected `main`. Then verify canonical post-merge CI, Ticket Owner Emergency where applicable, and the gated Deploy Supabase migrations workflow targets the same immutable canonical merge SHA before closing this finding and advancing the master audit.