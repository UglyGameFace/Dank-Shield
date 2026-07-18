from __future__ import annotations

from pathlib import Path

APPLIER = Path("tools/apply_p0_int_design_style_change_native_guard.py").read_text(encoding="utf-8")
STUDIO = Path("stoney_verify/commands_ext/public_design_studio.py").read_text(encoding="utf-8")


def _style_region(source: str) -> str:
    start = source.index("class StyleChangeFixMissingEmojiModal")
    end = source.index("class StyleChangePreviewView", start)
    return source[start:end]


def test_style_change_applier_requires_native_design_helpers() -> None:
    assert "async def _guard_design_action(" in APPLIER
    assert "safe_send_interaction" in APPLIER
    assert "Apply exact-format guard migration first" in APPLIER


def test_style_change_applier_is_boundary_based() -> None:
    assert "START_MARKER" in APPLIER
    assert "END_MARKER" in APPLIER
    assert "replace_section" in APPLIER
    assert "class StyleChangeFixMissingEmojiModal(discord.ui.Modal):" in APPLIER
    assert "class StyleChangePreviewView(DesignPreviewView):" in APPLIER


def test_style_change_applier_targets_issue_review_actions() -> None:
    for action_name in (
        "design.style_change.missing_icons_submit",
        "design.style_change.missing_icons.expired",
        "design.style_change.apply_safe_only",
        "design.style_change.apply_safe.expired",
        "design.style_change.fix_missing_icons_modal",
        "design.style_change.fix_missing.expired",
        "design.style_change.fix_missing.none",
    ):
        assert action_name in APPLIER


def test_style_change_runtime_is_either_debt_or_applied() -> None:
    assert "class StyleChangeFixMissingEmojiModal" in STUDIO
    assert "class StyleChangeApplySafeOnlyButton" in STUDIO
    assert "class StyleChangeFixMissingEmojiButton" in STUDIO
    region = _style_region(STUDIO)
    if "design.style_change.apply_safe_only" in region:
        for action_name in (
            "design.style_change.missing_icons_submit",
            "design.style_change.apply_safe_only",
            "design.style_change.fix_missing_icons_modal",
        ):
            assert action_name in region
        assert "async def on_submit(self, interaction: discord.Interaction) -> None:" in region
        assert region.count("async def action() -> None:") >= 3
        assert "key = _key" in region
        assert "await interaction.response.edit_message" in region
        assert "await _guard_design_action" in region
    else:
        assert "This preview expired. Run Style Change again." in region
        assert "return await interaction.response.send_message" in region
