from __future__ import annotations

from pathlib import Path

from stoney_verify.services import server_design_studio as studio


PUBLIC_STUDIO = Path("stoney_verify/commands_ext/public_design_studio.py").read_text(encoding="utf-8")


def test_strict_layout_guard_has_no_persisted_design_option_rewriter() -> None:
    guard_source = Path("stoney_verify/startup_guards/server_design_strict_layout_guard.py").read_text(encoding="utf-8")

    assert "_patch_command_guard_options" not in guard_source
    assert "_normalize_gothic_lock" not in guard_source
    assert "command_guard._load_design_options" not in guard_source
    assert "command_guard._save_design_options" not in guard_source


def test_recommended_strength_four_applies_selected_category_frame() -> None:
    result = studio.build_styled_name(
        "gaming",
        kind="category",
        theme_id="gothic_clean",
        strength=4,
        category_frame_id="lenticular",
        font="normal",
        icon_mode="clear",
        exact_match=True,
    )

    assert result.status == "changed"
    assert result.after.startswith("【")
    assert result.after.endswith("】")


def test_manual_exact_editor_changes_force_exact_match() -> None:
    assert 'current["exact_match"] = True' in PUBLIC_STUDIO


def test_unsaved_exact_editor_seeds_from_selected_item_not_server_majority() -> None:
    assert "def _live_target_exact_lock(" in PUBLIC_STUDIO
    assert "target_lock = _live_target_exact_lock" in PUBLIC_STUDIO
    assert "elif target_lock:" in PUBLIC_STUDIO
    assert '"live_target": "Selected item current style"' in PUBLIC_STUDIO


def test_exact_editor_preview_does_not_reuse_whole_server_style_change_controls() -> None:
    start = PUBLIC_STUDIO.index("async def _save_exact_and_preview")
    end = PUBLIC_STUDIO.index("class ExactFormatEditorView", start)
    exact_preview = PUBLIC_STUDIO[start:end]
    assert "view=DesignPreviewView(" in exact_preview
    assert "StyleChangePreviewView" not in exact_preview


def test_exact_editor_apply_is_bound_to_the_preview_user_reviewed() -> None:
    assert "pending_created_at=created_at" in PUBLIC_STUDIO
    assert "if self.pending_created_at is not None:" in PUBLIC_STUDIO
    assert "This Apply button belongs to an older preview" in PUBLIC_STUDIO


def test_separator_example_navigation_executes_inside_guarded_action() -> None:
    page_start = PUBLIC_STUDIO.index("class SeparatorExamplesPageButton")
    back_start = PUBLIC_STUDIO.index("class SeparatorExamplesBackButton", page_start)
    view_start = PUBLIC_STUDIO.index("class SeparatorExamplesView", back_start)
    page_block = PUBLIC_STUDIO[page_start:back_start]
    back_block = PUBLIC_STUDIO[back_start:view_start]
    assert 'await _guard_design_action(interaction, "design.exact.examples.page", action, defer=False)' in page_block
    assert 'await _guard_design_action(interaction, "design.exact.examples.back", action, defer=False)' in back_block
    assert page_block.index("guild = interaction.guild") < page_block.index("await interaction.response.edit_message")
    assert back_block.index("guild = interaction.guild") < back_block.index("await interaction.response.edit_message")
