from __future__ import annotations

from pathlib import Path

from stoney_verify.commands_ext import public_design_studio as public_studio
from stoney_verify.services import server_design_plan_service as plan_service
from stoney_verify.services import server_design_studio as studio

PUBLIC = Path("stoney_verify/commands_ext/public_design_studio.py").read_text(encoding="utf-8")
V2 = Path("stoney_verify/commands_ext/public_design_studio_v2.py").read_text(encoding="utf-8")
PLAN = Path("stoney_verify/services/server_design_plan_service.py").read_text(encoding="utf-8")


def test_strength_levels_have_distinct_engine_capabilities() -> None:
    one = studio.build_styled_name("gaming", strength=1, font="fraktur", separator_id="bar_full", category_frame_id="lenticular", exact_match=True)
    two = studio.build_styled_name("gaming", strength=2, font="fraktur", separator_id="bar_full", category_frame_id="lenticular", exact_match=True)
    three = studio.build_styled_name("gaming", strength=3, font="fraktur", separator_id="bar_full", category_frame_id="lenticular", exact_match=True)
    four = studio.build_styled_name("gaming", kind="category", strength=4, font="fraktur", separator_id="bar_full", category_frame_id="lenticular", exact_match=True)
    assert one.separator_id == "" and one.font == "normal"
    assert two.separator_id == "bar_full" and two.font == "normal"
    assert three.separator_id == "bar_full" and three.font == "fraktur"
    assert four.category_frame_id == "lenticular"


def test_exact_protection_override_is_not_name_scoped() -> None:
    result = studio.build_styled_name("logs", protection_rules={"logs": "never"}, protection_mode="full", strength=4, exact_match=True)
    assert result.protected is False


def test_legacy_category_frame_protection_aliases_to_full() -> None:
    result = studio.build_styled_name("general", kind="category", protection_mode="category_frame_only", strength=4, category_frame_id="lenticular", exact_match=True)
    assert result.category_frame_id == "lenticular"


def test_consolidated_server_selectors_fail_closed_and_sync_active_global_lock() -> None:
    theme_start = V2.index("class DesignServerThemeSelect")
    font_start = V2.index("class DesignServerFontSelect", theme_start)
    strength_start = V2.index("class DesignServerStrengthSelect", font_start)
    separator_start = V2.index("class DesignServerSeparatorSelect", strength_start)
    frame_start = V2.index("class DesignServerCategoryFrameSelect", separator_start)
    end = V2.index("def _design_server_embed", frame_start)
    block = V2[theme_start:end]
    assert block.count("await legacy._save_options(interaction, options)") == 6
    assert block.count("legacy._sync_enabled_global_lock(options)") == 6
    assert 'options["font"] = selected' in block
    assert 'options.pop("font", None)' in block
    assert 'options["separator_id"] = selected' in block
    assert 'options.pop("separator_id", None)' in block
    assert 'options["category_frame_id"] = selected' in block
    assert 'options.pop("category_frame_id", None)' in block
    assert 'options["icon_mode"] = selected' in block
    assert "class DesignServerIconModeSelect" in block
    assert "picked_font" not in block
    assert 'options["strength"] = 4' not in block
    assert "class ThemeSelect" not in PUBLIC
    assert "class StrengthSelect" not in PUBLIC


def test_current_format_lock_never_silently_rewrites_strength() -> None:
    lock = public_studio._current_format_lock({"theme_id": "gothic_clean", "strength": 2})
    assert lock["strength"] == 2


def test_server_category_frame_resolver_prefers_explicit_then_theme_default() -> None:
    assert plan_service.theme_default_category_frame_id({"theme_id": "night_gothic"}) == "top_box"
    assert plan_service.effective_server_category_frame_id(
        {"theme_id": "night_gothic", "category_frame_id": "lenticular"}
    ) == "lenticular"
    assert plan_service.effective_server_category_frame_id(
        {"theme_id": "night_gothic", "category_frame_id": "not-a-frame"}
    ) == "top_box"


def test_normalized_plan_drops_invalid_server_category_frame_override() -> None:
    normalized = plan_service.normalize_plan_options(
        {
            "theme_id": "night_gothic",
            "strength": 4,
            "category_frame_id": "not-a-frame",
        },
        strict=True,
    )
    assert "category_frame_id" not in normalized
    assert plan_service.effective_server_category_frame_id(normalized) == "top_box"


def test_current_format_lock_honors_explicit_server_category_frame_override() -> None:
    custom = public_studio._current_format_lock(
        {
            "theme_id": "night_gothic",
            "strength": 4,
            "category_frame_id": "lenticular",
        }
    )
    assert custom["category_frame_id"] == "lenticular"

    invalid = public_studio._current_format_lock(
        {
            "theme_id": "night_gothic",
            "strength": 4,
            "category_frame_id": "not-a-frame",
        }
    )
    assert invalid["category_frame_id"] == "top_box"


def test_gothic_default_separator_matches_planner_ui_and_saved_global_rule() -> None:
    options = {"theme_id": "gothic_clean", "strength": 4}
    normalized = plan_service.normalize_plan_options(options, strict=True)
    assert normalized["separator_id"] == "pipe_spaced"
    assert plan_service.effective_server_separator_id(options) == "pipe_spaced"
    assert public_studio._current_format_lock(options)["separator_id"] == "pipe_spaced"


def test_current_format_lock_honors_explicit_server_font_override() -> None:
    lock = public_studio._current_format_lock(
        {"theme_id": "gothic_clean", "strength": 4, "font": "double_struck"}
    )
    assert lock["font"] == "double_struck"

    invalid = public_studio._current_format_lock(
        {"theme_id": "gothic_clean", "strength": 4, "font": "not-a-font"}
    )
    assert invalid["font"] == "fraktur"


def test_rule_counts_include_exact_protection_overrides() -> None:
    counts = public_studio._lock_count({"protection_item_rules": {"100": "never", "200": "full"}})
    assert counts["protection_items"] == 2


def test_rules_surface_has_one_counter_and_retired_submenus_are_absent() -> None:
    start = PUBLIC.index("def _format_locks_embed")
    end = PUBLIC.index("async def build_design_plan", start)
    block = PUBLIC[start:end]
    assert block.count("Exact manual names:") == 1
    for marker in (
        "class DesignDoctorButton",
        "class DesignDoctorView",
        "class StartHereButton",
        "class StartHereView",
        "class EditorsLocksButton",
        "class EditorsLocksView",
        "class AdvancedToolsView",
        "def _doctor_embed",
        "def _start_here_embed",
        "def _editors_locks_embed",
        "def _design_help_embed",
        "def _advanced_tools_embed",
    ):
        assert marker not in PUBLIC
    assert "def _compat_help_embed" not in V2


def test_legacy_recovery_guidance_uses_canonical_public_route() -> None:
    assert "Reopen `/dank home`" in PUBLIC
    assert "choose **Server Design**" in PUBLIC
    assert "Reopen `/dank design`" not in PUBLIC


def test_plan_service_uses_native_saved_rule_authority() -> None:
    assert "respect_saved_rules=True" in PLAN
    assert "build_category_aware_options" in PLAN
    assert "evaluate_repair_plan" in PLAN
