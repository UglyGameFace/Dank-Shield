#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]


def text(path: str) -> str:
    target = ROOT / path
    if not target.exists():
        raise AssertionError(f"missing required file: {path}")
    return target.read_text(encoding="utf-8")


def require(path: str, *markers: str) -> None:
    data = text(path)
    for marker in markers:
        assert marker in data, f"{path} missing marker: {marker}"


def forbid(path: str, *markers: str) -> None:
    data = text(path)
    for marker in markers:
        assert marker not in data, f"{path} still contains forbidden marker: {marker}"


def absent(path: str) -> None:
    assert not (ROOT / path).exists(), f"obsolete file still exists: {path}"


def main() -> int:
    # #57 — direct Channel Builder registration, restart-safe status/rollback,
    # and no compatibility route/export shims.
    require(
        "stoney_verify/api_new/server.py",
        "from .channel_builder_routes import register_channel_builder_routes",
        "register_channel_builder_routes(app, sys.modules[__name__])",
    )
    require(
        "stoney_verify/api_new/channel_builder_routes.py",
        "from ..services.channel_builder_execution import",
        "get_operation_job_persistent",
        "cancel_operation_job",
        "/operation/{job_id}/cancel",
    )
    require(
        "stoney_verify/services/channel_builder_execution.py",
        "rollback_plan",
        "delete_created_channel",
        "rename_channel",
        "with_retry",
    )
    require(
        "stoney_verify/services/channel_builder_rollback_runtime.py",
        "get_operation_job_persistent",
        "source_job_rollback_plan",
        "with_retry",
    )
    for path in (
        "stoney_verify/startup_guards/channel_builder_api_guard.py",
        "stoney_verify/startup_guards/channel_builder_runtime_exports_guard.py",
        "tools/patch_channel_builder_server_routes.py",
        ".github/workflows/channel-builder-server-direct-registration.yml",
    ):
        absent(path)

    # #56 — persistent restart/cancellation/backpressure/metrics plus direct API
    # mutation routing; old import-hook queue/persistence shims are gone.
    require(
        "stoney_verify/operation_queue.py",
        "async def reconcile_startup",
        "async def get_operation_job_persistent",
        "async def cancel_operation_job",
        "DANK_OPERATION_QUEUE_MAX_GLOBAL",
        "DANK_OPERATION_QUEUE_MAX_PER_GUILD",
        "DANK_OPERATION_QUEUE_MAX_PER_TYPE",
        '"failure_rate"',
        "async def with_retry",
    )
    require(
        "stoney_verify/api_new/server.py",
        "queued_api_handler(sys.modules[__name__]",
        "operation_queue=operation_queue_health_summary()",
    )
    require(
        "supabase/migrations/20260811175500_operation_queue_security_hardening.sql",
        "enable row level security",
        "revoke all on table public.bot_operation_jobs from anon",
        "grant select, insert, update, delete on table public.bot_operation_jobs to service_role",
        "idx_bot_operation_jobs_active_recovery",
    )
    for path in (
        "stoney_verify/startup_guards/api_operation_queue_guard.py",
        "stoney_verify/startup_guards/operation_queue_persistence_retry_guard.py",
    ):
        absent(path)

    # #20 — real execution, authorized no-confirm, persisted one-run summary and
    # no startup monkeypatch around cleanup.
    require(
        "stoney_verify/members_new/cleanup_service.py",
        "actor_can_use_no_confirm",
        "run_exclusive(",
        'operation_type="inactive_purge_execute"',
        "finalize_cleanup_run",
        'event_type": "member_cleanup_summary"',
        "post_cleanup_run_summary",
    )
    require(
        "stoney_verify/commands_ext/public_members_cleanup_group.py",
        "actor_can_use_no_confirm",
        "finalize_cleanup_run",
        "require_confirmation = bool(settings.require_queue_confirmation or not no_confirm_allowed)",
    )
    forbid(
        "stoney_verify/commands_ext/public_members_cleanup_group.py",
        "if True:  # Safety invariant: mass cleanup always requires confirmation.",
    )
    absent("stoney_verify/startup_guards/member_cleanup_operation_queue_guard.py")

    # #119 — selected target, feature/minimum/full modes, explicit-deny safety,
    # undo, non-Administrator reauthorization, setup + diagnostics handoffs.
    require(
        "stoney_verify/permission_repair.py",
        "class TargetPermissionRepairView",
        "Recommended minimum",
        "Full Dank Shield control",
        "Include Category Children",
        "Resolve Explicit Denies",
        "Undo Repair",
        "Reauthorize Dank Shield",
        "administrator = False",
        "clear_explicit_denies=False",
        "clear_explicit_denies=True",
        'event_type="permission_repair"',
    )
    require(
        "stoney_verify/setup_permission_repair_services.py",
        "Specific Channel",
        "open_target_permission_repair",
    )
    require(
        "stoney_verify/commands_ext/public_diagnostics_group.py",
        "Fix Channel Access",
        "open_target_permission_repair",
    )

    # #2 — serialized invite cache/diff, canonical aliases, separate approval
    # truth and conflict-visible shared reader.
    require(
        "stoney_verify/members_new/join_context_service.py",
        "invite_lock_for",
        "mark_invite_cache_ready",
        "merge_with_persisted_member_sync",
        "invite_cache_warming",
    )
    require(
        "stoney_verify/members_new/join_truth_integrity.py",
        "merge_join_context",
        "incoming_is_approval",
        'merged["entry_conflict"] = True',
        "approval_truth_quality",
    )
    require(
        "stoney_verify/verification_new/service.py",
        "approval_meta = approval_context",
        "incoming_is_approval=True",
    )
    require(
        "stoney_verify/tickets_new/member_context_service.py",
        '"entry_truth_quality"',
        '"entry_confidence"',
        '"entry_conflict"',
    )

    # #11 — current ticket owner directly owns stale-menu, confirm and duplicate
    # interaction protection. The two runtime rewrite guards are removed.
    require(
        "stoney_verify/commands_ext/public_ticket_panel_clean.py",
        "async def _handle_panel_button_core",
        "_PANEL_INTERACTION_LOCKS",
        "_MENU_SESSIONS",
        "_CONFIRM_LOCKS",
        "Newest menu wins.",
        "if not _PANEL_VIEW_REGISTERED and not _PANEL_FALLBACK_LISTENER_REGISTERED",
        "super().__init__(timeout=None)",
        "Confirm Ticket Category",
    )
    absent("stoney_verify/startup_guards/public_ticket_panel_clean_hardening.py")
    absent("stoney_verify/startup_guards/public_ticket_confirm_hardening_guard.py")
    startup = text("stoney_verify/startup_guards/__init__.py")
    for marker in (
        "channel_builder_api_guard",
        "channel_builder_runtime_exports_guard",
        "api_operation_queue_guard",
        "operation_queue_persistence_retry_guard",
        "member_cleanup_operation_queue_guard",
        "public_ticket_panel_clean_hardening",
        "public_ticket_confirm_hardening_guard",
    ):
        assert marker not in startup, f"startup still references obsolete runtime rewrite: {marker}"

    print("DS-BACKLOG-027 static acceptance audit passed")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except AssertionError as exc:
        print(f"DS-BACKLOG-027 static audit failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
