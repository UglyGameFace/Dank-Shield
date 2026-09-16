# ACTIVE TASK

## DS-AUD-TICKET-CONTEXTUAL-REPAIR — Make Ticket diagnostics and controls self-repairing

**Outcome target:** Extend the merged same-screen repair contract into the normal public Ticket workflows. When a Ticket menu or Ticket health screen can prove Dank Shield itself is missing safe channel/category access, the same screen must expose one repair action, repair every safe target it owns, re-audit immediately, and leave only unsafe/manual blockers for the administrator.

**Status:** IMPLEMENTATION / VALIDATION

**Branch:** `audit/ticket-contextual-repair`
**Base main:** `2c1c8d72e63215159e89ae47549bc86d278cc231`
**Previous integrated task:** PR #245 merged at `2c1c8d72e63215159e89ae47549bc86d278cc231`

## Scope

- canonical Current Ticket Center in `public_ticket_command_center`
- canonical Public Ticket Panel Health in `public_ticket_panel_clean`
- reuse of `contextual_permission_repair` / `permission_repair_core`; no feature-local overwrite mutation
- exact configured Ticket targets: active category, archive category, public panel channel, transcript channel
- exact selected live ticket channel when a staff member is operating on that ticket
- same-screen `Fix Issues` / `Access Healthy` / `Manual Fix Needed` behavior
- exact post-repair re-audit and refreshed menu/health output
- focused regressions and task/PR bookkeeping

Out of scope unless tracing proves a direct dependency:
- rewriting ticket lifecycle, claim-first policy, ownership, transcript generation, delete/close/reopen semantics, or category catalog behavior
- automatically changing @everyone visibility or staff-role access on ticket categories
- moving roles or granting Administrator
- clearing explicit denies without the existing explicit confirmation flow
- broad startup-guard cleanup
- Modlog/Member Logs/Profile/Protection contextual repair adoption
- admin-only `/dank tickettool-check` adoption

## Findings

1. PR #245 merged the shared contextual repair contract into Setup Check and Verification Channels and is verified on `main`.
2. `TicketPanelToolsView` exposes **Health Check**, which calls `public_ticket_panel_clean._send_health()`. The health owner already diagnoses bot access on the Active Tickets category, archive category, public panel channel, and transcript channel, plus ticket-category privacy/staff shape. Before this task the result was read-only.
3. `public_ticket_panel_clean._health_lines()` distinguishes missing configured resources from live permission failures. Safe bot-only permission failures have exact target IDs and can use the shared contextual repair owner. Missing mappings and category privacy/staff-role shape are not safe bot-only repairs and remain manual.
4. `TicketActionCenterView` owns an exact selected live ticket channel and all guarded staff actions, so it can safely expose contextual repair for Dank Shield's own access without changing ticket ownership/member/staff visibility.
5. `permission_repair_core` already has a Ticket feature profile and safe selected-target repair with explicit-deny preservation, retry handling, undo/event recording, and post-repair audit. No new `set_permissions` owner is needed.
6. `/dank tickettool-check` is `public-admin` only and absent from `_PUBLIC_CORE_MODULES`. Importing it from the public repair bootstrap would re-expose an intentionally hidden admin diagnostic, so its contextual-repair adoption is deliberately deferred to the admin-only cleanup pass.

## Implemented execution path

- added `public_ticket_contextual_permission_repair` and activate it from the existing late public setup gate
- Ticket Panel Health now renders a contextual repair control on the same response
- one Ticket Panel Health repair action targets only saved IDs for:
  - Active Tickets category using the `tickets` profile
  - Ticket archive category using the `tickets` profile
  - public ticket panel channel using the `general` profile
  - transcripts channel using the `logs` profile
- the Current Ticket Center gains a contextual repair control for its exact selected live ticket channel using the `tickets` profile
- selecting another ticket rebuilds the contextual control against the new exact channel instead of retaining stale repair state
- every repair delegates to `contextual_permission_repair.repair_context()` and immediately refreshes/re-audits the same Ticket surface
- no feature-local `set_permissions` mutation was added

## Safety contract

- only saved Ticket infrastructure IDs or the exact selected live ticket are targeted
- no replacement resource is guessed
- no @everyone or staff-role visibility is widened
- no ticket owner/member/staff overwrite is changed by this contextual bot-access path
- no role hierarchy is moved and no Administrator permission is granted
- explicit denies remain preserved by the shared repair core
- missing mappings, privacy/staff shape, hierarchy, server-level prerequisites, and database/schema failures remain manual

## Validation added

`tests/test_ticket_contextual_permission_repair.py` covers:
- deterministic saved-ID target mapping and feature profiles
- no target guessing when mappings are absent
- missing category/staff mappings remaining manual
- preservation of the complete 16-action Current Ticket menu
- contextual repair control presence on Current Ticket and Ticket Panel Health surfaces
- runtime binding of the two normal-public Ticket entry points
- no feature-local `set_permissions` or explicit-deny clearing path
- proof that `/dank tickettool-check` stays admin-only

## Validation gate

- normal public Ticket execution path proven by source tracing
- focused Ticket contextual-repair regressions pass
- exact final branch 0 behind `main`
- final changed-file scope contains only task-owned implementation/tests/bookkeeping
- full required GitHub Actions pass on the exact final head
- no review/thread issue ignored
- no merge until exact-head validation and scope review are clean

## Backlog after this task

- Modlog / Member Logs contextual repair adoption
- Profile / Self Roles contextual repair adoption
- Protection contextual repair adoption
- remaining VC-specific repair cleanup
- Embed / Status contextual repair adoption
- admin-only `/dank tickettool-check` contextual repair adoption
- `/dank protection` remaining non-invite picker/guard cleanup
- `/dank design` picker migration
- admin-only legacy setup picker cleanup

## Next step

Normalize this task to one final commit, validate that exact head through focused/full CI, perform final drift/review/scope checks, merge PR #246 when green, verify `main`, then release the lock and move to Modlog / Member Logs contextual repair.
