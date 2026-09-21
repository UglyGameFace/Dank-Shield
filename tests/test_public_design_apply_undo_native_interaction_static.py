from __future__ import annotations

from pathlib import Path

V2 = Path("stoney_verify/commands_ext/public_design_studio_v2.py").read_text(encoding="utf-8")


def _region(start: str, end: str, *, offset: int = 0) -> str:
    start_index = V2.index(start, offset)
    end_index = V2.index(end, start_index)
    return V2[start_index:end_index]


def test_v2_mutation_boundaries_use_native_interaction_service() -> None:
    assert "from stoney_verify.interaction_guard import run_guarded_interaction, safe_send_interaction" in V2
    assert "async def _guard_design_v2_action" in V2
    assert "await run_guarded_interaction(" in V2
    assert "_DESIGN_V2_MUTATION_ERROR_GUIDANCE" in V2
    assert "Do not retry this design mutation blindly" in V2


def test_reviewed_apply_is_native_guarded_without_changing_transaction_ownership() -> None:
    owner = V2.index("class ReviewedPreviewView")
    block = _region(
        "    async def apply(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:",
        '\n    @discord.ui.button(label="Back"',
        offset=owner,
    )

    assert 'await _guard_design_v2_action(interaction, "design.v2.apply_reviewed", action)' in block
    assert "async def action() -> None:" in block
    assert "await self._apply_reviewed_action(interaction, button)" in block
    assert "async def _apply_reviewed_action" in block
    assert "interaction.response.send_message" not in block

    for safe_action in (
        "design.v2.apply_reviewed.stale_preview",
        "design.v2.apply_reviewed.no_preview",
        "design.v2.apply_reviewed.blocked",
        "design.v2.apply_reviewed.guild_busy",
    ):
        assert safe_action in block

    # The interaction wrapper must not replace the mature mutation safety path.
    for required in (
        "legacy._pending_matches",
        "legacy._lock_for",
        "apply_service.preflight_plan",
        "apply_service.apply_prepared",
        "apply_service.compensate_applied",
        "_store_residual_snapshot",
        "_store_snapshot_with_memory_fallback",
        "legacy._PENDING.pop",
    ):
        assert required in block


def test_undo_open_is_native_guarded_and_action_uses_safe_send() -> None:
    action_block = _region(
        "async def _open_undo_action(interaction: discord.Interaction) -> None:",
        "\n\nasync def _open_undo(interaction: discord.Interaction) -> None:",
    )
    wrapper_block = _region(
        "async def _open_undo(interaction: discord.Interaction) -> None:",
        "\n\nclass DoneView",
    )

    # The public callback is the native interaction boundary.
    assert 'await _guard_design_v2_action(interaction, "design.v2.undo_open", action)' in wrapper_block
    assert "async def action() -> None:" in wrapper_block
    assert "await _open_undo_action(interaction)" in wrapper_block

    # The extracted business-action helper owns snapshot lookup and user-facing
    # safe responses. Keeping these assertions on the helper avoids falsely
    # requiring implementation details to live inside the thin wrapper.
    assert "await safe_send_interaction(" in action_block
    assert "legacy.safe_send_interaction" not in action_block
    assert "legacy._latest_rollback_snapshot" in action_block
    assert "UndoConfirmView" in action_block


def test_undo_confirm_is_native_guarded_without_changing_undo_safety() -> None:
    owner = V2.index("class UndoConfirmView")
    block = _region(
        "    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:",
        '\n    @discord.ui.button(label="Cancel"',
        offset=owner,
    )

    assert 'await _guard_design_v2_action(interaction, "design.v2.undo_confirm", action)' in block
    assert "await self._confirm_undo_action(interaction, button)" in block
    assert "async def _confirm_undo_action" in block
    assert "design.v2.undo_confirm.guild_busy" in block
    assert "interaction.response.send_message" not in block

    for required in (
        "legacy._lock_for",
        "legacy._latest_rollback_snapshot",
        "_snapshot_matches",
        "apply_service.preflight_undo",
        "apply_service.undo_prepared",
        "_pop_snapshot_if_current",
    ):
        assert required in block
