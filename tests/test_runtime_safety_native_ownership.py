from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STARTUP_GUARDS = ROOT / "stoney_verify" / "startup_guards"


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_temporary_runtime_import_hook_modules_are_retired() -> None:
    assert not (STARTUP_GUARDS / "runtime_safety.py").exists()
    assert not (STARTUP_GUARDS / "public_startup_scope.py").exists()


def test_sitecustomize_no_longer_loads_application_runtime_patcher() -> None:
    text = _read("sitecustomize.py")
    assert "load_runtime_safety" not in text
    assert "startup_guards.runtime_safety" not in text
    assert "public_startup_scope" not in text
    assert "builtins.__import__" not in text

    # The separately verified Basic Verify compatibility path remains owned here.
    assert "_force_verify_panel_command_module()" in text
    assert "basic_verification_mode_guard.apply()" in text


def test_identity_truth_command_offloads_sync_truth_lookup() -> None:
    path = ROOT / "stoney_verify" / "commands_ext" / "identity_admin.py"
    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text)

    identity_truth = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "identity_truth"
    )

    calls = [node for node in ast.walk(identity_truth) if isinstance(node, ast.Call)]
    to_thread_calls = [
        node
        for node in calls
        if isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "asyncio"
        and node.func.attr == "to_thread"
    ]
    assert len(to_thread_calls) == 1

    call = to_thread_calls[0]
    assert call.args
    assert isinstance(call.args[0], ast.Name)
    assert call.args[0].id == "get_identity_truth_context"

    awaited_to_thread = any(
        isinstance(node, ast.Await) and node.value is call
        for node in ast.walk(identity_truth)
    )
    assert awaited_to_thread


def test_runtime_cannot_override_persistent_ticket_counter_anymore() -> None:
    service = _read("stoney_verify/tickets_new/service.py")
    assert "return await reserve_persistent_ticket_number" in service
    assert not (STARTUP_GUARDS / "runtime_safety.py").exists()


def test_startup_diagnostics_only_expect_current_live_owners() -> None:
    diagnostics = _read("stoney_verify/startup_diagnostics.py")
    assert "stoney_verify.startup_guards.runtime_safety" not in diagnostics
    assert "stoney_verify.startup_guards.public_startup_scope" not in diagnostics
    assert "stoney_verify.startup_guards.process_health" in diagnostics


def test_public_command_scope_has_explicit_safe_beta_sync_default_owner() -> None:
    text = _read("stoney_verify/startup_guards/command_scope_dedupe.py")
    assert 'os.environ["DANK_SYNC_BETA_GUILD_COMMANDS"] = "false"' in text


def test_process_health_remains_out_of_scope() -> None:
    assert (STARTUP_GUARDS / "process_health.py").exists()
