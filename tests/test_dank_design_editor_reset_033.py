from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from stoney_verify.commands_ext import public_design_studio as legacy
from stoney_verify.services import server_design_plan_service as plan_service
from stoney_verify.services import server_design_rule_service as rules


ROOT = Path(__file__).resolve().parents[1]
LEGACY_SOURCE = (ROOT / "stoney_verify/commands_ext/public_design_studio.py").read_text(encoding="utf-8")
V2_SOURCE = (ROOT / "stoney_verify/commands_ext/public_design_studio_v2.py").read_text(encoding="utf-8")


def run(coro: Any) -> Any:
    return asyncio.run(coro)


def test_explicit_saved_separator_beats_theme_default() -> None:
    options = {
        "theme_id": "gothic_clean",
        "strength": 4,
        "separator_id": "bar_heavy",
    }

    assert rules.effective_draft_separator(options, theme_separator="pipe_spaced") == "bar_heavy"
    assert legacy._current_format_lock(options)["separator_id"] == "bar_heavy"


def test_separator_apply_persists_only_separator_component_and_exact_result() -> None:
    options = {
        "theme_id": "gothic_clean",
        "strength": 4,
        "separator_id": "bar_full",
        "format_lock_global": {
            "enabled": True,
            "font": "fraktur",
            "separator_id": "bar_full",
            "category_frame_id": "line",
            "icon_mode": "replace_missing",
        },
        "category_format_locks": {
            "10": {
                "font": "monospace",
                "separator_id": "pipe_spaced",
                "category_frame_id": "box",
                "icon_mode": "clear",
            }
        },
        "channel_format_locks": {
            "20": {
                "font": "serif_bold",
                "separator_id": "bar_thin",
                "category_frame_id": "plain",
                "icon_mode": "keep_existing",
            }
        },
        "manual_name_overrides": {
            "20": {"name": "🔥｜general", "scope": "channel", "locked_at": "old"},
            "30": {"name": "🎮｜games", "scope": "channel"},
        },
        "protection_rules": {"staff": "never"},
    }

    applied = [SimpleNamespace(channel_id=20, after="🔥┃general")]
    updated = rules.persist_separator_choice(options, separator_id="bar_heavy", applied_rows=applied)

    assert updated["separator_id"] == "bar_heavy"
    assert updated["format_lock_global"]["separator_id"] == "bar_heavy"
    assert updated["category_format_locks"]["10"]["separator_id"] == "bar_heavy"
    assert updated["channel_format_locks"]["20"]["separator_id"] == "bar_heavy"

    # Separator-only means separator-only. Other styling survives untouched.
    assert updated["format_lock_global"]["font"] == "fraktur"
    assert updated["category_format_locks"]["10"]["font"] == "monospace"
    assert updated["category_format_locks"]["10"]["category_frame_id"] == "box"
    assert updated["channel_format_locks"]["20"]["font"] == "serif_bold"
    assert updated["channel_format_locks"]["20"]["icon_mode"] == "keep_existing"
    assert updated["protection_rules"] == {"staff": "never"}

    # An exact manual name changed by the reviewed batch must not immediately
    # fight the new separator on the next saved-design preview.
    assert updated["manual_name_overrides"]["20"]["name"] == "🔥┃general"
    assert updated["manual_name_overrides"]["30"]["name"] == "🎮｜games"


def test_reset_this_item_removes_every_same_item_override() -> None:
    options = {
        "format_lock_global": {"enabled": True, "separator_id": "bar_full"},
        "category_format_locks": {"77": {"font": "fraktur"}, "88": {"font": "normal"}},
        "channel_format_locks": {"77": {"font": "monospace"}, "99": {"font": "normal"}},
        "manual_name_overrides": {"77": {"name": "exact"}, "55": {"name": "keep"}},
        "protection_item_rules": {"77": "never", "44": "full"},
        "protection_rules": {"staff": "never"},
    }

    updated, removed = rules.reset_item_overrides(options, target_id=77)

    assert removed == {
        "category": True,
        "channel": True,
        "manual_name": True,
        "protection_item": True,
    }
    assert "77" not in updated["category_format_locks"]
    assert "77" not in updated["channel_format_locks"]
    assert "77" not in updated["manual_name_overrides"]
    assert "77" not in updated["protection_item_rules"]
    assert updated["category_format_locks"]["88"]["font"] == "normal"
    assert updated["channel_format_locks"]["99"]["font"] == "normal"
    assert updated["manual_name_overrides"]["55"]["name"] == "keep"
    assert updated["protection_item_rules"]["44"] == "full"
    assert updated["format_lock_global"]["enabled"] is True
    assert updated["protection_rules"] == {"staff": "never"}


def test_reset_all_design_overrides_really_clears_all_override_layers() -> None:
    options = {
        "theme_id": "gothic_clean",
        "strength": 3,
        "separator_id": "bar_heavy",
        "format_lock_global": {"enabled": True},
        "category_format_locks": {"1": {"font": "fraktur"}},
        "channel_format_locks": {"2": {"font": "normal"}},
        "manual_name_overrides": {"2": {"name": "exact"}},
        "protection_item_rules": {"2": "never"},
        "protection_rules": {"staff": "never", "logs": "font_only"},
    }

    updated = rules.reset_all_overrides(options)

    assert updated["format_lock_global"] == {}
    assert updated["category_format_locks"] == {}
    assert updated["channel_format_locks"] == {}
    assert updated["manual_name_overrides"] == {}
    assert updated["protection_item_rules"] == {}
    assert updated["protection_rules"] == {}

    # Resetting overrides must not secretly reset the ordinary server draft.
    assert updated["theme_id"] == "gothic_clean"
    assert updated["strength"] == 3
    assert updated["separator_id"] == "bar_heavy"


def test_separator_protection_modes_are_cumulative_and_exact_safe() -> None:
    assert rules.protection_allows_separator("never") is False
    assert rules.protection_allows_separator("emoji_only") is False
    assert rules.protection_allows_separator("separator_only") is True
    assert rules.protection_allows_separator("font_only") is True
    assert rules.protection_allows_separator("full") is True

    source = LEGACY_SOURCE[LEGACY_SOURCE.index("def _build_channel_separator_style_change_plan"):LEGACY_SOURCE.index("def _style_change_embed")]
    assert "_protection_item_rules(options)" in source
    assert "_inherited_protection_mode(options, base)" in source
    assert "rule_service.protection_allows_separator(protection)" in source


def test_category_and_channel_preview_use_native_scoped_planner_not_retired_magic() -> None:
    source = LEGACY_SOURCE[LEGACY_SOURCE.index("async def _preview_scope("):LEGACY_SOURCE.index("class DesignCategoryEditorButton")]
    assert "plan_service.build_scoped_repair_plan" in source
    assert 'repair_options["__use_live_majority_layout"]' not in source
    assert 'mode in {"category_editor", "channel_editor"}' in source


@pytest.mark.parametrize(
    ("category_id", "channel_id", "expected_ids"),
    [
        (10, None, ["10", "11"]),
        (None, 20, ["20"]),
    ],
)
def test_native_scoped_planner_filters_before_confidence(
    monkeypatch: pytest.MonkeyPatch,
    category_id: int | None,
    channel_id: int | None,
    expected_ids: list[str],
) -> None:
    all_items = [
        {"channel_id": "10", "category_id": "", "kind": "category", "status": "changed", "before": "a", "after": "b"},
        {"channel_id": "11", "category_id": "10", "kind": "text", "status": "changed", "before": "c", "after": "d"},
        {"channel_id": "20", "category_id": "99", "kind": "text", "status": "changed", "before": "e", "after": "f"},
    ]
    captured: list[str] = []

    monkeypatch.setattr(plan_service, "live_records", lambda guild: [])
    monkeypatch.setattr(plan_service.majority, "ensure_separator_spec", lambda *args, **kwargs: "pipe_spaced")
    monkeypatch.setattr(plan_service.majority, "build_category_aware_options", lambda studio, options, records: (dict(options), {"ok": True}))
    monkeypatch.setattr(plan_service.majority, "annotate_category_aware_plan_items", lambda studio, items, options: list(items))

    async def fake_build(guild: Any, options: Any) -> list[dict[str, Any]]:
        return [dict(item) for item in all_items]

    def fake_confidence(items: list[dict[str, Any]], *, context: str) -> dict[str, Any]:
        assert context == "smart_category_auto_detect"
        captured[:] = [str(item["channel_id"]) for item in items]
        return {"apply_allowed": True, "blocked_lines": [], "review_lines": []}

    monkeypatch.setattr(legacy, "build_design_plan", fake_build)
    monkeypatch.setattr(plan_service.repair_confidence, "evaluate_repair_plan", fake_confidence)

    items, options, analysis = run(
        plan_service.build_scoped_repair_plan(
            object(),
            {"theme_id": "custom", "strength": 4},
            category_id=category_id,
            channel_id=channel_id,
        )
    )

    assert [str(item["channel_id"]) for item in items] == expected_ids
    assert captured == expected_ids
    assert options["__scoped_editor_repair"] is True
    assert analysis["mode"] == "category_aware_scoped"


def test_public_apply_persists_separator_and_reset_ui_is_unambiguous() -> None:
    assert "async def _persist_separator_settings" in V2_SOURCE
    assert "rule_service.persist_separator_choice" in V2_SOURCE
    assert 'if mode == "style_change_separator"' in V2_SOURCE
    assert "Channel Separator Applied & Saved" in V2_SOURCE

    assert 'label="Reset This Category"' in LEGACY_SOURCE
    assert 'label="Reset This Channel"' in LEGACY_SOURCE
    assert 'label="Reset All Design Overrides"' in LEGACY_SOURCE
    assert "One Saved Rule Removed" in LEGACY_SOURCE
    assert "Removed only the listed rule" in LEGACY_SOURCE


def test_no_one_shot_patch_files_are_part_of_product() -> None:
    assert not (ROOT / "tools/_apply_ds_design_033_patch.py").exists()
    assert not (ROOT / ".github/workflows/_ds-design-033-apply.yml").exists()
