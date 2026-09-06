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


def run(coroutine: Any) -> Any:
    return asyncio.run(coroutine)


def test_separator_persistence_updates_authoritative_setting_without_rewriting_other_style_fields() -> None:
    options = {
        "theme_id": "gothic_clean",
        "strength": 3,
        "separator_id": "bar_full",
        "format_lock_global": {
            "enabled": True,
            "theme_id": "gothic_clean",
            "strength": 3,
            "font": "fraktur",
            "separator_id": "bar_full",
            "category_frame_id": "line",
            "icon_mode": "keep_existing",
            "exact_match": True,
        },
        "category_format_locks": {"10": {"separator_id": "bar_thin", "strength": 2}},
        "channel_format_locks": {"20": {"separator_id": "bar_block", "strength": 4}},
    }
    updated = rules.persist_separator_authority(options, "bar_heavy")
    assert updated["separator_id"] == "bar_heavy"
    assert updated["format_lock_global"]["separator_id"] == "bar_heavy"
    assert updated["format_lock_global"]["font"] == "fraktur"
    assert updated["format_lock_global"]["category_frame_id"] == "line"
    assert updated["category_format_locks"] == options["category_format_locks"]
    assert updated["channel_format_locks"] == options["channel_format_locks"]


def test_current_format_lock_prefers_explicit_saved_separator_over_theme_default() -> None:
    lock = legacy._current_format_lock({"theme_id": "gothic_clean", "strength": 4, "separator_id": "bar_heavy"})
    assert lock["separator_id"] == "bar_heavy"


def test_reset_item_removes_all_same_item_override_layers() -> None:
    options = {
        "category_format_locks": {"10": {"separator_id": "bar_full"}},
        "channel_format_locks": {"10": {"separator_id": "bar_heavy"}, "11": {"separator_id": "bar_thin"}},
        "manual_name_overrides": {"10": "staff", "11": "rules"},
        "protection_item_rules": {"10": "never", "11": "full"},
        "protection_rules": {"staff": "never", "rules": "emoji_only", "general": "full"},
    }
    updated, removed = rules.reset_item_overrides(options, target_id=10, current_name="staff", include_category=True)
    assert removed == 4
    assert "10" not in updated["category_format_locks"]
    assert "10" not in updated["channel_format_locks"]
    assert "10" not in updated["manual_name_overrides"]
    assert "10" not in updated["protection_item_rules"]
    assert "staff" not in updated["protection_rules"]
    assert updated["channel_format_locks"]["11"]["separator_id"] == "bar_thin"
    assert updated["protection_rules"]["general"] == "full"


def test_reset_all_design_overrides_clears_every_advertised_override_layer_but_preserves_server_draft() -> None:
    options = {
        "theme_id": "gothic_clean",
        "strength": 3,
        "separator_id": "bar_heavy",
        "format_lock_global": {"enabled": True, "separator_id": "bar_full"},
        "category_format_locks": {"10": {"separator_id": "bar_full"}},
        "channel_format_locks": {"11": {"separator_id": "bar_thin"}},
        "manual_name_overrides": {"12": "staff"},
        "protection_item_rules": {"13": "never"},
        "protection_rules": {"staff": "never"},
    }
    updated, removed = rules.reset_all_overrides(options)
    assert removed == 5
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
    start = LEGACY_SOURCE.index("async def _preview_scope(")
    source = LEGACY_SOURCE[start:LEGACY_SOURCE.index("def _category_editor_embed", start)]
    assert "plan_service.build_scoped_repair_plan" in source
    assert 'repair_options["__use_live_majority_layout"]' not in source
    assert 'mode in {"category_editor", "channel_editor"}' in source
    assert "class DesignCategoryEditorButton" not in LEGACY_SOURCE
    assert "class DesignChannelEditorButton" not in LEGACY_SOURCE


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

    async def fake_build(guild: Any, options: Any) -> list[dict[str, Any]]:
        return [dict(item) for item in all_items]

    monkeypatch.setattr(plan_service.legacy, "build_design_plan", fake_build)

    def fake_confidence(items: list[dict[str, Any]], *, context: str) -> dict[str, Any]:
        captured.extend(str(item.get("channel_id")) for item in items)
        return {"apply_allowed": True, "context": context, "blocked_lines": [], "review_lines": []}

    monkeypatch.setattr(plan_service.repair_confidence, "evaluate_repair_plan", fake_confidence)

    if category_id is not None:
        _items, _options, _analysis = run(plan_service.build_scoped_repair_plan(SimpleNamespace(id=1), {}, category_id=category_id))
    else:
        _items, _options, _analysis = run(plan_service.build_scoped_repair_plan(SimpleNamespace(id=1), {}, channel_id=channel_id))

    assert captured == expected_ids


def test_category_header_uses_saved_design_while_child_channels_use_local_auto_detect(monkeypatch: pytest.MonkeyPatch) -> None:
    records = [
        {"id": "10", "name": "staff", "kind": "category", "category_id": ""},
        {"id": "11", "name": "🔒│mods", "kind": "text", "category_id": "10"},
    ]
    items = [
        {"channel_id": "10", "category_id": "", "kind": "category", "status": "changed", "before": "staff", "after": "── 𝔰𝔱𝔞𝔣𝔣 ──", "warnings": [], "blockers": []},
        {"channel_id": "11", "category_id": "10", "kind": "text", "status": "unchanged", "before": "🔒│mods", "after": "🔒│mods", "warnings": [], "blockers": []},
    ]

    monkeypatch.setattr(plan_service, "live_records", lambda guild: [dict(row) for row in records])
    monkeypatch.setattr(plan_service.majority, "build_category_aware_options", lambda _studio, options, _records: ({**dict(options), "__auto_detect_preserve_ids": ["10"]}, {"summaries": {}}))
    monkeypatch.setattr(plan_service.majority, "annotate_category_aware_plan_items", lambda _studio, rows, _options: rows)

    async def fake_build(guild: Any, options: Any) -> list[dict[str, Any]]:
        return [dict(item) for item in items]

    monkeypatch.setattr(plan_service.legacy, "build_design_plan", fake_build)
    monkeypatch.setattr(plan_service.repair_confidence, "evaluate_repair_plan", lambda _items, context: {"apply_allowed": True, "context": context, "blocked_lines": [], "review_lines": []})

    scoped, _options, _analysis = run(plan_service.build_scoped_repair_plan(SimpleNamespace(id=1), {"theme_id": "gothic_clean", "strength": 4}, category_id=10))
    assert [row["channel_id"] for row in scoped] == ["10", "11"]
    assert scoped[0]["status"] == "changed"
