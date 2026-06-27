from __future__ import annotations

from pathlib import Path

APPLIER = Path("tools/apply_p0_int_design_rollback_native_guard.py").read_text(encoding="utf-8")
STUDIO = Path("stoney_verify/commands_ext/public_design_studio.py").read_text(encoding="utf-8")
BAD_SPLIT_JOIN = 'value="' + "\n" + '".join'


def test_rollback_applier_requires_exact_format_guard_first() -> None:
    assert "Missing _guard_design_action" in APPLIER
    assert "apply_p0_int_design_exact_format_native_guard.py" in APPLIER
    assert "safe_send_interaction" in APPLIER


def test_rollback_applier_targets_high_risk_rollback_actions() -> None:
    for action_name in (
        "design.rollback.open_button",
        "design.done.back_to_studio",
        "design.rollback.open",
        "design.rollback.preview",
        "design.rollback.locked",
        "design.rollback.confirm.no_snapshot",
        "design.rollback.confirm",
    ):
        assert action_name in APPLIER


def test_rollback_applier_repairs_split_newline_literals() -> None:
    assert "repair_split_newline_literals" in APPLIER
    assert "value=\"\\\\n\".join(preview)" in APPLIER
    assert "value=\"\\\\n\".join(failed[:10])" in APPLIER


def test_rollback_runtime_is_either_debt_or_applied() -> None:
    assert "class RollbackConfirmView" in STUDIO
    assert "async def _open_rollback" in STUDIO
    if "design.rollback.confirm" in STUDIO:
        assert BAD_SPLIT_JOIN not in STUDIO
        assert "design.rollback.open" in STUDIO
        assert "design.done.back_to_studio" in STUDIO
        assert 'value="\\n".join(preview)[:1024] or "No items."' in STUDIO
        assert 'value="\\n".join(failed[:10])[:1024]' in STUDIO
    else:
        assert "await interaction.response.defer(ephemeral=True, thinking=False)" in STUDIO
        assert "await interaction.edit_original_response(embed=embed, view=None)" in STUDIO
