from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INIT = (ROOT / "stoney_verify/commands_ext/__init__.py").read_text(encoding="utf-8")
STATUS = (ROOT / "stoney_verify/commands_ext/public_status_reporter.py").read_text(encoding="utf-8")
SURFACE = (ROOT / "stoney_verify/commands_ext/public_command_surface_v2.py").read_text(encoding="utf-8")
CONTRACT = (ROOT / "stoney_verify/command_surface_contract.py").read_text(encoding="utf-8")


def test_status_reporter_is_public_core() -> None:
    assert '"public_status_reporter"' in INIT
    assert '"status"' in INIT


def test_status_implementation_remains_loaded() -> None:
    assert "async def _status_callback" in STATUS
    assert 'name="status"' in STATUS
    assert "Send a fresh Dank Shield status report now." in STATUS


def test_status_is_reached_from_compact_home_not_a_direct_dank_child() -> None:
    assert 'label="Status"' in SURFACE
    assert 'await _invoke_saved("status", interaction)' in SURFACE
    assert 'PUBLIC_DANK_CHILDREN: frozenset[str] = frozenset({"home", "upload"})' in CONTRACT


def test_setup_status_implementation_can_remain_loaded_but_final_tree_hides_it() -> None:
    assert 'name="setup-status"' in STATUS
    assert "Choose where Dank Shield posts online/restored status reports." in STATUS
    assert '"setup-status"' in CONTRACT


def test_status_on_ready_logs_task_start() -> None:
    assert "status_reporter tasks ensured on_ready" in STATUS


if __name__ == "__main__":
    for test in (
        test_status_reporter_is_public_core,
        test_status_implementation_remains_loaded,
        test_status_is_reached_from_compact_home_not_a_direct_dank_child,
        test_setup_status_implementation_can_remain_loaded_but_final_tree_hides_it,
        test_status_on_ready_logs_task_start,
    ):
        test()
        print(f"PASS {test.__name__}")
