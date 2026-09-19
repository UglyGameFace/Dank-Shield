from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GROUP = (ROOT / "stoney_verify/commands_ext/public_design_group.py").read_text(encoding="utf-8")
V2 = (ROOT / "stoney_verify/commands_ext/public_design_studio_v2.py").read_text(encoding="utf-8")
LEGACY = (ROOT / "stoney_verify/commands_ext/public_design_studio.py").read_text(encoding="utf-8")
PLAN = (ROOT / "stoney_verify/services/server_design_plan_service.py").read_text(encoding="utf-8")
STARTUP = (ROOT / "stoney_verify/startup_guards/__init__.py").read_text(encoding="utf-8")
REGISTRY = (ROOT / "stoney_verify/commands_ext/__init__.py").read_text(encoding="utf-8")

RETIRED = (
    ROOT / "stoney_verify/commands_ext/public_design_enhancements.py",
    ROOT / "stoney_verify/startup_guards/server_design_command_module_guard.py",
    ROOT / "stoney_verify/startup_guards/server_design_majority_layout_guard.py",
    ROOT / "stoney_verify/startup_guards/server_design_strict_layout_guard.py",
    ROOT / "stoney_verify/startup_guards/server_design_studio_command_guard.py",
)


def test_one_public_design_registration_owner() -> None:
    assert GROUP.count('@dank_group.command(name="design"') == 1
    assert "register_public_design_studio_command" not in V2
    assert "register_public_design_studio_command" not in LEGACY
    assert 'allowed.add("design")' not in GROUP
    assert "commands_ext._ALLOWED_DANK_CHILDREN =" not in GROUP
    assert '"public_design_group"' in REGISTRY
    assert '"design"' in REGISTRY


def test_retired_runtime_patch_design_modules_are_physically_absent() -> None:
    assert all(not path.exists() for path in RETIRED)
    assert "server_design_command_module_guard" not in STARTUP
    assert "server_design_majority_layout_guard" not in STARTUP
    assert "server_design_strict_layout_guard" not in STARTUP


def test_native_plan_has_no_retired_runtime_magic_flag() -> None:
    assert "__use_live_majority_layout" not in PLAN
    assert "majority.build_category_aware_options" in PLAN
    assert "majority.annotate_category_aware_plan_items" in PLAN


def test_separator_entry_uses_saved_authority_not_live_majority_guess() -> None:
    helper_start = V2.index("def _design_server_separator")
    helper_end = V2.index("def _design_server_embed", helper_start)
    helper = V2[helper_start:helper_end]
    assert "effective_draft_separator" in helper

    start = V2.index("async def separator_only")
    end = V2.index("async def back", start)
    block = V2[start:end]
    assert "_design_server_separator" in block
    assert "_infer_live_majority_context" not in block
    assert "legacy.StyleChangeView" not in block


def test_server_designer_owns_separator_selection_and_preview() -> None:
    server_start = V2.index("class DesignServerThemeSelect")
    server_end = V2.index("def _edit_one_embed", server_start)
    server = V2[server_start:server_end]

    assert "class DesignServerSeparatorSelect" in server
    assert 'label="Preview Separator Only"' in server
    assert 'legacy._build_channel_separator_style_change_plan' in server
    assert 'view=LegacyStyleChangePreviewView(' in server
    assert "legacy.StyleChangeView(" not in server


def test_server_designer_acknowledges_selects_before_config_io() -> None:
    for class_name, next_marker in (
        ("class DesignServerThemeSelect", "class DesignServerStrengthSelect"),
        ("class DesignServerStrengthSelect", "class DesignServerSeparatorSelect"),
        ("class DesignServerSeparatorSelect", "def _design_server_separator"),
    ):
        start = V2.index(class_name)
        end = V2.index(next_marker, start)
        block = V2[start:end]
        assert "await interaction.response.defer" in block
        assert block.index("await interaction.response.defer") < block.index("await _load_design_options")


def test_clean_redesign_is_native_confirmed_and_protection_preserving() -> None:
    server_start = V2.index("class DesignServerView")
    server_end = V2.index("def _edit_one_embed", server_start)
    server = V2[server_start:server_end]

    assert 'label="Start Clean Redesign"' in server
    assert "class CleanRedesignConfirmView" in V2
    assert "rule_service.reset_layout_overrides(options)" in V2
    assert "Clear Saved Design Overrides" in V2
    assert "Protection rules were kept" in V2


def test_design_navigation_acknowledges_before_config_reads() -> None:
    home_start = V2.index("class DesignHomeView")
    home_end = V2.index("def _snapshot_matches", home_start)
    home = V2[home_start:home_end]

    for method_name in ("async def design_server", "async def rules"):
        start = home.index(method_name)
        next_method = home.find("\n    @discord.ui.button", start + 1)
        block = home[start:] if next_method < 0 else home[start:next_method]
        assert "await interaction.response.defer" in block
        assert block.index("await interaction.response.defer") < block.index("await _load_design_options")


def test_io_backed_design_routes_acknowledge_before_work() -> None:
    exact_start = LEGACY.index("async def _open_exact_format_editor")
    exact_end = LEGACY.index("async def _update_exact_draft", exact_start)
    exact_open = LEGACY[exact_start:exact_end]
    assert exact_open.index("await interaction.response.defer") < exact_open.index("await _load_design_options")
    assert "await interaction.edit_original_response" in exact_open

    examples_start = LEGACY.index("async def layout_examples")
    examples_end = LEGACY.index("async def save_and_preview", examples_start)
    examples = LEGACY[examples_start:examples_end]
    assert examples.index("await interaction.response.defer") < examples.index("await _load_design_options")

    style_start = LEGACY.index("async def use_server_style")
    style_end = LEGACY.index("async def set_emoji", style_start)
    style = LEGACY[style_start:style_end]
    assert style.index("await interaction.response.defer") < style.index("await _load_design_options")

    undo_start = V2.index("async def _open_undo")
    undo_end = V2.index("class UndoConfirmView", undo_start)
    undo = V2[undo_start:undo_end]
    assert undo.index("await interaction.response.defer") < undo.index("await legacy._latest_rollback_snapshot")
    assert "await interaction.edit_original_response" in undo


def test_compatibility_rule_and_protection_actions_ack_before_storage() -> None:
    checks = (
        ("class LockRemoveButton", "class LockManagerPageButton", "_remove_format_lock"),
        ("class LockManagerPageButton", "class LockManagerView", "_load_design_options"),
        ("class CleanStaleLocksButton", "class BackToLocksOrDesignButton", "_clean_stale_format_locks"),
        ("class BackToLocksOrDesignButton", "# ---------------------------------------------------------------------------\n# Protection Manager", "_load_design_options"),
        ("async def allow_font_defaults", "async def restore_defaults", "_set_default_protection_rules"),
        ("async def restore_defaults", '@discord.ui.button(label="Pick Category"', "_set_default_protection_rules"),
        ("class ProtectionModeSelect", "class ProtectionModeView", "_save_protection_rule"),
        ("class StyleChangeSeparatorSelect", "class StyleChangeView", "_load_design_options"),
    )
    for start_marker, end_marker, io_marker in checks:
        start = LEGACY.index(start_marker)
        end = LEGACY.index(end_marker, start)
        block = LEGACY[start:end]
        assert "await interaction.response.defer" in block
        assert block.index("await interaction.response.defer") < block.index(io_marker)


def test_reviewed_preview_back_returns_to_originating_workflow() -> None:
    helper_start = V2.index("async def _return_from_reviewed_preview")
    helper_end = V2.index("class ReviewedPreviewView", helper_start)
    helper = V2[helper_start:helper_end]

    assert 'mode in {"preview_server_v2", "style_change_separator"}' in helper
    assert 'mode == "consistency_check_v2"' in helper
    assert 'mode == "category_editor"' in helper
    assert 'mode == "channel_editor"' in helper
    assert 'mode in {"category_exact_format", "channel_exact_format"}' in helper
    assert "_design_server_embed" in helper
    assert "_review_embed()" in helper
    assert "legacy._open_exact_format_editor" in helper

    preview_start = V2.index("class ReviewedPreviewView")
    preview_end = V2.index("class LegacyStyleChangePreviewView", preview_start)
    preview = V2[preview_start:preview_end]
    assert 'label="Back"' in preview
    assert "_return_from_reviewed_preview(interaction, self.pending_created_at)" in preview

    scope_start = LEGACY.index("async def _preview_scope")
    scope_end = LEGACY.index("def _category_editor_embed", scope_start)
    scope = LEGACY[scope_start:scope_end]
    assert '"category_id": str(int(category_id)) if category_id is not None else ""' in scope
    assert '"channel_id": str(int(channel_id)) if channel_id is not None else ""' in scope


def test_historical_design_mutators_are_removed() -> None:
    assert not list((ROOT / "tools").glob("apply_dank_design_*.py"))
    assert not list((ROOT / "tools").glob("apply_p0_int_design_*.py"))
