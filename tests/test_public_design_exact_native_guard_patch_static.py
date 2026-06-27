from __future__ import annotations

from pathlib import Path

PATCH = Path("patches/p0-int-design-exact-format-native-guard.patch").read_text(encoding="utf-8")
STUDIO = Path("stoney_verify/commands_ext/public_design_studio.py").read_text(encoding="utf-8")
PATCH_ADDED_LINES = "\n".join(line for line in PATCH.splitlines() if line.startswith("+") and not line.startswith("+++"))
PATCH_REMOVED_LINES = "\n".join(line for line in PATCH.splitlines() if line.startswith("-") and not line.startswith("---"))


def test_exact_format_patch_introduces_native_guard_helper() -> None:
    assert "from stoney_verify.interaction_guard import run_guarded_interaction, safe_send_interaction" in PATCH
    assert "async def _guard_design_action" in PATCH
    assert "Dank Design action failed safely" in PATCH
    assert "_DESIGN_ERROR_GUIDANCE" in PATCH


def test_exact_format_patch_covers_high_risk_buttons() -> None:
    for action_name in (
        "design.exact.open.",
        "design.exact.examples",
        "design.exact.save_preview",
        "design.exact.server_style",
        "design.exact.emoji_modal",
        "design.exact.back",
    ):
        assert action_name in PATCH


def test_exact_format_patch_replaces_local_custom_format_fallback() -> None:
    # Normal git patches include removed code on '-' lines, so the old local
    # fallback should appear in removed lines but must not appear in added lines.
    assert "Custom Format could not open" in STUDIO
    assert "Custom Format could not open" in PATCH_REMOVED_LINES
    assert "Custom Format could not open" not in PATCH_ADDED_LINES
    assert "await _guard_design_action(interaction, f\"design.exact.open.{scope}\", action, defer=False)" in PATCH_ADDED_LINES


def test_exact_format_studio_debt_still_exists_until_patch_applied() -> None:
    # This intentionally documents why P0-INT-001 is still partial.
    assert "async def _open_exact_format_editor" in STUDIO
    assert "async def layout_examples" in STUDIO
    assert "async def save_and_preview" in STUDIO
    assert "async def use_server_style" in STUDIO
    assert "interaction.response.edit_message" in STUDIO
