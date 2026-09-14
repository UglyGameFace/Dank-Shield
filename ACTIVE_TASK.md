# ACTIVE TASK

## DS-AUD-PROCESS-HEALTH — Move process health to explicit native boot ownership

**Status:** IN PROGRESS — implementation written; exact-head validation pending
**Branch:** `audit/process-health-native-ownership`
**Base main:** `f84549835335d2b0844ec61887f740d646c57094`

## Previous finding closure

`DS-AUD-RUNTIME-SAFETY` is closed and must not be reopened without new regression evidence.

Its bookkeeping closeout PR #224 merged as `f84549835335d2b0844ec61887f740d646c57094`. Acceptance on that exact canonical SHA passed:

- Dank Shield CI #2118 / `34880362833` — success, including the full repository suite and all standard audits.
- Ticket Owner Emergency Override #689 / `34880362840` — success on the same SHA.
- Deploy Supabase migrations #27 / `34881534753` — success on the same SHA.
  - canonical CI completed at 2026-09-14T18:33:34Z;
  - production promotion started afterward at 2026-09-14T18:33:37Z;
  - immutable current-main verification, migration status, preview, and apply all passed.
- Canonical `main` remained exactly `f84549835335d2b0844ec61887f740d646c57094` after promotion.
- No open PRs remained when this finding began.

## Finding and execution path

Before this branch, production reached process health implicitly:

`main.py` imported `stoney_verify.startup_guards.*` → Python initialized `startup_guards/__init__.py` → package `__init__` imported `startup_guards.process_health` → `process_health.py` called `install()` at import time.

That `install()` owned legitimate process-health behavior, but it also replaced `builtins.__import__` globally. Every subsequent Python import flowed through `_safe_import()`, which repeatedly called `_maybe_attach_loaded_bot()` until `stoney_verify.app` or `stoney_verify.globals` exposed `bot`.

The import hook therefore existed as hidden bot discovery, not as import protection.

## Root cause

A legitimate infrastructure service was coupled to package import side effects and a process-wide import interceptor instead of receiving its dependency explicitly from the entrypoint that already owns boot order.

Repository search confirmed process health is the unique owner of real behavior that must remain:

- `sys.excepthook` unhandled synchronous exception visibility;
- asyncio loop exception visibility;
- SIGTERM/SIGINT logging and clean `SystemExit` behavior;
- atexit process-exit logging;
- boot/restart count visibility;
- current and peak RSS snapshots;
- operation-queue health snapshots;
- periodic process heartbeat logging;
- external Healthchecks watchdog ping/status.

The file path itself is not the correctness bug. After reviewing live consumers, the smallest complete migration keeps the stable internal module path `stoney_verify.startup_guards.process_health` while removing hidden activation and import interception. This avoids a path-only refactor and compatibility shim that would add risk without changing ownership.

## Implemented scope

- `stoney_verify/startup_guards/process_health.py`
  - removed `builtins` import interception;
  - removed `_ORIGINAL_IMPORT`, `_safe_import`, and `_maybe_attach_loaded_bot`;
  - removed import-time `install()` execution;
  - added explicit idempotent `install_process_health()`;
  - added explicit idempotent `attach_process_health(bot)`;
  - preserved process exception, signal, atexit, boot-state, memory, operation-queue, heartbeat, and external-watchdog behavior.
- `main.py`
  - explicitly calls `install_process_health()` before other startup guards;
  - explicitly attaches health to `stoney_verify.globals.bot` after login-backoff handling and before app import/login.
- `startup_guards/__init__.py`
  - no longer imports/activates process health;
  - removed the process-health side-effect export;
  - removed process health from the inert dormant historical inventory.
- Added behavioral regression coverage proving startup-guard package imports are inert, explicit installation never replaces Python imports, and bot attachment is idempotent.
- Added `docs/PROCESS_HEALTH_NATIVE_OWNERSHIP_AUDIT.md`.
- Updated `CLAUDE.md` with the explicit boot contract and ban on runtime discovery through `builtins.__import__`.

## Deliberately unchanged

- The process-health module remains at `stoney_verify.startup_guards.process_health` because current public status reporting and focused health tests already consume that internal path and location was not the root cause. No compatibility alias or duplicate module was added.
- `startup_diagnostics.py` remains correct because the live owner module path did not change.
- `commands_ext/public_status_reporter.py` remains correct and continues importing `external_watchdog_status` from the same module.
- Existing external-watchdog and current-vs-peak RSS tests continue exercising the same implementation path.
- `app.py`, tickets, verification, moderation, schema, Supabase, and product command behavior are untouched.

## Out of scope

- `command_safety`, command-tree wrappers, or other live guard debt;
- dormant startup-guard deletion/consolidation beyond removing migrated process health from the historical inventory;
- AntiNuke runtime installers, hostile actor/re-entry work, or DS-SEC-044 acceptance;
- dual/dead implementation trees (`commands_new`, `db_new`, `tasks_new`, `core/`);
- Channel Builder cleanup;
- unrelated app, ticket, moderation, verification, schema, or Supabase behavior.

## Validation plan

- Inspect branch diff against exact base `f84549835335d2b0844ec61887f740d646c57094` and confirm only process-health ownership/docs/tests/task files changed.
- Behavioral subprocess proof that importing `stoney_verify.startup_guards` leaves `builtins.__import__` unchanged and does not load process health.
- Behavioral subprocess proof that importing process health itself is inert, then `install_process_health()` installs the sync exception hook while still leaving `builtins.__import__` unchanged and remains idempotent.
- Behavioral fake-bot proof that `attach_process_health(bot)` registers exactly one `on_ready` listener and remains idempotent.
- Behavioral proof that process health is an expected explicit startup owner but no longer appears in the dormant historical inventory.
- Existing current/peak RSS and external-watchdog tests must remain green.
- Full Dank Shield CI, compile, repository pytest, standalone tools, public safety audits, Claim-first security, Managed SQL, and all applicable companion workflows must pass on one frozen exact PR head.
- Before merge: exact file scope, clean diff, no unresolved review threads, no main drift, mergeable, no temporary/debug/workflow debris.
- After merge: canonical main equals actual merge SHA; canonical CI and Ticket Owner pass on that SHA; gated Supabase promotion starts only after canonical CI and passes immutable-main/status/preview/apply; main remains unchanged afterward.

## Current blockers

No implementation blocker is known. Validation has not yet been run on a frozen PR head, so the finding is not ready or complete.

## Next step

Inspect the exact branch diff, open a draft PR, run exact-head validation, fix only evidence-backed failures, then merge and perform the full canonical production acceptance chain before closing this finding or selecting another audit item.
