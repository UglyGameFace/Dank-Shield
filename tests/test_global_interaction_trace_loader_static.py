from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GUARD = ROOT / "stoney_verify/startup_guards/global_interaction_trace_guard.py"
LOADER = (ROOT / "stoney_verify/startup_guards/__init__.py").read_text(encoding="utf-8")
NATIVE = (ROOT / "stoney_verify/interaction_guard.py").read_text(encoding="utf-8")

BOOT_FILES = (
    ROOT / "main.py",
    ROOT / "sitecustomize.py",
    ROOT / "usercustomize.py",
    ROOT / "stoney_verify/app.py",
    ROOT / "stoney_verify/commands.py",
)


def test_global_interaction_trace_guard_is_retired_not_historical_runtime_metadata() -> None:
    assert not GUARD.exists()
    assert "stoney_verify.startup_guards.global_interaction_trace_guard" not in LOADER


def test_production_boot_has_no_global_interaction_trace_owner() -> None:
    for path in BOOT_FILES:
        source = path.read_text(encoding="utf-8")
        assert "global_interaction_trace_guard" not in source


def test_native_interaction_service_owns_failure_and_duplicate_handling_without_framework_patch() -> None:
    assert "async def run_guarded_interaction" in NATIVE
    assert "def recent_interaction_failures" in NATIVE
    assert "DuplicateInteractionAction" in NATIVE

    for private_framework_hook in (
        "CommandTree._call",
        "_invoke_with_namespace",
        "_scheduled_task",
        "_dank_shield_elite_tree_call_wrapped",
        "_dank_shield_elite_view_scheduled_wrapped",
    ):
        assert private_framework_hook not in NATIVE
