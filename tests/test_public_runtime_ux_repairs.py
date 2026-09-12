from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import discord
import pytest

from stoney_verify.commands_ext import public_design_studio as legacy
from stoney_verify.commands_ext import public_design_studio_v2 as design_v2
from stoney_verify.commands_ext import public_runtime_ux_repairs as repairs


def _button_labels(view: discord.ui.View) -> set[str]:
    return {
        str(getattr(child, "label", "") or "")
        for child in view.children
        if isinstance(child, discord.ui.Button)
    }


def _custom_ids(view: discord.ui.View) -> set[str]:
    return {
        str(getattr(child, "custom_id", "") or "")
        for child in view.children
        if getattr(child, "custom_id", None)
    }


def test_advanced_setup_removes_redundant_manage_features_route() -> None:
    view = repairs.CompactAdvancedWithoutDuplicateFeatures()
    assert _button_labels(view) == {
        "Repair / Restart",
        "Help",
        "Setup Home",
        "Close",
    }
    assert "Manage Features" not in _button_labels(view)
    assert "dank_setup_advanced:features" not in _custom_ids(view)


def test_channel_item_exposes_icon_and_protection_controls() -> None:
    view = repairs.ContextAwareChannelEditorActionView(123, category_id=456)
    labels = _button_labels(view)
    assert "Change Icon / Emoji" in labels
    assert "Protection / Skip Rule" in labels
    assert "Protection Mode" not in labels
    assert "dank_design:channel_change_icon" in _custom_ids(view)


def test_reviewed_preview_exposes_protected_item_review() -> None:
    view = repairs.ReviewedPreviewWithProtection(
        can_apply=True,
        pending_created_at=1.0,
    )
    assert "Review Protected Items" in _button_labels(view)
    assert "dank_design_v2:preview_protection" in _custom_ids(view)


def test_safe_skip_detection_uses_real_protected_plan_status() -> None:
    items = [
        {
            "status": "protected",
            "channel_id": "10",
            "before": "mod-log",
            "warnings": ["Safe skip — protected ticket/log/system item."],
        },
        {"status": "changed", "channel_id": "11", "before": "general"},
    ]
    protected = repairs._protected_items(items)
    assert len(protected) == 1
    assert protected[0]["channel_id"] == "10"


def test_editor_context_preserves_global_page_instead_of_falling_to_page_zero() -> None:
    interaction = SimpleNamespace(
        guild=SimpleNamespace(id=101),
        user=SimpleNamespace(id=202),
    )
    repairs._EDITOR_CONTEXT.clear()
    repairs._remember_editor_context(
        interaction,
        channel_id=303,
        page=5,
        category_filter_id=None,
    )
    assert repairs._editor_context(
        interaction,
        channel_id=303,
        fallback_category_id=404,
    ) == (5, None)


def test_editor_context_preserves_category_scoped_page() -> None:
    interaction = SimpleNamespace(
        guild=SimpleNamespace(id=111),
        user=SimpleNamespace(id=222),
    )
    repairs._EDITOR_CONTEXT.clear()
    repairs._remember_editor_context(
        interaction,
        channel_id=333,
        page=2,
        category_filter_id=444,
    )
    assert repairs._editor_context(
        interaction,
        channel_id=333,
        fallback_category_id=None,
    ) == (2, 444)


def test_channel_page_jump_exposes_every_current_page(monkeypatch: pytest.MonkeyPatch) -> None:
    groups = [
        {
            "category": None,
            "category_id": None,
            "channels": [],
            "part": 1,
            "parts": 1,
            "label": f"Category {index + 1}",
        }
        for index in range(7)
    ]
    monkeypatch.setattr(legacy, "_channel_editor_groups", lambda _guild: groups)
    select = repairs.ChannelPageJumpSelect(
        SimpleNamespace(),
        page=4,
        category_id=None,
    )
    assert [option.value for option in select.options] == [str(index) for index in range(7)]
    defaults = [option.value for option in select.options if option.default]
    assert defaults == ["4"]
    assert "5/7" in str(select.placeholder)


def test_allow_listed_protected_items_saves_exact_full_overrides(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    saved: dict[str, Any] = {}
    edited: dict[str, Any] = {}

    async def allow(_interaction: Any) -> bool:
        return True

    async def load(_guild_id: int) -> dict[str, Any]:
        return {
            "theme_id": "gothic_clean",
            "protection_item_rules": {"99": "never"},
        }

    async def save(_interaction: Any, options: dict[str, Any]) -> None:
        saved.update(options)

    class Response:
        async def edit_message(self, **kwargs: Any) -> None:
            edited.update(kwargs)

    interaction = SimpleNamespace(
        guild=SimpleNamespace(id=9001),
        user=SimpleNamespace(id=77),
        response=Response(),
    )

    monkeypatch.setattr(legacy, "_require_design_permission", allow)
    monkeypatch.setattr(legacy, "_load_design_options", load)
    monkeypatch.setattr(legacy, "_save_options", save)

    view = repairs.ProtectedItemsView(
        [
            {"status": "protected", "channel_id": "10", "before": "mod-log"},
            {"status": "protected", "channel_id": "20", "before": "tickets"},
        ]
    )
    asyncio.run(view._allow_all(interaction))

    rules = saved["protection_item_rules"]
    assert rules["10"] == "full"
    assert rules["20"] == "full"
    assert rules["99"] == "never"
    assert edited["embed"].title == "🔓 Protected Items Can Now Be Styled"
    assert isinstance(edited["view"], design_v2.DesignHomeView)


def test_runtime_patch_reserves_rows_for_page_jump_and_navigation() -> None:
    assert repairs.apply_runtime_ux_repairs.__name__ == "apply_runtime_ux_repairs"
    source_globals = repairs.apply_runtime_ux_repairs.__code__.co_names
    assert "EDITOR_PAGE_SIZE" in source_globals
    assert "ChannelEditorPickerView" in source_globals
    assert "ChannelEditorActionView" in source_globals
