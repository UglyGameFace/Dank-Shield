from __future__ import annotations

from pathlib import Path

LEGACY = Path("stoney_verify/commands_ext/public_design_studio.py").read_text(encoding="utf-8")
V2 = Path("stoney_verify/commands_ext/public_design_studio_v2.py").read_text(encoding="utf-8")
RETIRED_APPLIER = Path("tools/apply_p0_int_design_rollback_native_guard.py")
BAD_SPLIT_JOIN = 'value="' + "\n" + '".join'


def test_retired_rollback_mutator_is_absent() -> None:
    assert not RETIRED_APPLIER.exists()


def test_rollback_runtime_has_native_guarded_actions() -> None:
    assert "class RollbackConfirmView" in LEGACY
    assert "async def _open_rollback" in LEGACY
    for action_name in (
        "design.rollback.open_button",
        "design.done.back_to_studio",
        "design.rollback.open",
        "design.rollback.preview",
        "design.rollback.locked",
        "design.rollback.confirm.no_snapshot",
        "design.rollback.confirm",
    ):
        assert action_name in LEGACY
    assert "await _guard_design_action" in LEGACY


def test_rollback_runtime_has_no_split_newline_artifact() -> None:
    assert BAD_SPLIT_JOIN not in LEGACY
    assert 'value="\\n".join(preview)[:1024] or "No items."' in LEGACY
    assert 'value="\\n".join(failed[:10])[:1024]' in LEGACY


def test_consolidated_studio_owns_public_undo_flow() -> None:
    assert "class UndoConfirmView" in V2
    assert "class DoneView" in V2
    assert "_open_undo" in V2
    assert "Undo Last Apply" in V2
