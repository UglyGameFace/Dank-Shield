# Dank Shield TicketTool-Parity Completion Map

Updated: 2026-08-11  
Owner task: DS-BACKLOG-027  
Umbrella issue: #11

This document replaces the May 2026 snapshot. Issue #11 became an umbrella for multiple later ticket/setup/schema/scaling tasks, so checkbox age is no longer a useful source of truth. The canonical implementations and regression gates below are the source of truth for the original acceptance areas.

## 1. Ticket panel reliability and public UX

| Original requirement | Canonical owner / evidence |
|---|---|
| Persistent Create Ticket panel survives restarts | `commands_ext/public_ticket_panel_clean.py` → `PublicCreateTicketPanelView(timeout=None)` and persistent view registration |
| Create Ticket opens category picker, not direct modal | `TicketSelectView` / `TicketSelect` in the same owner |
| Category can be reviewed with Confirm / Back before creation | `TicketConfirmView` in the same owner |
| Wrong category can be corrected before creation | Back returns to the current session's category picker |
| Ticket is created only after Confirm | `_create_ticket` is reached from the confirmed current session; optional forms are opened before creation |
| Duplicate/stale menus cannot race ticket creation | owner-file interaction lock, newest-menu session ID, confirm lock, and existing-open-ticket recheck |
| Setup blockers are shown before creation | `_ticket_setup_preflight` and `_setup_problem_embed` |
| Ticket numbering remains persistent | `reserve_persistent_ticket_number` path |
| Active/archive lifecycle behavior | canonical `tickets_new` lifecycle/services and ticket action audits |
| Visible errors instead of silent interaction failures | owner helpers plus ticket lifecycle/action tests |

Regression gates:

- `.github/workflows/ticket-panel-owner.yml`
- `tests/test_public_ticket_panel_single_owner.py`
- `tools/audit_ticket_panel_doctor.py`
- `tools/audit_ticket_category_menu.py`

The former runtime callback rewrite files `public_ticket_panel_clean_hardening.py` and `public_ticket_confirm_hardening_guard.py` were removed by DS-BACKLOG-027. Their stale-menu, duplicate-interaction, confirm-lock and preflight behavior now lives in the actual ticket-panel owner.

## 2. Setup simplicity

The public owner remains the compact `/dank` menu-first surface rather than a large command tree. Canonical setup/service ownership includes:

- `commands_ext/public_setup_group.py`
- `startup_guards/setup_feature_health_scoreboard.py`
- `tickets_new/managed_category_service.py`
- `startup_guards/ticket_category_setup_guard.py`
- `setup_permission_repair_services.py`
- `permission_repair.py`

Ticket category selection comes from the managed catalog used by both setup and the live panel. DS-BACKLOG-027 additionally provides selected-target **Fix Access** from setup and diagnostics, with minimum/full modes, explicit-deny confirmation, undo, and non-Administrator reauthorization.

## 3. DB/schema resilience

Canonical migrations and compatibility services own schema truth. Important current gates include the managed ticket category migration/audit workflow, ticket-category selection/duplicate-repair migrations, the persistent operation queue + RLS hardening, schema compatibility checks before optional-field writes, and the Supabase migration-version audit in the main CI.

Auto-schema bootstrap is optional and must be security-equivalent to the migration path; DS-BACKLOG-027 specifically aligns `bot_operation_jobs` direct bootstrap with its hardened migration.

## 4. Public scale / multi-server readiness

The bot uses `AutoShardedBot`, per-guild configuration, bounded background work, and explicit shared operation serialization/idempotency for dangerous mutations. Current scale/security ownership includes:

- `globals.py` — AutoShardedBot construction
- `operation_queue.py` — persistent idempotency, per-guild/scoped concurrency, global/per-guild/per-operation backpressure, stale restart reconciliation, cancellation, retry/rate metrics and health
- `api_new/queued_handlers.py` — direct structured-API mutation protection
- `services/channel_builder_execution.py` — preflight + retry + rollback plan
- `services/channel_builder_rollback_runtime.py` — persistent restart-safe rollback
- `tools/test_multi_server_scale_static.py`
- `tools/test_backlog_027_static.py`
- `.github/workflows/backlog-027-validation.yml`

Unknown/dangerous queue classes default to broad serialization; explicitly scoped ticket/member/read-only classes can use bounded concurrency so unrelated guilds and safe scopes remain concurrent.

## 5. Legacy cleanup rule

The rule remains: **do not add a new patch to fix an old patch.**

DS-BACKLOG-027 removes the runtime import/callback rewrites that were still standing between the canonical owner and live behavior for Channel Builder route injection/export, structured API queue protection, operation-queue persistence retry, member cleanup queueing, and Create Ticket panel callback/confirm behavior.

Remaining startup-guard modules must own a discrete compatibility/service boundary rather than duplicate the Create Ticket or Channel Builder owner. New work should be implemented directly in owner/service modules and tests should assert those owners, not require a runtime monkeypatch file to exist.

## Acceptance status

This map describes implementation ownership, **not CI success by itself**. DS-BACKLOG-027 is complete only when its exact final PR head passes focused Python regressions, static acceptance audits, PostgreSQL migration/RLS apply-twice validation, ticket/category/doctor owner audits, the repository-wide Dank Shield CI and compile/static suite, and final stale-reference/dead-file inspection.
