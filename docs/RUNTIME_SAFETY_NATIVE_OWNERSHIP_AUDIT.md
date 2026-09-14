# Runtime Safety Native Ownership Audit

Base examined: `0d6287d67b4a28ab4f08fdbbdf9811ec16f263c9`

Task: `DS-AUD-RUNTIME-SAFETY`

## Outcome target

Retire the temporary `startup_guards.runtime_safety` monkey-patcher and its transitive `startup_guards.public_startup_scope` import hook without losing any behavior that still has a real ownership gap.

This is deliberately separate from `process_health`, which still owns process/signal/import-health behavior and requires its own boot-order-sensitive audit.

## Host ownership before this migration

`sitecustomize.py` imported `load_runtime_safety()` before the normal app entrypoint. `runtime_safety` then installed a global `builtins.__import__` hook and patched selected modules after import. It also imported `public_startup_scope`, which installed a second chained import hook.

That made production behavior differ from the canonical source files inspected by normal static tests.

## Exact runtime_safety target map

| Target | Temporary patch behavior | Current canonical evidence | Disposition |
| --- | --- | --- | --- |
| `raidguard` | refuse synchronous hard-identity DB reads on the running event loop | canonical `build_member_risk_profile()` already detects a running loop and uses cache/empty hard context instead of synchronous PostgREST | retire override; no replacement needed |
| `identity_proof_service` | replace `get_identity_truth_context()` with `{}` when a loop is running | `/identity_truth` is async but called the sync truth lookup directly | move the command call to `await asyncio.to_thread(...)` so real truth is returned off-thread |
| `tickets_new.service` | replace durable allocator with channel/DB-max scanning and wrap DB/event aliases in timeouts | canonical service delegates to `reserve_persistent_ticket_number()`; permanent regression tooling requires DB-authoritative numbering | retire all overrides; preserve canonical service unchanged |
| `modlog` | patch two absent legacy targets and queue `maybe_log_voice_state_update()` | the live voice path is already async, using Discord audit-log/channel APIs and canonical async modlog delivery | retire override; no replacement queue required |
| `app` | replace background startup runner with an extra runtime-jobs queue | canonical startup maintenance already runs in a dedicated background task and awaits async member/ticket services | retire override; no second queue owner required |

## Persistent ticket counter conflict

The most serious defect was not theoretical architecture debt.

Canonical `tickets_new.service._reserve_next_ticket_number()` calls the durable database-authoritative `reserve_persistent_ticket_number()` allocator. `tools/test_persistent_ticket_counter_static.py` explicitly requires that implementation and forbids channel scanning in the service allocator.

After those static checks passed, `runtime_safety` replaced the function at import time with older `max(channel numbers, DB numbers) + 1` logic. Production could therefore run behavior that contradicted the source CI had just validated.

Retiring the patcher fixes the ownership conflict by allowing the canonical allocator to remain the actual runtime allocator.

## Why the timeout wrappers are not migrated

The ticket repository and event helpers already offload synchronous database work. Wrapping a coroutine that is waiting on `asyncio.to_thread(...)` with `asyncio.wait_for(...)` cannot reliably cancel the underlying thread-backed database operation. A timeout can therefore return a synthetic failure while the write continues and later commits.

That creates a worse consistency contract than the canonical repository path. These wrappers are retired rather than moved.

## Identity truth migration

Before:

- `/identity_truth` called synchronous `get_identity_truth_context()` from an async interaction handler.
- the runtime patch detected the event loop and returned `{}` instead of querying.
- the command remained responsive by silently losing the requested truth data.

After:

- `/identity_truth` awaits `asyncio.to_thread(get_identity_truth_context, ...)`.
- the event loop remains non-blocking.
- the command receives the real proof/manual-link context.
- no import-time mutation is required.

## Public startup scope ownership

`public_startup_scope.py` duplicated command-scope ownership by replacing `app._sync_beta_guild_commands_if_requested` through an import hook.

The canonical production owner is already explicit:

- `main.py` directly imports `command_scope_dedupe` before the app;
- `command_scope_dedupe` defaults `DANK_SYNC_BETA_GUILD_COMMANDS=false` unless explicitly configured;
- it owns stale guild-command cleanup for public deployments;
- `app.py` owns the actual slash-maintenance execution path.

The duplicate import-hook owner is therefore retired rather than reimplemented.

## Files retired

- `stoney_verify/startup_guards/runtime_safety.py`
- `stoney_verify/startup_guards/public_startup_scope.py`

Both names are also removed from the inert historical startup inventory and startup diagnostics.

## Host behavior retained

`sitecustomize.py` remains only for the separately verified Basic Verify compatibility path:

- force/register `public_verify_basic_panel` compatibility;
- apply `basic_verification_mode_guard`.

This task does not change that behavior.

## Explicitly out of scope

- `startup_guards.process_health`;
- Basic Verify host compatibility;
- direct `main.py` command/startup guards;
- unrelated dormant startup guards;
- AntiNuke / DS-SEC-044;
- schema changes;
- broad identity-admin write-path refactoring.

## Regression contract

`tests/test_runtime_safety_native_ownership.py` requires:

- both retired files remain absent;
- the inert startup inventory cannot list them;
- `sitecustomize.py` cannot load them or install an import hook;
- `/identity_truth` must await `asyncio.to_thread(get_identity_truth_context, ...)`;
- the canonical persistent ticket allocator remains present;
- startup diagnostics cannot expect retired owners;
- the explicit command-scope owner retains the safe beta-sync default;
- `process_health` remains present and untouched.

## Follow-up debt

The next boot-order-sensitive runtime-ownership finding is `process_health`. Other command-tree wrappers and feature-owned compatibility helpers remain separate audits and must not be mass-deleted based solely on their directory name.
