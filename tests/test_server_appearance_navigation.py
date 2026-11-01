from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from stoney_verify.commands_ext import public_design_studio_v2 as design


def button(view, label: str):
    return next(item for item in view.children if getattr(item, "label", "") == label)


def interaction():
    return SimpleNamespace(
        guild=SimpleNamespace(id=731),
        user=SimpleNamespace(id=92),
        response=SimpleNamespace(
            defer=AsyncMock(),
            edit_message=AsyncMock(),
            is_done=lambda: False,
        ),
        edit_original_response=AsyncMock(),
    )


def test_advanced_rules_and_history_use_current_saved_state(monkeypatch):
    allow = AsyncMock(return_value=True)
    load = AsyncMock(return_value={"channel_format_locks": {"731": {"enabled": True}}})
    monkeypatch.setattr(design, "_require_design_permission", allow)
    monkeypatch.setattr(design, "_load_design_options", load)

    async def check():
        rules_interaction = interaction()
        await button(design.DesignAdvancedView(), "Saved Style Exceptions").callback(rules_interaction)
        rules_interaction.response.defer.assert_awaited_once()
        load.assert_awaited_with(731)
        assert isinstance(rules_interaction.edit_original_response.await_args.kwargs["view"], design.SavedRulesView)

        history_interaction = interaction()
        await button(design.DesignAdvancedView(), "History & Reset").callback(history_interaction)
        history_interaction.response.defer.assert_awaited_once()
        history_view = history_interaction.edit_original_response.await_args.kwargs["view"]
        assert isinstance(history_view, design.DesignHistoryView)
        assert not button(history_view, "Clean Redesign").disabled

        clean_interaction = interaction()
        await button(history_view, "Clean Redesign").callback(clean_interaction)
        clean_view = clean_interaction.response.edit_message.await_args.kwargs["view"]
        assert isinstance(clean_view, design.CleanRedesignConfirmView)

        empty_history = design.DesignHistoryView({})
        assert button(empty_history, "Clean Redesign").disabled

    asyncio.run(check())


def test_advanced_back_returns_to_one_authoritative_home(monkeypatch):
    allow = AsyncMock(return_value=True)
    load = AsyncMock(return_value={})
    monkeypatch.setattr(design, "_require_design_permission", allow)
    monkeypatch.setattr(design, "_load_design_options", load)

    i = interaction()
    asyncio.run(button(design.DesignAdvancedView(), "Back to Appearance").callback(i))
    assert isinstance(i.edit_original_response.await_args.kwargs["view"], design.DesignHomeView)
    load.assert_awaited_once_with(731)


def test_searchable_names_uses_shared_policy_ui(monkeypatch):
    from stoney_verify.commands_ext import public_search_safe_naming as search_safe

    open_policy = AsyncMock()
    monkeypatch.setattr(search_safe, "open_search_safe_naming", open_policy)
    i = interaction()
    asyncio.run(button(design.DesignAdvancedView(), "Searchable Names").callback(i))
    open_policy.assert_awaited_once_with(i)


def test_separator_only_preview_is_in_details_not_whole_server():
    server = design.DesignServerView({"theme_id": "gothic_clean", "strength": 4})
    detail = design.DesignServerCategoryFrameView({"theme_id": "gothic_clean", "strength": 4})
    assert "Preview Separator Only" not in [x.label for x in server.children if hasattr(x, "label")]
    assert "Preview Separator Only" in [x.label for x in detail.children if hasattr(x, "label")]
    assert "Clean Redesign" not in [x.label for x in server.children if hasattr(x, "label")]
