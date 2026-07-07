from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

INIT = (ROOT / "stoney_verify/commands_ext/__init__.py").read_text(encoding="utf-8")
RUNTIME = (ROOT / "stoney_verify/commands_ext/public_member_lifecycle_runtime.py").read_text(encoding="utf-8")
ROUTER = (ROOT / "stoney_verify/startup_guards/member_lifecycle_router_guard.py").read_text(encoding="utf-8")
STARTUP_GUARDS = (ROOT / "stoney_verify/startup_guards/__init__.py").read_text(encoding="utf-8")
SETUP = (ROOT / "stoney_verify/commands_ext/public_setup_group.py").read_text(encoding="utf-8")
SETUP_LOGS = (ROOT / "stoney_verify/commands_ext/public_setup_logs.py").read_text(encoding="utf-8")
HARDENING = (ROOT / "stoney_verify/startup_guards/member_lifecycle_verify_runtime_hardening.py").read_text(encoding="utf-8")


def test_member_lifecycle_runtime_is_public_core() -> None:
    assert '"public_member_lifecycle_runtime"' in INIT
    assert "register_public_member_lifecycle_runtime" in INIT


def test_runtime_bootstrap_calls_authoritative_router_install() -> None:
    assert "member_lifecycle_router_guard" in RUNTIME
    assert "router.install()" in RUNTIME


def test_join_leave_aliases_are_broad_and_consistent() -> None:
    for key in (
        "join_leave_log_channel_id",
        "join_leave_channel_id",
        "member_join_leave_log_channel_id",
        "member_lifecycle_log_channel_id",
        "member_log_channel_id",
        "member_logs_channel_id",
        "join_log_channel_id",
        "join_exit_log_channel_id",
        "joinlog_channel_id",
        "joinleave_channel_id",
        "welcome_exit_channel_id",
        "welcome_exit_log_channel_id",
        "leave_log_channel_id",
        "welcome_leave_channel_id",
        "leave_channel_id",
    ):
        assert key in ROUTER, f"router missing alias {key}"
        assert key in SETUP, f"setup display missing alias {key}"
        assert key in SETUP_LOGS, f"setup-logs write missing alias {key}"
        assert key in HARDENING, f"hardening picker missing alias {key}"


def test_ready_logs_resolved_member_lifecycle_routes() -> None:
    assert "member lifecycle routes ready" in ROUTER
    assert "members intent is disabled in code" in ROUTER


def test_member_logs_command_is_allowed_child() -> None:
    assert '"member-logs"' in INIT


def test_old_welcome_member_events_route_is_not_loaded_by_startup() -> None:
    assert STARTUP_GUARDS.find("stoney_verify.startup_guards.welcome_member_events_guard") == -1


def test_setup_logs_uses_explicit_join_leave_route() -> None:
    assert "join_leave_log_channel" in SETUP_LOGS
    assert "welcome_exit_channel_id" in SETUP_LOGS
    assert SETUP_LOGS.find("join_leave_log_channel) or _channel_value(modlog_channel)") == -1


def test_runtime_hardening_is_loaded() -> None:
    assert "member_lifecycle_verify_runtime_hardening" in STARTUP_GUARDS
    assert "_install_basic_verify_fallback" in HARDENING
    assert "maybe_handle_basic_verify_interaction" in HARDENING
    assert "_patch_setup_join_leave_alias_picker" in HARDENING
    assert "_patch_join_context_schema_fallback" in HARDENING
    assert "_patch_modlog_alias_resolution" in HARDENING


def test_welcome_channel_not_join_leave_default() -> None:
    assert "join/leave channel equals welcome" in HARDENING
    assert "welcome_enabled" in HARDENING
    assert "welcome_join_enabled" in HARDENING


if __name__ == "__main__":
    for test in (
        test_member_lifecycle_runtime_is_public_core,
        test_runtime_bootstrap_calls_authoritative_router_install,
        test_join_leave_aliases_are_broad_and_consistent,
        test_ready_logs_resolved_member_lifecycle_routes,
        test_member_logs_command_is_allowed_child,
        test_old_welcome_member_events_route_is_not_loaded_by_startup,
        test_setup_logs_uses_explicit_join_leave_route,
        test_runtime_hardening_is_loaded,
        test_welcome_channel_not_join_leave_default,
    ):
        test()
        print(f"PASS {test.__name__}")
