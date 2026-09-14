# Process Health Native Ownership Audit

Base canonical main: `f84549835335d2b0844ec61887f740d646c57094`

Finding: `DS-AUD-PROCESS-HEALTH`

## Why this finding existed

`stoney_verify.startup_guards.process_health` contained useful production health behavior, but its activation model was hidden and process-wide.

Before this migration, normal boot reached process health only because importing any `stoney_verify.startup_guards.*` module first executed `startup_guards/__init__.py`. That package imported `process_health`, and `process_health.py` immediately called `install()`.

The old `install()` then replaced `builtins.__import__` globally. Every later Python import ran through `_safe_import()`, which repeatedly inspected `sys.modules` until `stoney_verify.app` or `stoney_verify.globals` exposed `bot`. Only then did it attach an `on_ready` listener.

The import hook was therefore not protecting imports. It was a bot-discovery mechanism hidden inside process health.

## Behavior that is real and must remain

Repository search confirmed process health is the unique owner of several legitimate infrastructure protections:

- unhandled synchronous exception visibility through `sys.excepthook`;
- asyncio loop exception visibility;
- SIGTERM/SIGINT shutdown logging and clean `SystemExit` behavior;
- atexit process-exit logging;
- boot/restart count visibility;
- current RSS and peak RSS reporting;
- operation-queue health snapshots;
- periodic process heartbeat logging;
- optional external Healthchecks watchdog ping/status.

Those behaviors are retained.

## Native ownership after this migration

`main.py` is the explicit owner of process-health activation.

At module boot, before the rest of the startup guards, it calls:

- `install_process_health()` for process-level exception/signal/exit/boot visibility.

After login-backoff handling and before application import/login, it resolves the canonical shared bot and calls:

- `attach_process_health(bot)` for the Discord `on_ready` health listener.

`startup_guards/__init__.py` no longer imports process health. Importing the startup-guard package therefore does not install process hooks or register health listeners.

The implementation remains at `stoney_verify.startup_guards.process_health` in this finding. The path is already consumed by the public status reporter and focused health tests, and the file location itself was not the correctness problem. Ownership and activation are now explicit without introducing path churn or a compatibility shim. A later file-organization cleanup may move it only if there is separate value in doing so.

## Removed behavior

The following old mechanisms are retired:

- `builtins.__import__` replacement;
- `_ORIGINAL_IMPORT` chaining;
- `_safe_import()` post-import interception;
- `_maybe_attach_loaded_bot()` sys.modules polling;
- import-time `install()` execution;
- package-level `start_process_health_loop` side-effect export from `startup_guards/__init__.py`;
- process health membership in the inert dormant startup-guard inventory.

No replacement import hook or fallback discovery layer was added.

## Regression contract

Behavioral coverage must prove:

1. importing `stoney_verify.startup_guards` leaves `builtins.__import__` unchanged and does not load process health;
2. importing process health leaves `builtins.__import__` unchanged and does not install the sync exception hook;
3. explicit `install_process_health()` installs the sync exception hook while still leaving Python imports untouched and is idempotent;
4. explicit `attach_process_health(bot)` registers one `on_ready` listener and is idempotent;
5. startup diagnostics still recognize process health as an explicit live owner, while the dormant historical inventory does not list it;
6. existing external-watchdog and current-vs-peak memory behavior remains intact;
7. full repository CI and post-merge production acceptance pass on one exact immutable SHA.

## Scope boundary

This finding does not alter command-tree safety wrappers, other startup guards, AntiNuke runtime installers, tickets, verification, schema, Supabase behavior, dual/dead trees, or suspended DS-SEC-044 production acceptance.

The next master-audit finding must be selected from current canonical repository evidence only after this migration is merged and production-accepted.
