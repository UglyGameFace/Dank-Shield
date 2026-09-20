from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from stoney_verify.commands_ext import public_design_group
from stoney_verify.commands_ext import public_design_studio as legacy
from stoney_verify.commands_ext import public_design_studio_v2 as studio_v2
from stoney_verify.services import server_design_apply_service as apply_service
from stoney_verify.services import server_design_majority_layout as majority
from stoney_verify.services import server_design_plan_service as plan_service
from stoney_verify.services import server_design_repair_confidence as repair_confidence
from stoney_verify.services import server_design_studio as studio

ROOT = Path(__file__).resolve().parents[1]
V2 = (ROOT / "stoney_verify/commands_ext/public_design_studio_v2.py").read_text(encoding="utf-8")
LEGACY = (ROOT / "stoney_verify/commands_ext/public_design_studio.py").read_text(encoding="utf-8")
GROUP = (ROOT / "stoney_verify/commands_ext/public_design_group.py").read_text(encoding="utf-8")
BRIDGE = (ROOT / "stoney_verify/commands_ext/public_design_bridge.py").read_text(encoding="utf-8")
SETUP_GUARD = (ROOT / "stoney_verify/startup_guards/setup_overview_command_guard.py").read_text(encoding="utf-8")


def _button_labels(view: object) -> list[str]:
    return [str(getattr(item, "label", "")) for item in getattr(view, "children", []) if getattr(item, "label", None)]


def test_home_has_exactly_five_explicit_workflows() -> None:
    view = studio_v2.DesignHomeView({})
    assert _button_labels(view) == [
        "Design Entire Server",
        "Edit One Category / Channel",
        "Fix Inconsistent Names",
        "Saved Rules & Protection",
        "Undo Last Apply",
    ]


def test_clean_redesign_button_only_enables_when_layout_exceptions_exist() -> None:
    clean = studio_v2.DesignServerView({})
    clean_button = next(item for item in clean.children if getattr(item, "custom_id", "") == "dank_design_v2:clean_redesign")
    assert clean_button.disabled is True

    crossed = studio_v2.DesignServerView({"channel_format_locks": {"123": {"enabled": True}}})
    crossed_button = next(item for item in crossed.children if getattr(item, "custom_id", "") == "dank_design_v2:clean_redesign")
    assert crossed_button.disabled is False


def test_server_style_controls_are_only_inside_design_server() -> None:
    assert "DesignServerThemeSelect" in V2
    assert "DesignServerFontSelect" in V2
    assert "DesignServerStrengthSelect" in V2
    assert "DesignServerCategoryFrameSelect" in V2
    home_start = V2.index("class DesignHomeView")
    home_end = V2.index("def _snapshot_matches", home_start)
    home = V2[home_start:home_end]
    assert "DesignServerThemeSelect" not in home
    assert "DesignServerFontSelect" not in home
    assert "DesignServerStrengthSelect" not in home
    assert "DesignServerCategoryFrameSelect" not in home


def test_server_font_picker_fits_discord_and_exposes_full_catalog() -> None:
    view = studio_v2.DesignServerView({"theme_id": "gothic_clean", "strength": 4})
    picker = next(item for item in view.children if isinstance(item, studio_v2.DesignServerFontSelect))
    assert len(picker.options) == len(studio.DESIGN_FONT_STYLES) + 1
    assert len(picker.options) <= 25
    values = {str(option.value) for option in picker.options}
    assert {"__theme__", "sans", "double_struck", "fraktur", "small_caps"} <= values
    assert any("𝕘" in str(option.description) for option in picker.options if option.value == "double_struck")


def test_server_separator_picker_can_follow_theme_or_hold_custom_choice() -> None:
    theme_default = studio_v2.DesignServerView(
        {"theme_id": "gaming_arcade", "strength": 4}
    )
    picker = next(
        item for item in theme_default.children
        if isinstance(item, studio_v2.DesignServerSeparatorSelect)
    )
    defaults = [option for option in picker.options if option.default]
    assert len(defaults) == 1
    assert defaults[0].value == "__theme__"
    assert "Theme Default" in str(defaults[0].label)

    custom = studio_v2.DesignServerView(
        {"theme_id": "gaming_arcade", "strength": 4, "separator_id": "middle_dot"}
    )
    custom_picker = next(
        item for item in custom.children
        if isinstance(item, studio_v2.DesignServerSeparatorSelect)
    )
    custom_defaults = [option for option in custom_picker.options if option.default]
    assert len(custom_defaults) == 1
    assert custom_defaults[0].value == "middle_dot"



def test_server_category_frame_browser_exposes_full_catalog_without_row_overflow() -> None:
    grouped_ids = [
        frame_id
        for _group_label, frame_ids in studio.CATEGORY_FRAME_GROUPS
        for frame_id in frame_ids
    ]
    assert len(grouped_ids) == 80
    assert len(grouped_ids) == len(set(grouped_ids))
    assert set(grouped_ids) <= set(studio.CATEGORY_FRAMES_BY_ID)
    assert len([frame for frame in studio.CATEGORY_FRAMES if frame.id in set(grouped_ids)]) == 80

    view = studio_v2.DesignServerView({"theme_id": "night_gothic", "strength": 5})
    frame_button = next(
        item for item in view.children
        if getattr(item, "custom_id", "") == "dank_design_v2:category_frame"
    )
    assert str(frame_button.label) == "Frame"

    buttons = [item for item in view.children if getattr(item, "label", None) is not None]
    assert len(buttons) == 5
    assert all(getattr(item, "row", None) == 4 for item in buttons)

    seen: set[str] = set()
    groups = studio_v2._category_frame_groups()
    assert len(studio.CATEGORY_FRAME_GROUPS) == 10
    assert len(groups) >= 10
    for page in range(len(groups)):
        page_view = studio_v2.DesignServerCategoryFrameView(
            {"theme_id": "night_gothic", "strength": 5},
            page=page,
        )
        picker = next(
            item for item in page_view.children
            if isinstance(item, studio_v2.DesignServerCategoryFrameSelect)
        )
        assert len(picker.options) <= 25
        assert str(picker.options[0].value) == "__theme__"
        seen.update(str(option.value) for option in picker.options if str(option.value) != "__theme__")

    assert set(grouped_ids) <= seen

    theme_default_view = studio_v2.DesignServerCategoryFrameView(
        {"theme_id": "night_gothic", "strength": 5},
        page=0,
    )
    picker = next(
        item for item in theme_default_view.children
        if isinstance(item, studio_v2.DesignServerCategoryFrameSelect)
    )
    defaults = [option for option in picker.options if option.default]
    assert len(defaults) == 1
    assert defaults[0].value == "__theme__"
    assert "Top Box" in str(defaults[0].label)

    custom_options = {
        "theme_id": "night_gothic",
        "strength": 5,
        "category_frame_id": "ribbon_heart",
    }
    custom_page = studio_v2._category_frame_page_for(custom_options)
    assert custom_page == len(studio.CATEGORY_FRAME_GROUPS) - 1
    custom_view = studio_v2.DesignServerCategoryFrameView(custom_options, page=custom_page)
    custom_picker = next(
        item for item in custom_view.children
        if isinstance(item, studio_v2.DesignServerCategoryFrameSelect)
    )
    custom_defaults = [option for option in custom_picker.options if option.default]
    assert len(custom_defaults) == 1
    assert custom_defaults[0].value == "ribbon_heart"

    invalid_view = studio_v2.DesignServerCategoryFrameView(
        {
            "theme_id": "night_gothic",
            "strength": 5,
            "category_frame_id": "not-a-frame",
        },
        page=0,
    )
    invalid_picker = next(
        item for item in invalid_view.children
        if isinstance(item, studio_v2.DesignServerCategoryFrameSelect)
    )
    invalid_defaults = [option for option in invalid_picker.options if option.default]
    assert len(invalid_defaults) == 1
    assert invalid_defaults[0].value == "__theme__"


def test_every_category_frame_round_trips_through_parser_and_majority_detection() -> None:
    assert len(studio.CATEGORY_FRAMES) == 80
    assert len(studio.CATEGORY_FRAMES_BY_ID) == 80
    for frame in studio.CATEGORY_FRAMES:
        rendered = studio.category_frame_preview(frame.id, emoji="🎮", name="gaming")
        assert len(rendered) <= studio.DISCORD_NAME_LIMIT
        assert studio.normalize_base_name(rendered) == "gaming"
        detected = majority.detect_category_frame(studio, rendered)
        assert detected["id"] == frame.id


def test_new_category_frame_families_are_grouped_and_reachable() -> None:
    grouped = {label: tuple(frame_ids) for label, frame_ids in studio.CATEGORY_FRAME_GROUPS}
    assert set(("Divider & Rails", "Royal & Luxury", "Nature & Magic", "Gaming & Cyber", "Cute & Soft")) <= set(grouped)
    assert "pointer_rail" in grouped["Divider & Rails"]
    assert "luxury_diamond" in grouped["Royal & Luxury"]
    assert "mystic" in grouped["Nature & Magic"]
    assert "circuit_gate" in grouped["Gaming & Cyber"]
    assert "ribbon_heart" in grouped["Cute & Soft"]
    assert all(len(frame_ids) <= 24 for frame_ids in grouped.values())


def test_exact_category_editor_can_browse_frames_beyond_first_select_page() -> None:
    initial = legacy.ExactFrameSelect("category", 123, "line")
    values = {str(option.value) for option in initial.options}
    assert len(initial.options) <= 25
    assert legacy.EXACT_FRAME_BROWSE_VALUE in values

    late_frame = "ribbon_heart"
    late = legacy.ExactFrameSelect("category", 123, late_frame)
    late_defaults = [option for option in late.options if option.default]
    assert len(late_defaults) == 1
    assert late_defaults[0].value == late_frame
    assert legacy.EXACT_FRAME_BROWSE_VALUE in {str(option.value) for option in late.options}

    invalid = legacy.ExactFrameSelect("category", 123, "not-a-frame")
    invalid_defaults = [option for option in invalid.options if option.default]
    assert len(invalid_defaults) == 1
    assert invalid_defaults[0].value == "plain"

    page = legacy._exact_frame_page_for(late_frame)
    browser = legacy.ExactFrameBrowserView(
        SimpleNamespace(),
        scope="category",
        target_id=123,
        lock={"category_frame_id": late_frame},
        page=page,
    )
    picker = next(
        item for item in browser.children
        if isinstance(item, legacy.ExactFrameBrowserSelect)
    )
    defaults = [option for option in picker.options if option.default]
    assert len(defaults) == 1
    assert defaults[0].value == late_frame
    assert len(picker.options) <= 25


def test_gothic_theme_default_separator_matches_real_preview_plan() -> None:
    options = {"theme_id": "gothic_clean", "strength": 4}
    assert studio_v2._design_server_separator(options) == "pipe_spaced"
    view = studio_v2.DesignServerView(options)
    picker = next(
        item for item in view.children
        if isinstance(item, studio_v2.DesignServerSeparatorSelect)
    )
    selected = [option for option in picker.options if option.default]
    assert len(selected) == 1
    assert selected[0].value == "__theme__"
    assert "Spaced" in str(selected[0].label) or "|" in str(selected[0].description)


def test_server_design_preview_uses_current_guild_names_not_project_specific_examples(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        legacy,
        "_designable_editor_categories",
        lambda _guild: [SimpleNamespace(name="Community Hub")],
    )
    monkeypatch.setattr(
        legacy,
        "_editable_channels",
        lambda _guild: [SimpleNamespace(name="general-chat")],
    )

    category_name, channel_name = studio_v2._design_server_example_source_names(SimpleNamespace())
    assert category_name == "community-hub"
    assert channel_name == "general-chat"
    assert "the-420-lobby" not in V2

    embed = studio_v2._design_server_embed(
        SimpleNamespace(),
        {"theme_id": "modern_minimal", "strength": 4},
    )
    fields = {str(field.name): str(field.value) for field in embed.fields}
    assert "community-hub" in studio.normalize_base_name(fields["Style example"])
    assert "the-420-lobby" not in fields["Style example"]


def test_server_design_embed_shows_font_frame_sources_and_live_style_examples() -> None:
    embed = studio_v2._design_server_embed(
        SimpleNamespace(),
        {"theme_id": "double_struck_luxe", "strength": 4},
    )
    fields = {str(field.name): str(field.value) for field in embed.fields}
    assert "Font" in fields
    assert "Category frame" in fields
    assert "Style example" in fields
    assert "Double-Struck" in fields["Font"]
    assert "𝕘" in fields["Font"]
    assert "theme default" in fields["Category frame"]
    assert "Lenticular" in fields["Category frame"]

    custom = studio_v2._design_server_embed(
        SimpleNamespace(),
        {
            "theme_id": "double_struck_luxe",
            "strength": 4,
            "category_frame_id": "top_box",
        },
    )
    custom_fields = {str(field.name): str(field.value) for field in custom.fields}
    assert "Top Box" in custom_fields["Category frame"]
    assert "custom override" in custom_fields["Category frame"]


def test_active_registration_does_not_activate_runtime_monkey_patches() -> None:
    assert "activate_public_design_enhancements" not in GROUP
    assert "server_design_majority_layout_guard" not in GROUP
    assert "server_design_strict_layout_guard" not in GROUP
    assert "public_design_studio_v2 as design" in GROUP


def test_command_guard_is_validation_only() -> None:
    assert not (ROOT / "stoney_verify/startup_guards/server_design_command_module_guard.py").exists()
    assert "_selected_command_modules =" not in GROUP


def test_setup_guard_does_not_attach_deprecated_design_command() -> None:
    assert "server_design_studio_command_guard" not in SETUP_GUARD


def test_gothic_pipe_and_visual_name_policy_are_preserved_without_global_mutation() -> None:
    before_theme = studio.THEMES_BY_ID["gothic_clean"].channel_separator
    before_protected = set(studio.DEFAULT_PROTECTED_NAMES)

    normalized = plan_service.normalize_plan_options({"theme_id": "gothic_clean", "strength": 4}, strict=True)

    assert normalized["separator_id"] == "pipe_spaced"
    assert normalized["protection_rules"]["staff"] == "full"
    assert studio.THEMES_BY_ID["gothic_clean"].channel_separator == before_theme
    assert set(studio.DEFAULT_PROTECTED_NAMES) == before_protected


def test_strict_saved_rule_layers_remain_exact_and_preserve_precedence() -> None:
    options = {
        "theme_id": "gothic_clean",
        "strength": 4,
        "format_lock_global": {"enabled": True, "font": "normal", "separator_id": "bar_full", "strength": 2},
        "category_format_locks": {"10": {"enabled": True, "font": "fraktur", "separator_id": "bar_heavy", "strength": 3}},
        "channel_format_locks": {"20": {"enabled": True, "font": "monospace", "separator_id": "bar_thin", "strength": 4}},
    }
    normalized = plan_service.normalize_plan_options(options, strict=True)
    assert normalized["format_lock_global"]["exact_match"] is True
    assert normalized["category_format_locks"]["10"]["exact_match"] is True
    assert normalized["channel_format_locks"]["20"]["exact_match"] is True
    assert normalized["format_lock_global"]["separator_id"] == "bar_full"
    assert normalized["category_format_locks"]["10"]["separator_id"] == "bar_heavy"
    assert normalized["channel_format_locks"]["20"]["separator_id"] == "bar_thin"


def test_category_aware_native_drift_plan_is_called(monkeypatch: pytest.MonkeyPatch) -> None:
    guild = SimpleNamespace(id=1)
    monkeypatch.setattr(plan_service, "live_records", lambda _guild: [{"id": "1", "name": "general", "kind": "text", "category_id": ""}])
    monkeypatch.setattr(plan_service.majority, "build_category_aware_options", lambda _studio, options, _records: ({**dict(options), "__category_aware_auto_detect": True}, {"summaries": {}}))
    monkeypatch.setattr(plan_service.majority, "annotate_category_aware_plan_items", lambda _studio, items, _options: items)
    monkeypatch.setattr(legacy, "build_design_plan", AsyncMock(return_value=[]))
    monkeypatch.setattr(plan_service.repair_confidence, "evaluate_repair_plan", lambda _items, context: {"apply_allowed": True, "context": context})

    _items, plan_options, _analysis = asyncio.run(plan_service.build_drift_repair_plan(guild, {"theme_id": "gothic_clean"}))
    assert plan_options["__category_aware_auto_detect"] is True
    assert plan_options["__repair_confidence_result"]["context"] == "smart_category_auto_detect"


def test_low_confidence_drift_plan_fails_closed() -> None:
    items = [{"channel_id": "1", "before": "general", "after": "x", "status": "changed", "warnings": [], "blockers": []}]
    confidence = {"apply_allowed": False, "blocked_lines": ["Unsafe simplification"], "review_lines": [], "context": "smart_category_auto_detect"}
    guarded = plan_service._fail_closed_on_low_confidence(items, confidence)
    assert guarded[0]["status"] == "failed"
    assert "confidence is too low" in guarded[0]["blockers"][0]
    assert items[0]["status"] == "changed", "pure guard should not mutate caller rows in place"


def test_smart_auto_detect_decorative_simplification_is_blocked() -> None:
    item = {"channel_id": "1", "before": "╭─ 𝕊𝕋𝔸𝔽𝔽 ─╮", "after": "staff", "status": "changed", "warnings": [], "blockers": []}
    scored = repair_confidence.score_repair_item(item, context="smart_category_auto_detect")
    assert scored["classification"] == repair_confidence.BLOCKED_AESTHETIC_DOWNGRADE
    assert "simplify" in str(scored["reason"]).lower() or "strip" in str(scored["reason"]).lower()
    result = repair_confidence.evaluate_repair_plan([item], context="smart_category_auto_detect")
    assert result["apply_allowed"] is False
    assert result["counts"].get(repair_confidence.BLOCKED_AESTHETIC_DOWNGRADE) == 1


def test_successful_apply_snapshot_failure_falls_back_to_memory_without_revert(monkeypatch: pytest.MonkeyPatch) -> None:
    legacy._LAST_SNAPSHOTS.clear()

    async def broken_persist(_guild_id: int, _snapshot: dict[str, object]) -> None:
        raise OSError("read-only snapshot store")

    monkeypatch.setattr(legacy, "_persist_rollback_snapshot", broken_persist)
    prepared = apply_service.PreparedRename(
        channel_id=123,
        channel=SimpleNamespace(name="new-name"),
        item={
            "channel_id": "123",
            "before": "old-name",
            "after": "new-name",
            "status": "changed",
        },
        before="old-name",
        after="new-name",
    )

    snapshot, durable = asyncio.run(
        studio_v2._store_snapshot_with_memory_fallback(999, 42, [prepared])
    )

    assert snapshot is not None
    assert durable is False
    assert snapshot["durable"] is False
    assert snapshot["items"][0]["old_name"] == "old-name"
    assert snapshot["items"][0]["new_name"] == "new-name"
    assert legacy._LAST_SNAPSHOTS["999"][-1]["durable"] is False


def test_one_reviewed_apply_component() -> None:
    assert V2.count('custom_id="dank_design_v2:apply"') == 1
    assert "Apply Reviewed Changes" in V2


def test_setup_and_slash_command_converge_on_v2() -> None:
    assert "public_design_studio_v2 as design" in GROUP
    assert "public_design_studio_v2 as design" in BRIDGE
    assert "design.DesignHomeView(options)" in BRIDGE


def test_legacy_bridge_is_small_explicit_navigation_and_apply_boundary() -> None:
    assert legacy._home_embed is studio_v2._home_embed
    assert not hasattr(legacy, "_start_here_embed")
    assert not hasattr(legacy, "_design_help_embed")
    assert legacy.DesignHomeView is studio_v2.DesignHomeView
    assert legacy.DesignPreviewView is studio_v2.ReviewedPreviewView
    assert legacy.StyleChangePreviewView is studio_v2.LegacyStyleChangePreviewView

    bridge_start = V2.index("def _install_legacy_compatibility_bridge")
    bridge_end = V2.index("\n\n_install_legacy_compatibility_bridge()", bridge_start)
    bridge = V2[bridge_start:bridge_end]
    for required in (
        "legacy._home_embed = _home_embed",
        "legacy.DesignHomeView = DesignHomeView",
        "legacy.DesignPreviewView = ReviewedPreviewView",
        "legacy.StyleChangePreviewView = LegacyStyleChangePreviewView",
    ):
        assert required in bridge
    for forbidden in (
        "legacy._start_here_embed =",
        "legacy._design_help_embed =",
        "legacy.build_design_plan =",
        "legacy.DesignDoctorView =",
        "legacy._load_design_options =",
        "legacy.register_public_design_studio_command =",
    ):
        assert forbidden not in bridge


def test_all_surviving_legacy_back_paths_resolve_the_consolidated_home() -> None:
    assert LEGACY.count("view=DesignHomeView(options)") >= 4
    assert legacy.DesignHomeView is studio_v2.DesignHomeView
    assert legacy._home_embed is studio_v2._home_embed
    for retired in (
        "class FormatLocksButton",
        "class DesignCategoryEditorButton",
        "class DesignChannelEditorButton",
        "class ProtectionManagerButton",
        "class DesignDoneView",
    ):
        assert retired not in LEGACY


def test_retired_design_command_guard_is_not_a_startup_dependency() -> None:
    startup = (ROOT / "stoney_verify/startup_guards/__init__.py").read_text(encoding="utf-8")
    assert "server_design_command_module_guard" not in startup
    assert "allowed.add(\"design\")" not in GROUP
    assert "commands_ext._ALLOWED_DANK_CHILDREN =" not in GROUP


def test_setup_guard_no_longer_attaches_deprecated_design_command_shim() -> None:
    assert "server_design_studio_command_guard" not in SETUP_GUARD
    assert 'allowed.add("overview")' in SETUP_GUARD
    assert 'allowed.update({"overview", "design"})' not in SETUP_GUARD


def test_plan_defaults_preserve_gothic_pipe_and_visual_name_policy_without_global_mutation() -> None:
    before_protected = set(plan_service.studio.DEFAULT_PROTECTED_NAMES)
    options = plan_service.normalize_plan_options(
        {"theme_id": "gothic_clean", "strength": 4, "protection_rules": {"staff": "never"}},
        strict=True,
    )
    assert options["separator_id"] == "pipe_spaced"
    assert options["protection_rules"]["staff"] == "never"
    assert set(plan_service.studio.DEFAULT_PROTECTED_NAMES) == before_protected


def test_separator_picker_catalogs_expose_mixed_layouts_within_discord_limits() -> None:
    server_view = studio_v2.DesignServerView({"theme_id": "gothic_clean", "strength": 4})
    server_picker = next(
        item for item in server_view.children
        if isinstance(item, studio_v2.DesignServerSeparatorSelect)
    )
    server_values = {str(option.value) for option in server_picker.options}

    assert len(studio.SERVER_DESIGN_SEPARATOR_IDS) == 24
    assert len(server_picker.options) == 25
    assert len(server_picker.options) <= 25
    assert {
        "__theme__",
        "dash",
        "double_dash",
        "pipe_compact",
        "pipe_spaced",
        "double_pipe",
        "double_colon",
    } <= server_values

    assert len(studio.EXACT_EDITOR_SEPARATOR_IDS) == 25
    assert {
        "double_dash",
        "pipe_compact",
        "pipe_spaced",
        "double_pipe",
    } <= set(studio.EXACT_EDITOR_SEPARATOR_IDS)
    assert legacy.EDITOR_SEPARATOR_IDS == studio.EXACT_EDITOR_SEPARATOR_IDS
    assert legacy.STYLE_CHANGE_SEPARATOR_IDS == studio.SERVER_DESIGN_SEPARATOR_IDS


def test_existing_separator_picker_choices_remain_available_after_expansion() -> None:
    server_values = set(studio.SERVER_DESIGN_SEPARATOR_IDS)
    assert {
        "none",
        "bar_heavy",
        "bar_thin",
        "bar_full",
        "bar_medium",
        "bar_bold",
        "bar_block",
        "dash",
        "middle_dot",
        "sparkle",
        "bracket_corner",
        "bracket_lenticular",
    } <= server_values

    exact_values = set(studio.EXACT_EDITOR_SEPARATOR_IDS)
    assert {
        "none",
        "bar_full",
        "bar_thin",
        "bar_heavy",
        "dash",
        "en_dash",
        "em_dash",
        "middle_dot",
        "bullet",
        "katakana_dot",
        "colon",
        "single_angle",
        "tri_right",
        "tri_small",
        "premium_sparkle",
        "premium_thin_sparkle",
        "sparkle_small",
        "small_dot",
        "presentation_bar",
        "bracket_corner",
        "bracket_lenticular",
    } <= exact_values


def test_intentional_square_emoji_is_preserved_in_mixed_server_layout() -> None:
    assert legacy._direct_rename_has_unsafe_channel_icon("⬜--mods-only") is False
    assert legacy._direct_rename_has_unsafe_channel_icon("#️⃣--general") is True

    after, warnings, blockers = legacy._style_change_separator_after(
        "⬜--mods-only",
        "double_dash",
    )

    assert after == "⬜--mods-only"
    assert warnings == []
    assert blockers == []


def test_unsafe_keycap_blocker_still_routes_to_icon_repair() -> None:
    item = {
        "status": "failed",
        "blockers": ["Leading icon uses the unsafe #️⃣ keycap form. Choose a different emoji/icon first."],
    }

    assert legacy._style_change_missing_emoji_items([item]) == [item]
