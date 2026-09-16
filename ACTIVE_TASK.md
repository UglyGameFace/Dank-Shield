# ACTIVE TASK

## DS-AUD-MODLOG-MEMBERLOGS-CONTEXTUAL-REPAIR — Make logging routes self-repairing

**Outcome target:** Extend the merged same-screen permission-repair contract into the normal public Modlog and Member Logs workflows. When those surfaces can prove Dank Shield itself is missing safe access to an already configured log route, the same screen must expose one repair action, re-audit immediately, and leave only unsafe or configuration-level blockers for the administrator.

**Status:** IMPLEMENTATION / VALIDATION

**Branch:** `audit/modlog-memberlogs-contextual-repair`
**Base main:** `df85757d53b38338f829ff7ac618599015546cdf`
**Previous integrated task:** PR #246 merged at `df85757d53b38338f829ff7ac618599015546cdf`

## Scope

- canonical public Modlog Tracking → Health path through `modlog_tracking_service` and `public_modlog_group.open_modlog_health`
- canonical public `/dank member-logs` route installed by `public_member_lifecycle_runtime` through the authoritative member lifecycle router
- reuse of `contextual_permission_repair` / `permission_repair_core`; no feature-local Discord overwrite mutation
- exact configured/resolved Modlog, live join card, live exit card, and staff audit routes
- same-screen `Fix Issues` / `Access Healthy` / `Manual Fix Needed` behavior
- post-repair re-audit and refreshed status on the same workflow
- focused regressions and task/PR bookkeeping

Out of scope unless tracing proves a direct dependency:
- changing which event families Modlog records
- changing Welcome Card or Exit Card content/routing semantics
- creating or guessing replacement log channels
- making private staff log channels public
- changing member/staff role visibility
- clearing explicit denies without the existing explicit confirmation flow
- broad startup-guard retirement
- Profile / Self Roles / Protection / Embed / Status contextual repair adoption

## Findings

1. PR #246 merged the shared contextual repair contract into normal public Ticket surfaces and is verified on `main`.
2. Normal public Modlog Tracking is owned by `modlog_tracking_service.ModlogTrackingView`. Its **Health** button calls `public_modlog_group.open_modlog_health()`. That health path diagnoses the saved modlog channel and missing bot permissions but was read-only.
3. `public_modlog_group._missing_perms()` checks target-effective View Channel, Send Messages, Embed Links, Read Message History plus the server-level View Audit Log prerequisite. The channel bits are safe contextual-repair candidates; View Audit Log is not a channel-overwrite fix and remains manual.
4. `/dank member-logs` is a normal public child even though its current callback lives in `startup_guards/member_lifecycle_router_guard.py`. Production ownership is explicit: `public_member_lifecycle_runtime` imports that router and calls `install()`, and the startup-ownership audit marks it as the authoritative live feature owner.
5. `/dank member-logs` resolves three live routes after every save: the Welcome Card Studio join route, Exit Card Studio exit route, and a staff audit/modlog route. Those exact resolved text channels provide deterministic repair targets without guessing replacements.
6. The shared repair core already defines `welcome` and `logs` minimum profiles with View Channel, Send Messages, Embed Links, Attach Files, and Read Message History. That matches image-card/log delivery needs and keeps mutation centralized.
7. Member-log invite attribution also needs the server-level Manage Server capability. That prerequisite cannot be repaired through a channel overwrite and remains a precise manual warning.
8. `public_modlog_group._modlog_channel()` can discover a channel by name when no saved route exists. The new health display may report that discovery, but contextual repair deliberately refuses to target it until the administrator saves an exact route.
9. The late public setup gate runs after the authoritative member lifecycle command has installed, so it can safely compose the existing `/dank member-logs` callback without adding a second command owner.

## Implemented execution path

- added `public_logging_contextual_permission_repair` and activate it from the existing late public setup gate
- Modlog Tracking → Health now renders a same-screen contextual repair control
- Modlog repair targets only a saved Modlog channel ID using the `logs` profile; name-discovered channels are display-only until explicitly saved
- View Audit Log stays a manual server-level prerequisite
- `/dank member-logs` keeps its authoritative save callback, then gains a contextual repair view on the same response
- Member Logs repair targets the exact resolved live join card (`welcome`), live exit card (`logs`), and configured staff audit route (`logs`)
- missing join/exit routes, stale configured staff routes, and missing Manage Server for invite attribution remain manual
- every repair delegates to `contextual_permission_repair.repair_context()` and refreshes the same surface with a fresh config/audit
- runtime binding is atomic: if the existing Member Logs command cannot be resolved, Modlog Health is not partially patched
- no feature-local `set_permissions` mutation, explicit-deny clearing, role mutation, Administrator grant, or visibility widening was added

## Validation added

`tests/test_logging_contextual_permission_repair.py` covers:
- saved-ID-only Modlog target mapping and `logs` profile
- no guessed Modlog repair target when mapping is absent
- exact resolved Member Logs route mapping and `welcome` / `logs` profiles
- unresolved Member Logs routes never becoming guessed repair targets
- Modlog mapping + View Audit Log remaining manual
- Member Logs route gaps + Manage Server remaining manual
- optional absent staff audit route not being invented as a channel problem
- no feature-local `set_permissions` or explicit-deny clearing path
- late public bootstrap activation
- atomic binding when `/dank member-logs` is unavailable
- preservation of the authoritative Member Logs save callback before the repair view is attached

Local container validation was unavailable because the runtime cannot resolve GitHub for a clone. GitHub Actions on the exact final PR head is the validation authority.

## Validation gate

- normal public Modlog and Member Logs execution paths proven by source tracing
- focused logging contextual-repair regressions pass
- exact final branch 0 behind `main`
- final changed-file scope contains only task-owned implementation/tests/bookkeeping
- full required GitHub Actions pass on the exact final head
- no review/thread issue ignored
- no merge until exact-head validation and scope review are clean

## Backlog after this task

- Profile / Self Roles contextual repair adoption
- Protection contextual repair adoption
- remaining VC-specific repair cleanup
- Embed / Status contextual repair adoption
- admin-only `/dank tickettool-check` contextual repair adoption
- `/dank protection` remaining non-invite picker/guard cleanup
- `/dank design` picker migration
- admin-only legacy setup picker cleanup

## Next step

Normalize this task to one final commit, open the draft PR, run exact-head focused/full CI, inspect any concrete failure, perform final scope/drift/review checks, merge only when the exact final head is clean, verify `main`, then release the lock and move to Profile / Self Roles contextual repair.
