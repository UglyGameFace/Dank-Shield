# ACTIVE TASK

## DS-AUD-MODLOG-MEMBERLOGS-CONTEXTUAL-REPAIR — Repair the failed logging contextual integration

**Outcome target:** Restore the intended same-screen permission-repair contract for normal public Modlog Health and `/dank member-logs`. Safe Dank Shield channel-access problems must expose one repair action, re-audit immediately, refresh the same response, and leave only unsafe or configuration-level blockers for the administrator.

**Status:** REOPENED — CORRECTIVE IMPLEMENTATION / VALIDATION

**Corrective branch:** `audit/modlog-memberlogs-contextual-repair-fix`
**Base main:** `b8c798422dfd8d56c8dd7a6159c20cdcf1d6804a`
**Failed merged implementation:** PR #247, merge commit `b8c798422dfd8d56c8dd7a6159c20cdcf1d6804a`, PR head `9f75754c88caec92946f2ad468e266b932e31783`
**Previous integrated task:** PR #246 merged at `df85757d53b38338f829ff7ac618599015546cdf`

## Scope

- investigate and correct PR #247 only; do not advance the audit backlog while this lock is active
- canonical public Modlog Tracking → Health path through `modlog_tracking_service` and `public_modlog_group.open_modlog_health`
- canonical public `/dank member-logs` callback owned by `startup_guards/member_lifecycle_router_guard.py` and installed through `public_member_lifecycle_runtime`
- reuse `contextual_permission_repair` / `permission_repair_core`; no feature-local Discord overwrite mutation
- preserve exact configured/resolved Modlog, live join card, live exit card, and staff audit targets
- preserve safe/manual classification and same-screen `Fix Issues` / `Access Healthy` / `Manual Fix Needed` behavior
- focused regression coverage for the concrete runtime binding failure

Out of scope unless tracing proves a direct dependency:
- Profile / Self Roles work or the already-created `audit/profile-selfroles-contextual-repair` branch
- changing Modlog event-family ownership
- changing Welcome Card or Exit Card content/routing semantics
- creating or guessing replacement log channels
- widening @everyone, member, or staff visibility
- clearing explicit denies automatically
- broad startup-guard retirement or unrelated cleanup

## Concrete failure

PR #247 merged while two checks were still running, but the completed exact-head record is fully green: Dank Shield CI, Profile Runtime Diagnostics, Application Command Size Diagnostics, Ticket Owner Emergency Override, and Dank Design Regression CI all completed successfully on `9f75754c88caec92946f2ad468e266b932e31783`. The failure is therefore not a red-CI merge.

The merged runtime integration is nevertheless inactive. `apply_logging_contextual_permission_repair()` tries to assign `member_logs_command.callback = contextual_member_logs_callback`. This repository pins `discord.py==2.7.1`; `discord.app_commands.Command.callback` is a getter-only property backed by `_callback`, with no setter. The assignment raises `AttributeError`. PR #247's atomic rollback then restores the Modlog Health function too, so neither Member Logs nor Modlog contextual repair remains active and startup reports the logging contextual repair activation as false.

## Root cause

The regression is a **runtime callback wrapping problem** caused by treating a real `discord.app_commands.Command` like a mutable test double. `tests/test_logging_contextual_permission_repair.py` used `SimpleNamespace(callback=...)`, so assignment succeeded in CI and failed to model the pinned discord.py command object. The registration order itself is valid: `public_member_lifecycle_runtime` installs `/dank member-logs` before `public_setup_gate` applies contextual integrations.

## Production execution path

- `commands_ext` registers `public_member_lifecycle_runtime` before `public_setup_gate`
- `public_member_lifecycle_runtime` imports the authoritative member lifecycle router and calls `install()`
- the router registers `/dank member-logs` as a real `discord.app_commands.Command` around `_member_logs_command`
- `public_setup_gate` later calls `apply_logging_contextual_permission_repair()`
- PR #247 failed at assignment to the read-only `Command.callback`, which triggered its rollback and removed the Modlog patch as well
- Modlog Tracking's Health button and `/dank modlog health` both resolve `public_modlog_group.open_modlog_health` at call time, so replacing that module function remains the correct Modlog integration point

## Corrective changes

- keep `_member_logs_command` as the authoritative Member Logs callback instead of replacing the framework command callback after registration
- after the canonical Member Logs response succeeds, call `attach_member_logs_contextual_repair()` to edit that same response with `MemberLogsRepairView`
- gate Member Logs decoration on the shared logging integration activation flag so Modlog + Member Logs activation remains atomic
- keep Modlog Health patching through the existing module function
- remove the obsolete `_ORIGINAL_MEMBER_LOGS_CALLBACK` state and callback-assignment/rollback logic
- do not mutate discord.py private `_callback`
- do not add any new `set_permissions()` implementation

## Validation coverage

`tests/test_logging_contextual_permission_repair.py` must cover:
- saved-ID-only Modlog targeting and `logs` profile
- no guessed Modlog repair target
- exact resolved Member Logs routes and `welcome` / `logs` profiles
- unresolved routes and server-level permissions remain manual
- no feature-local `set_permissions` / explicit-deny clearing
- late public bootstrap activation
- missing Member Logs command prevents partial Modlog activation
- the pinned `app_commands.Command.callback` property is read-only
- activation succeeds with a real `app_commands.Command` without replacing its callback
- authoritative Member Logs source calls the contextual response decorator
- Member Logs decoration is disabled until atomic activation succeeds
- activated Member Logs response receives the repair view

Local cloning remains unavailable because this execution environment cannot resolve `github.com`. GitHub Actions on the exact corrective PR head is the executable validation authority.

## Validation gate

Before this task can be closed:
- corrective branch is 0 behind `main`
- final changed-file scope is reviewed and contains only task-owned implementation/tests/bookkeeping
- focused logging contextual-repair regressions pass
- Python compile passes
- full unit suite passes
- Claim-first ticket security passes
- Managed category SQL smoke test passes
- every other triggered workflow finishes successfully on the exact final head
- review threads and PR reviews are checked
- no unexplained drift or unrelated change remains
- merge uses the exact validated head
- `main` is verified at the resulting merge commit

Any commit that changes the corrective PR head resets exact-head validation.

## Cleanup / conflicts

- shared permission mutation ownership remains in `contextual_permission_repair` / `permission_repair_core`
- the failed post-registration Member Logs callback wrapper is removed rather than layered with another workaround
- no private discord.py callback field mutation is introduced
- the previously created `audit/profile-selfroles-contextual-repair` branch remains untouched

## Blockers / risks

- no current implementation blocker
- local clone/test execution is unavailable in this environment because GitHub DNS resolution fails; exact-head GitHub Actions is required before merge

## Backlog after this task

1. Profile / Self Roles contextual repair adoption
2. Protection contextual repair adoption
3. remaining VC-specific repair cleanup
4. Embed / Status contextual repair adoption
5. admin-only `/dank tickettool-check` contextual repair adoption
6. remaining `/dank protection` non-invite picker/guard cleanup
7. `/dank design` picker migration
8. admin-only legacy setup picker cleanup

## Next step

Commit the corrective implementation and regressions as one focused tree, open the corrective PR, validate the exact head through every required workflow, inspect scope/reviews/drift, merge only that validated head, then verify the resulting merge commit is the current `main` before releasing this task lock.
