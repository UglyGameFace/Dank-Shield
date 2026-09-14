# ACTIVE TASK

## DS-AUD-PROCESS-HEALTH — Move process health to explicit native boot ownership

**Status:** IN PROGRESS
**Branch:** `audit/process-health-native-ownership`
**Base main:** `f84549835335d2b0844ec61887f740d646c57094`

## Previous finding closure

`DS-AUD-RUNTIME-SAFETY` is closed and must not be reopened without new regression evidence.

Its bookkeeping closeout PR #224 merged as `f84549835335d2b0844ec61887f740d646c57094`. Acceptance on that exact canonical SHA passed:

- Dank Shield CI #2118 / `34880362833` — success, including the full repository unit suite, standalone tools, public setup/isolation, command-surface, startup-friction, invite-permissions, setup-safety, Dank Design, role-truth, event-boundary, Claim-first ticket security, and Managed category SQL checks.
- Ticket Owner Emergency Override #689 / `34880362840` — success on the same SHA.
- Deploy Supabase migrations #27 / `34881534753` — success on the same SHA.
  - canonical CI completed at 2026-09-14T18:33:34Z;
  - production promotion started afterward at 2026-09-14T18:33:37Z;
  - validated release checkout, immutable current-main verification, secrets, Supabase CLI, production link, migration status, preview, and apply all passed.
- Canonical `main` remained exactly `f84549835335d2b0844ec61887f740d646c57094` after promotion.
- No open PRs remained when this finding began.

## Finding

The remaining live startup ownership debt is `process_health`.

Production boot currently reaches it implicitly:

`main.py` imports `stoney_verify.startup_guards.*` → Python imports `stoney_verify.startup_guards.__init__` → package `__init__` imports `startup_guards.process_health` → `process_health.py` calls `install()` at module import time.

`install()` currently owns real process-health behavior, but also replaces `builtins.__import__` globally. That import hook exists only so `_safe_import()` can repeatedly call `_maybe_attach_loaded_bot()` after every Python import until `stoney_verify.app` or `stoney_verify.globals` exposes `bot`.

That is unnecessary hidden runtime ownership. `main.py` already owns boot order and can explicitly install process health and attach it to the known bot before importing/running the app.

## Root cause

A legitimate infrastructure service was implemented as a startup-guard package side effect and used a process-wide import interceptor to discover when its real dependency (`bot`) became available.

This couples unrelated Python imports to Discord health registration and makes `startup_guards` package import itself behaviorally significant.

Repository search confirms the following process-health behaviors are unique and must be preserved:

- `sys.excepthook` ownership for unhandled synchronous exceptions;
- asyncio loop exception handling;
- SIGTERM/SIGINT logging and clean `SystemExit` behavior;
- atexit process-exit logging;
- boot-count/restart visibility;
- current and peak RSS snapshots;
- operation-queue health snapshots;
- periodic health heartbeat task;
- external Healthchecks watchdog ping/status.

The problem is not those behaviors. The problem is package-level activation and global `builtins.__import__` replacement used for bot discovery.

## In scope

- Move process health out of `stoney_verify/startup_guards/` into a canonical infrastructure module under `stoney_verify/`.
- Preserve process exception, signal, atexit, boot-state, memory, operation-queue, heartbeat, and external-watchdog behavior.
- Remove the global import interceptor and implicit bot discovery.
- Make `main.py` explicitly install process-level health before startup-guard imports and explicitly attach health to the known bot before app import/login.
- Remove the `startup_guards` package-level process-health side effect and retired historical inventory entry.
- Update startup diagnostics to the canonical owner path.
- Update public status reporting to use the canonical health module.
- Update existing process-health test paths and add behavioral regression coverage for explicit ownership and the absence of a global import hook.
- Document the ownership migration and update architecture guardrails.

## Out of scope

- `command_safety`, command-tree wrappers, or other startup guards;
- dormant startup-guard deletion beyond the retired `process_health` file itself;
- AntiNuke runtime installers, hostile actor/re-entry work, or DS-SEC-044 acceptance;
- dual/dead implementation trees (`commands_new`, `db_new`, `tasks_new`, `core/`);
- Channel Builder cleanup;
- unrelated app, ticket, moderation, verification, schema, or Supabase behavior.

## Planned implementation

1. Create `stoney_verify/process_health.py` as the canonical owner.
   - Preserve the health/watchdog functionality.
   - Remove `builtins` import, `_ORIGINAL_IMPORT`, `_safe_import`, `_maybe_attach_loaded_bot`, and import-time `install()` activation.
   - Expose explicit `install_process_health()` and `attach_process_health(bot)` entrypoints.
   - Keep listener attachment idempotent.
   - Adjust the operation-queue relative import for the new module location.
2. Update `main.py` to install process-level health explicitly before startup-guard imports, then attach it to `stoney_verify.globals.bot` before the app is imported/run.
3. Remove the old package-level process-health import/export and historical inventory entry from `startup_guards/__init__.py`.
4. Update `startup_diagnostics.py` and `commands_ext/public_status_reporter.py` to the canonical module path.
5. Delete `startup_guards/process_health.py` only after all callers are migrated.
6. Update existing tests that intentionally exercise process-health behavior and add behavioral ownership tests.
7. Add `docs/PROCESS_HEALTH_NATIVE_OWNERSHIP_AUDIT.md` and update `CLAUDE.md`.

## Validation plan

- Inspect final diff against exact base `f84549835335d2b0844ec61887f740d646c57094` and verify only process-health ownership files changed.
- Behavioral subprocess proof that importing `stoney_verify.startup_guards` no longer changes `builtins.__import__` or loads the retired guard module.
- Behavioral subprocess proof that canonical process-health installation preserves `builtins.__import__` while installing the process exception hook.
- Behavioral fake-bot proof that explicit process-health attachment registers exactly one `on_ready` listener and remains idempotent.
- Existing current/peak RSS and external-watchdog tests remain valid against the canonical module.
- Startup diagnostics expect the canonical process-health owner and not the retired startup-guard path.
- Full Dank Shield CI, compile, repository pytest, standalone tools, public safety audits, Claim-first security, Managed SQL, and all applicable companion workflows must pass on one frozen exact PR head.
- Before merge: exact file scope, clean diff, no unresolved review threads, no main drift, mergeable, no temporary/debug/workflow debris.
- After merge: canonical main equals actual merge SHA; canonical CI and Ticket Owner pass on that SHA; gated Supabase promotion starts only after canonical CI and passes immutable-main/status/preview/apply; main remains unchanged afterward.

## Current evidence / blockers

- Canonical base is production-accepted and stable.
- No open PR conflicts existed when the branch was created.
- No blocker is currently known.
- Because this finding changes boot-order-sensitive infrastructure, no merge is allowed until exact-head full CI and post-merge acceptance prove the explicit owner behaves correctly.

## Next step

Implement the explicit process-health owner on this branch, validate it on a draft PR, and do not move to another master-audit finding until this one is merged, production-accepted, and repository-accurately closed.
