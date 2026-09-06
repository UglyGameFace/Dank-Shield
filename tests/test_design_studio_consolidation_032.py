from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from stoney_verify.commands_ext import public_design_group
from stoney_verify.commands_ext import public_design_studio as legacy
from stoney_verify.commands_ext import public_design_studio_v2 as studio_v2
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


def test_theme_and_strength_are_only_inside_design_server() -> None:
    assert "DesignServerThemeSelect" in V2
    assert "DesignServerStrengthSelect" in V2
    home_start = V2.index("class DesignHomeView")
    home_end = V2.index("def _snapshot_matches", home_start)
    home = V2[home_start:home_end]
    assert "DesignServerThemeSelect" not in home
    assert "DesignServerStrengthSelect" not in home


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


@pytest.mark.asyncio
async def test_category_aware_native_drift_plan_is_called(monkeypatch: pytest.MonkeyPatch) -> None:
    guild = SimpleNamespace(id=1)
    monkeypatch.setattr(plan_service, "live_records", lambda _guild: [{"id": "1", "name": "general", "kind": "text", "category_id": ""}])
    monkeypatch.setattr(plan_service.majority, "build_category_aware_options", lambda _studio, options, _records: ({**dict(options), "__category_aware_auto_detect": True}, {"summaries": {}}))
    monkeypatch.setattr(plan_service.majority, "annotate_category_aware_plan_items", lambda _studio, items, _options: items)
    monkeypatch.setattr(plan_service.legacy, "build_design_plan", AsyncMock(return_value=[]))
    monkeypatch.setattr(plan_service.repair_confidence, "evaluate_repair_plan", lambda _items, context: {"apply_allowed": True, "context": context})

    _items, plan_options, _analysis = await plan_service.build_drift_repair_plan(guild, {"theme_id": "gothic_clean"})
    assert plan_options["__category_aware_auto_detect"] is True
    assert plan_options["__repair_confidence_result"]["context"] == "smart_category_auto_detect"


def test_low_confidence_drift_plan_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    items = [{"channel_id": "1", "before": "general", "after": "x", "status": "changed", "warnings": [], "blockers": []}]
    confidence = {"apply_allowed": False, "blocked_lines": ["Unsafe simplification"], "review_lines": [], "context": "smart_category_auto_detect"}
    plan_service._fail_closed_on_low_confidence(items, confidence)
    assert items[0]["status"] == "failed"
    assert "Unsafe simplification" in items[0]["blockers"][0]


def test_smart_auto_detect_decorative_simplification_is_blocked() -> None:
    items = [{"channel_id": "1", "before": "╭─ 𝕊𝕋𝔸𝔽𝔽 ─╮", "after": "staff", "status": "changed", "warnings": [], "blockers": []}]
    result = repair_confidence.evaluate_repair_plan(items, context="smart_category_auto_detect")
    assert result["apply_allowed"] is False
    assert any("aesthetic" in str(line).lower() or "decorative" in str(line).lower() for line in result["blocked_lines"])


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
