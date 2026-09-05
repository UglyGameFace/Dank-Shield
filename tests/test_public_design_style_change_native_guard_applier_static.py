from __future__ import annotations

from pathlib import Path

LEGACY = Path("stoney_verify/commands_ext/public_design_studio.py").read_text(encoding="utf-8")
V2 = Path("stoney_verify/commands_ext/public_design_studio_v2.py").read_text(encoding="utf-8")
RETIRED_APPLIER = Path("tools/apply_p0_int_design_style_change_native_guard.py")


def _style_region(source: str) -> str:
    start = source.index("class StyleChangeFixMissingEmojiModal")
    end = source.index("class StyleChangePreviewView", start)
    return source[start:end]


def test_retired_style_change_mutator_is_absent() -> None:
    assert not RETIRED_APPLIER.exists()


def test_style_change_issue_review_is_native_and_guarded() -> None:
    assert "class StyleChangeFixMissingEmojiModal" in LEGACY
    assert "class StyleChangeApplySafeOnlyButton" in LEGACY
    assert "class StyleChangeFixMissingEmojiButton" in LEGACY
    region = _style_region(LEGACY)
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


def test_consolidated_preview_owns_separator_apply_boundary() -> None:
    assert "class LegacyStyleChangePreviewView(ReviewedPreviewView)" in V2
    assert "legacy.StyleChangeFixMissingEmojiButton" in V2
    assert "legacy.StyleChangeApplySafeOnlyButton" in V2
    assert "class ReviewedPreviewView" in V2
    assert "Apply Reviewed Changes" in V2
