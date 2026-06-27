from __future__ import annotations

from pathlib import Path

GROUP = Path("stoney_verify/commands_ext/public_design_group.py").read_text(encoding="utf-8")
STUDIO = Path("stoney_verify/commands_ext/public_design_studio.py").read_text(encoding="utf-8")


def test_design_group_registers_command_with_native_interaction_guard() -> None:
    assert "run_guarded_interaction" in GROUP
    assert 'action_name="/dank design"' in GROUP
    assert "Dank Design failed safely" in GROUP
    assert "await design.open_design_studio(interaction)" in GROUP


def test_design_group_does_not_delegate_command_registration_to_raw_studio_callback() -> None:
    assert "design.register_public_design_studio_command(" not in GROUP
    assert "server_design_studio_command_guard" not in GROUP
    assert "public_design_studio as design" in GROUP


def test_design_studio_still_contains_raw_callback_debt_for_next_migration_slice() -> None:
    # This intentionally records why P0-INT-001 is not done yet.
    # The command opener is guarded in public_design_group, but the giant studio
    # module still has raw component callbacks that must be migrated in smaller
    # slices.
    assert "async def _require_design_permission" in STUDIO
    assert "interaction.response.send_message" in STUDIO
    assert "interaction.response.edit_message" in STUDIO
