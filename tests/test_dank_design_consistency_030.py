from __future__ import annotations

from pathlib import Path

from stoney_verify.commands_ext import public_design_studio as public_studio
from stoney_verify.services import server_design_studio as studio

PUBLIC = Path("stoney_verify/commands_ext/public_design_studio.py").read_text(encoding="utf-8")


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


def test_home_selectors_fail_closed_and_sync_active_global_lock() -> None:
    theme_start = PUBLIC.index("class ThemeSelect")
    strength_start = PUBLIC.index("class StrengthSelect", theme_start)
    end = PUBLIC.index("def _consistency_summary", strength_start)
    block = PUBLIC[theme_start:end]
    assert block.count("await _save_options(interaction, options)") == 2
    assert block.count("_sync_enabled_global_lock(options)") == 2
    assert "picked_font" not in block
    assert 'options[\"strength\"] = 4' not in block


def test_current_format_lock_never_silently_rewrites_strength() -> None:
    lock = public_studio._current_format_lock({"theme_id": "gothic_clean", "strength": 2})
    assert lock["strength"] == 2


def test_rule_counts_include_exact_protection_overrides() -> None:
    counts = public_studio._lock_count({"protection_item_rules": {"100": "never", "200": "full"}})
    assert counts["protection_items"] == 2


def test_rules_ui_has_no_duplicate_exact_name_counter_or_joined_lines() -> None:
    start = PUBLIC.index("def _format_locks_embed")
    end = PUBLIC.index("async def build_design_plan", start)
    block = PUBLIC[start:end]
    assert block.count("Exact manual names:") == 1
    editor_start = PUBLIC.index("def _editors_locks_embed")
    editor_end = PUBLIC.index("class EditorsLocksButton", editor_start)
    editor = PUBLIC[editor_start:editor_end]
    assert "Exact manual names:" in editor
    assert "Exact protection overrides:" in editor


def test_doctor_does_not_treat_optional_category_locks_as_required() -> None:
    start = PUBLIC.index("def _doctor_embed")
    end = PUBLIC.index("class DesignDoctorButton", start)
    block = PUBLIC[start:end]
    assert "missing_locks" not in block
    assert "lock missing categories" not in block


def test_protection_editor_is_exact_id_scoped() -> None:
    assert "protection_item_rules" in PUBLIC
    start = PUBLIC.index("async def _save_protection_rule")
    end = PUBLIC.index("async def _set_default_protection_rules", start)
    block = PUBLIC[start:end]
    assert "target_id" in block
    assert "base_name" not in block


def test_help_uses_real_current_button_labels() -> None:
    assert "Preview Server" not in PUBLIC
    assert "**Preview Design**" not in PUBLIC
    assert "Edit Custom Format" not in PUBLIC
