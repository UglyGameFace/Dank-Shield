from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from stoney_verify.commands_ext import public_design_studio as legacy
from stoney_verify.commands_ext import public_design_studio_v2 as studio_v2
from stoney_verify.services import server_design_plan_service as plan_service


ROOT = Path(__file__).resolve().parents[1]
GROUP = (ROOT / "stoney_verify/commands_ext/public_design_group.py").read_text(encoding="utf-8")
ENHANCEMENTS = (ROOT / "stoney_verify/commands_ext/public_design_enhancements.py").read_text(encoding="utf-8")
COMMAND_GUARD = (ROOT / "stoney_verify/startup_guards/server_design_command_module_guard.py").read_text(encoding="utf-8")
SETUP_GUARD = (ROOT / "stoney_verify/startup_guards/setup_overview_command_guard.py").read_text(encoding="utf-8")
V2 = (ROOT / "stoney_verify/commands_ext/public_design_studio_v2.py").read_text(encoding="utf-8")
PLAN = (ROOT / "stoney_verify/services/server_design_plan_service.py").read_text(encoding="utf-8")


def run(coro: Any) -> Any:
    return asyncio.run(coro)


def _labels(view: Any) -> list[str]:
    return [str(getattr(child, "label", "") or "") for child in view.children]


def test_home_is_five_clear_workflows_not_a_mixed_control_panel() -> None:
    view = studio_v2.DesignHomeView({"theme_id": "gothic_clean", "strength": 4})
    assert _labels(view) == [
        "Design Server",
        "Edit One Item",
        "Review / Repair",
        "Saved Rules",
        "Rollback",
    ]
    assert all(child.__class__.__name__ != "DesignServerThemeSelect" for child in view.children)
    assert all(child.__class__.__name__ != "DesignServerStrengthSelect" for child in view.children)


def test_server_design_controls_live_only_inside_design_server_workflow() -> None:
    view = studio_v2.DesignServerView({"theme_id": "gothic_clean", "strength": 4})
    class_names = [child.__class__.__name__ for child in view.children]
    assert "DesignServerThemeSelect" in class_names
    assert "DesignServerStrengthSelect" in class_names
    assert "Preview Server" in _labels(view)
    assert "Separator Only" in _labels(view)


def test_active_registration_does_not_activate_design_runtime_monkey_patches() -> None:
    assert "public_design_studio_v2 as design" in GROUP
    assert "activate_public_design_enhancements" not in GROUP
    assert "server_design_strict_layout_guard" not in GROUP
    assert "server_design_majority_layout_guard" not in GROUP
    assert "server_design_strict_layout_guard" not in ENHANCEMENTS
    assert "server_design_majority_layout_guard" not in ENHANCEMENTS
    assert "command_guard.build_design_plan =" not in PLAN
    assert "DesignDoctorView =" not in PLAN


def test_design_command_guard_is_validation_only_not_registry_mutation() -> None:
    assert "validation-only" in COMMAND_GUARD or "validation shim" in COMMAND_GUARD
    for forbidden in (
        "commands_ext.COMMAND_MODULES =",
        "commands_ext.COMMAND_PROFILES =",
        "commands_ext._selected_command_modules =",
        "_install_selected_module_wrapper",
    ):
        assert forbidden not in COMMAND_GUARD


def test_setup_guard_no_longer_attaches_deprecated_design_command_shim() -> None:
    assert "server_design_studio_command_guard" not in SETUP_GUARD
    assert 'allowed.add("overview")' in SETUP_GUARD
    assert 'allowed.update({"overview", "design"})' not in SETUP_GUARD


def test_plan_defaults_preserve_gothic_pipe_and_visual_name_policy_without_global_mutation() -> None:
    before_protected = set(plan_service.studio.DEFAULT_PROTECTED_NAMES)
    options = plan_service.normalize_plan_options(
        {
            "theme_id": "gothic_clean",
            "strength": 4,
            "protection_rules": {"staff": "never"},
        },
        strict=True,
    )
    assert options["separator_id"] == "pipe_spaced"
    assert options["exact_match"] is True
    assert options["protection_rules"]["staff"] == "never"
    assert options["protection_rules"]["logs"] == "full"
    assert set(plan_service.studio.DEFAULT_PROTECTED_NAMES) == before_protected


def test_strict_plan_marks_saved_rule_layers_exact_without_changing_precedence() -> None:
    options = plan_service.normalize_plan_options(
        {
            "format_lock_global": {"enabled": True, "font": "fraktur", "exact_match": False},
            "category_format_locks": {"10": {"font": "bold_sans", "exact_match": False}},
            "channel_format_locks": {"20": {"font": "monospace", "exact_match": False}},
        },
        strict=True,
    )
    assert options["format_lock_global"]["exact_match"] is True
    assert options["category_format_locks"]["10"]["font"] == "bold_sans"
    assert options["category_format_locks"]["10"]["exact_match"] is True
    assert options["channel_format_locks"]["20"]["font"] == "monospace"
    assert options["channel_format_locks"]["20"]["exact_match"] is True


def test_drift_plan_calls_category_aware_native_service_not_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[str] = []
    guild = SimpleNamespace(id=1)

    monkeypatch.setattr(plan_service, "live_records", lambda _guild: [{"id": "1", "category_id": "9", "kind": "text", "name": "chat"}])

    def build_category_aware(studio: Any, options: Any, records: Any) -> tuple[dict[str, Any], dict[str, Any]]:
        events.append("category-aware")
        assert list(records)[0]["category_id"] == "9"
        return ({**dict(options), "category_format_locks": {"9": {"font": "bold_sans"}}}, {"9": {"font": "bold_sans"}})

    def annotate(studio: Any, items: Any, options: Any) -> list[dict[str, Any]]:
        events.append("annotate")
        return list(items)

    async def build_plan(_guild: Any, options: Any) -> list[dict[str, Any]]:
        events.append("legacy-plan")
        assert options["__use_live_majority_layout"] is True
        assert options["category_format_locks"]["9"]["exact_match"] is True
        return [{"channel_id": "1", "before": "chat", "after": "💬|chat", "status": "changed"}]

    monkeypatch.setattr(plan_service.majority, "build_category_aware_options", build_category_aware)
    monkeypatch.setattr(plan_service.majority, "annotate_category_aware_plan_items", annotate)
    monkeypatch.setattr(legacy, "build_design_plan", build_plan)

    items, options, analysis = run(plan_service.build_drift_repair_plan(guild, {"theme_id": "gothic_clean"}))
    assert events == ["category-aware", "legacy-plan", "annotate"]
    assert items[0]["status"] == "changed"
    assert options["__respect_saved_rules"] is True
    assert analysis["mode"] == "category_aware"


def test_all_primary_previews_share_one_reviewed_apply_component() -> None:
    assert "class ReviewedPreviewView" in V2
    assert V2.count("ReviewedPreviewView(") >= 3
    assert V2.count('label="Apply Reviewed Changes"') == 1
    assert "This preview is obsolete" in V2
    assert "current != before" in V2
    assert "_persist_rollback_snapshot" in V2


def test_setup_and_slash_command_converge_on_same_studio_module() -> None:
    bridge = (ROOT / "stoney_verify/commands_ext/public_design_bridge.py").read_text(encoding="utf-8")
    assert "public_design_studio_v2 as design" in bridge
    assert "public_design_studio_v2 as design" in GROUP
    assert "competing design screen" in bridge
