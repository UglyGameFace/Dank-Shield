from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INIT = (ROOT / "stoney_verify/commands_ext/__init__.py").read_text(encoding="utf-8")
STATUS = (ROOT / "stoney_verify/commands_ext/public_status_reporter.py").read_text(encoding="utf-8")


def test_status_reporter_is_public_core() -> None:
    assert '"public_status_reporter"' in INIT
    assert '"status"' in INIT


def test_setup_status_is_not_pruned_as_confusing() -> None:
    confusing_start = INIT.index("_CONFUSING_DANK_CHILDREN")
    allowed_start = INIT.index("_ALLOWED_DANK_CHILDREN")
    confusing_block = INIT[confusing_start:allowed_start]
    assert '"setup-status"' not in confusing_block


def test_public_status_command_exists() -> None:
    assert "async def _status_callback" in STATUS
    assert 'name="status"' in STATUS
    assert "Send a fresh Dank Shield status report now." in STATUS


def test_setup_status_alias_still_exists() -> None:
    assert 'name="setup-status"' in STATUS
    assert "Choose where Dank Shield posts online/restored status reports." in STATUS


def test_status_on_ready_logs_task_start() -> None:
    assert "status_reporter tasks ensured on_ready" in STATUS


if __name__ == "__main__":
    for test in (
        test_status_reporter_is_public_core,
        test_setup_status_is_not_pruned_as_confusing,
        test_public_status_command_exists,
        test_setup_status_alias_still_exists,
        test_status_on_ready_logs_task_start,
    ):
        test()
        print(f"PASS {test.__name__}")
