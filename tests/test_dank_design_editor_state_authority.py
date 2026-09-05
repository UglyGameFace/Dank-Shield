from __future__ import annotations

from pathlib import Path

from stoney_verify.services import server_design_studio as studio
from stoney_verify.commands_ext import public_design_studio as public_studio


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
    assert "if not _pending_matches(payload, self.pending_created_at):" in PUBLIC_STUDIO
    assert "older or invalidated preview" in PUBLIC_STUDIO


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


def test_manual_name_override_preserves_literal_name_without_normalizing() -> None:
    item = public_studio._manual_name_override_plan_item(
        "old-category",
        "My Exact Category Name",
        kind="category",
        channel_id=123,
        category_id=123,
    )
    assert item["after"] == "My Exact Category Name"
    assert item["status"] == "changed"
    assert item["format_lock_scope"] == "manual_name"


def test_direct_rename_runs_inside_guard_and_saves_exact_name() -> None:
    start = PUBLIC_STUDIO.index("class DirectRenameModal")
    end = PUBLIC_STUDIO.index("def _category_action_embed", start)
    block = PUBLIC_STUDIO[start:end]
    assert 'await _guard_design_action(interaction, "design.direct_rename", action, defer=False)' in block
    assert "await _save_manual_name_override(" in block
    assert block.index("guild = interaction.guild") < block.index("await channel.edit(")


def test_direct_rename_refresh_prefers_live_api() -> None:
    start = PUBLIC_STUDIO.index("async def _direct_rename_fetch_target")
    end = PUBLIC_STUDIO.index("def _direct_rename_result_value", start)
    block = PUBLIC_STUDIO[start:end]
    assert "await guild.fetch_channel" in block
    assert block.index("await guild.fetch_channel") < block.index("return cached if cached is not None else fallback")


def test_manual_name_override_outranks_style_plan_and_is_resettable() -> None:
    assert 'manual_override = _manual_name_override_for(options, channel_id)' in PUBLIC_STUDIO
    assert '"format_lock_scope": "manual_name"' in PUBLIC_STUDIO
    assert 'elif scope == "manual_name":' in PUBLIC_STUDIO
    assert "rule_service.reset_item_overrides" in PUBLIC_STUDIO
    assert "rule_service.reset_all_overrides" in PUBLIC_STUDIO
    assert 'label="Reset This Category"' in PUBLIC_STUDIO
    assert 'label="Reset This Channel"' in PUBLIC_STUDIO


def test_item_lock_buttons_capture_live_item_style_not_global_preset() -> None:
    assert 'scope="category", target_id=self.category_id, target=category' in PUBLIC_STUDIO
    assert 'scope="channel", target_id=self.channel_id, target=channel' in PUBLIC_STUDIO
    assert "async def _save_live_target_format_lock(" in PUBLIC_STUDIO


def test_config_saves_invalidate_old_pending_previews() -> None:
    assert "def _invalidate_pending_for_guild" in PUBLIC_STUDIO
    save_start = PUBLIC_STUDIO.index("async def _save_design_options")
    save_end = PUBLIC_STUDIO.index("def _bot_missing_manage", save_start)
    assert "_invalidate_pending_for_guild(int(guild_id))" in PUBLIC_STUDIO[save_start:save_end]


def test_style_change_issue_controls_are_bound_to_preview_identity() -> None:
    assert "class StyleChangePreviewView" in PUBLIC_STUDIO
    assert "pending_created_at=pending_created_at" in PUBLIC_STUDIO
    assert "_pending_matches(pending, self.pending_created_at)" in PUBLIC_STUDIO
