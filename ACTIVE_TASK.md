# ACTIVE TASK

## DS-AUD-STARTUP-OWNERSHIP — Retire dead bulk startup loader and document real runtime ownership

**Status:** IMPLEMENTATION VALIDATED — FINAL BOOKKEEPING HEAD REVALIDATION REQUIRED
**Branch:** `audit/startup-guard-runtime-ownership`
**PR:** #221 — `Retire dormant startup guard bulk loader`
**Base / canonical main at task start:** `85cdac93bcb6fdd47243819ff8ea1a8ca4eada65`
**Validated implementation head:** `0a48b5b0d33c3465651467af932b3aa3c404a5a8`

## Outcome

Production startup ownership is explicit and auditable without reactivating the historical startup-guard bulk loader. Proven dead loader/host-hook machinery is retired; verified live owners remain intact; diagnostics report the real startup contract instead of registry fiction.

## Root cause / findings

- `load_startup_guards()` / `load_all_startup_guards()` had no normal production boot caller.
- The historical `_STARTUP_GUARDS` tuple contains 76 modules and is not runtime truth.
- The only remaining executable bulk-loader escape hatch was diagnostics `--load`; public `/dank diagnostics` did not use it.
- Blind activation would execute old monkey patches, listeners, command-tree mutations, compatibility layers, and schema-era code with duplicate-ownership risk.
- Direct `main.py` startup-guard ownership is exactly six modules: `discord_api_safety`, `command_safety`, `command_scope_dedupe`, `public_server_env_id_guard`, `guild_config_runtime_validator`, and `interaction_action_lock_guard`.
- `command_safety` transitively owns `auto_shard` and `global_command_sync`.
- Importing `stoney_verify.startup_guards` still intentionally owns `process_health`.
- `sitecustomize.py` owns `runtime_safety` / `public_startup_scope` and Basic Verify compatibility through `basic_verification_mode_guard` -> `id_verify_allowlist_guard` -> `unverified_ticket_panel_flow`.
- Canonical feature modules deliberately import selected startup-guard helpers; those imports do not justify a bulk loader.
- `usercustomize.py` -> `panel_menu_retry_guard` -> removed `public_ticket_panel_clean_hardening` was a proven dead host path.
- Channel Builder is directly wired through `stoney_verify.app` -> `api_new.server.start_api()` -> `register_channel_builder_routes(...)`; the old bridge remains removed.

## Implemented

- Removed executable `load_startup_guards()` / `load_all_startup_guards()` and loader-only state.
- Retained the 76-module inventory only as inert `LEGACY_DORMANT_STARTUP_GUARDS` historical metadata.
- Made startup diagnostics read-only over verified owners already present in `sys.modules`; diagnostics cannot activate missing guards.
- Retired diagnostics `--load` activation behavior.
- Removed obsolete bulk-loader alias compatibility from `sitecustomize.py`.
- Removed the dead `usercustomize.py` panel retry hook and deleted `panel_menu_retry_guard.py` after reference verification.
- Updated ticket-category and public-command audits to validate canonical live owners rather than historical registry membership.
- Added regression coverage requiring the exact six direct startup-guard owners in `main.py` and preventing loader/host-shim restoration.
- Added `docs/STARTUP_GUARD_RUNTIME_OWNERSHIP_AUDIT.md` with live boot, host, transitive, feature-owned, and dormant-family classification.
- Corrected `CLAUDE.md`, including the stale Channel Builder warning.
- Removed leftover unused AST audit machinery found during final diff review.

## Safety / cleanup evidence

- `main.py` boot order unchanged.
- `stoney_verify/app.py` import order unchanged.
- No schema migration, AntiNuke change, new startup guard, root `runtime_*_patch.py`, host hook, discord.py monkey patch, persistent-view owner, or `*_new` tree introduced.
- No dormant guard family bulk-activated or mass-deleted.
- Channel Builder direct route registration remains canonical with one installer.
- Live `process_health`, runtime-safety, command-tree, verification, ticket, invite, member-lifecycle, and Channel Builder feature-owned paths remain intentionally owned.
- PR scope is exactly 13 files and contains no unrelated project changes.
- PR #221 has no unresolved review threads.
- Canonical `main` remained `85cdac93bcb6fdd47243819ff8ea1a8ca4eada65` through the validated implementation/bookkeeping passes.

## Validation evidence already completed

Implementation head `0a48b5b0d33c3465651467af932b3aa3c404a5a8` passed the complete repository and companion gate, including Dank Shield CI #2107 / `34864527365`.

A subsequent bookkeeping head also passed the complete gate before this final record normalization:

- Dank Shield CI #2108 / `34865851160` — success.
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
- Ticket Category Menu Sanity #509 / `34865851300` — success.
- Dank Design Regression CI #374 / `34865851342` — success.
- Schema Authority SQL #39 / `34865851306` — success.
- Ticket Owner Emergency Override #679 / `34865851260` — success.
- Application Command Size Diagnostics #1124 / `34865851408` — success.
- DS Backlog 027 Validation #75 / `34865851149` — success.
- Profile Runtime Diagnostics #880 / `34865851325` — success.

The commit containing this head-stable task record is the final bookkeeping candidate. No further branch edits are permitted unless its exact-head validation finds a real defect. Its exact SHA and final validation run IDs will be recorded in PR #221 metadata/body, which does not mutate the branch head.

## Remaining risks / backlog

- Live explicitly owned legacy patch/import-hook behavior (`process_health`, `runtime_safety`, `public_startup_scope`, command-tree wrappers, selected feature helpers) remains separate architectural debt.
- Dormant guard files remain a future deletion/consolidation surface and require individual importer/supersession proof.
- Dual/dead implementation trees (`commands_new`, `db_new`, `tasks_new`, `core/`, etc.) remain separate audit work.
- Channel Builder stale runbook/workflow path references remain separate follow-up if still present.
- DS-SEC-044 hostile re-entry production acceptance remains suspended.

## Next step

Freeze this branch. Validate the exact current PR head with Dank Shield CI and all applicable companion workflows. If every workflow is green, re-fetch `main`, verify mergeability/review state/file scope, record the immutable final head and validation IDs in PR #221 metadata, mark the PR ready, and merge only that exact validated head through protected `main`. Then verify canonical post-merge CI, Ticket Owner Emergency where applicable, and the gated Deploy Supabase migrations workflow targets the exact same immutable merge SHA before closing this finding and advancing the master audit.