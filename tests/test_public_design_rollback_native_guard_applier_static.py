from __future__ import annotations

from pathlib import Path

LEGACY = Path("stoney_verify/commands_ext/public_design_studio.py").read_text(encoding="utf-8")
V2 = Path("stoney_verify/commands_ext/public_design_studio_v2.py").read_text(encoding="utf-8")
RETIRED_APPLIER = Path("tools/apply_p0_int_design_rollback_native_guard.py")
BAD_SPLIT_JOIN = 'value="' + "\n" + '".join'


def test_retired_rollback_mutator_is_absent() -> None:
    assert not RETIRED_APPLIER.exists()


def test_duplicate_legacy_rollback_ui_is_absent() -> None:
    assert "class RollbackConfirmView" not in LEGACY
    assert "async def _open_rollback" not in LEGACY
    assert "class DesignDoneView" not in LEGACY
    for retired_action in (
        "design.rollback.open_button",
        "design.done.back_to_studio",
        "design.rollback.preview",
        "design.rollback.confirm",
    ):
        assert retired_action not in LEGACY


def test_rollback_persistence_primitives_remain_backend_owned() -> None:
    for primitive in (
        "_persist_rollback_snapshot",
        "_latest_rollback_snapshot",
        "_pop_latest_rollback_snapshot",
    ):
        assert f"def {primitive}" in LEGACY or f"async def {primitive}" in LEGACY
        assert f"legacy.{primitive}" in V2


def test_consolidated_undo_has_no_split_newline_artifact() -> None:
    assert BAD_SPLIT_JOIN not in LEGACY
    assert BAD_SPLIT_JOIN not in V2
    assert 'value="\\n".join(lines)[:1024] or "No restorable names."' in V2
    assert '"\\n".join(f"• {line}" for line in errors[:8])[:1024]' in V2


def test_consolidated_studio_owns_public_undo_flow() -> None:
    assert "class UndoConfirmView" in V2
    assert "class DoneView" in V2
    assert "_open_undo" in V2
    assert "Undo Last Apply" in V2
    assert "apply_service.preflight_undo" in V2
    assert "apply_service.undo_prepared" in V2
