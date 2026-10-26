"""Mobile paging of existing authorized Community Hub session discovery."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from stoney_verify.community_lfg_discovery import filter_discovery_games
from stoney_verify.commands_ext import public_community_hub as ui


def _sessions(count: int = 60):
    return [
        {
            "id": str(i + 1),
            "guild_id": "123",
            "game_name": "Minecraft",
            "play_style": "casual" if i % 2 == 0 else "competitive",
            "mic_preference": "required",
            "privacy": "public",
            "state": "open",
            "capacity": 6,
        }
        for i in range(count)
    ]


def test_paging_scans_only_authorized_100_row_snapshot_and_preserves_filters():
    rows = _sessions(140)
    first = filter_discovery_games(rows, "mc", limit=25, play_style="casual")
    second = filter_discovery_games(rows, "mc", limit=25, play_style="casual", offset=25)
    assert [x["id"] for x in first] == [str(i + 1) for i in range(0, 50, 2)]
    assert [x["id"] for x in second] == [str(i + 1) for i in range(50, 100, 2)]
    assert filter_discovery_games(rows, "mc", limit=25, play_style="casual", offset=50) == []
    assert filter_discovery_games(rows, "Minecraft", offset=101) == []
    assert len(filter_discovery_games(rows, "mc", limit=999)) == 25


def test_mobile_paging_reuses_existing_select_row_without_extra_commands():
    page1 = ui.FindPlayersView(
        44, _sessions(25), partner=True, page=1, has_next=True,
        game_query="mc", play_style="casual", mic_preference="required",
    )
    select = next(item for item in page1.children if getattr(item, "custom_id", "") == "dank:hub:find:preferences:v1")
    assert select.row == 4
    assert select.page == 1
    assert {x.value for x in select.options} >= {"page:prev", "page:next", "style:casual", "mic:required"}
    assert len(select.options) <= 25
    rows: dict[int, int] = {}
    for child in page1.children:
        row = int(getattr(child, "row", 0) or 0)
        rows[row] = rows.get(row, 0) + 1
    assert all(n <= 5 for n in rows.values())
    assert len(rows) <= 5


def test_page_choice_preserves_source_and_filters(monkeypatch):
    open_source = AsyncMock()
    monkeypatch.setattr(ui, "_open_find_players_source", open_source)
    monkeypatch.setattr(ui, "_defer_update", AsyncMock())
    selection = ui.SessionPreferenceSelect(
        44, partner=True, game_query="Minecraft",
        play_style="casual", mic_preference="required",
        page=1, has_next=True,
    )
    selection._values = ["page:next"]
    asyncio.run(selection.callback(SimpleNamespace(guild_id=123)))
    assert open_source.await_args.kwargs == {
        "partner": True, "game_query": "Minecraft",
        "play_style": "casual", "mic_preference": "required", "page": 2,
    }


def test_page_lookup_refetches_authorized_local_rows(monkeypatch):
    list_local = AsyncMock(return_value=_sessions(60))
    summary = AsyncMock(return_value={})
    edited = AsyncMock()
    monkeypatch.setattr(ui.hub, "list_active_sessions", list_local)
    monkeypatch.setattr(ui.hub, "availability_summary", summary)
    monkeypatch.setattr(ui, "_edit_private_original", edited)
    interaction = SimpleNamespace(guild_id=123, client=object())
    asyncio.run(ui._open_find_players_source(interaction, 44, partner=False, page=1))
    list_local.assert_awaited_once_with(123, limit=100)
    result_view = edited.await_args.kwargs["view"]
    assert result_view.page == 1
    assert result_view.has_next is True
    assert len(result_view.sessions) == 25
    assert [x["id"] for x in result_view.sessions] == [str(i) for i in range(26, 51)]
    assert "Page 2" in edited.await_args.kwargs["embed"].description


def test_revoked_partner_listings_reset_stale_page(monkeypatch):
    partner_list = AsyncMock(return_value=_sessions(7))
    edited = AsyncMock()
    monkeypatch.setattr(ui.hub, "list_partner_discovery_sessions", partner_list)
    monkeypatch.setattr(ui, "ensure_community_hub_runtime", lambda client: SimpleNamespace(partner_activity_snapshots=AsyncMock(return_value=[])))
    monkeypatch.setattr(ui, "_edit_private_original", edited)
    interaction = SimpleNamespace(guild_id=123, client=object())
    asyncio.run(ui._open_find_players_source(
        interaction, 44, partner=True, page=2, game_query="mc",
    ))
    partner_list.assert_awaited_once_with(123, limit=100)
    view = edited.await_args.kwargs["view"]
    assert view.partner and view.page == 0 and not view.has_next
    assert len(view.sessions) == 7


def test_source_view_does_not_enable_pagination_without_more_results():
    first = ui.FindPlayersView(44, _sessions(5), partner=False)
    menu = next(item for item in first.children if getattr(item, "custom_id", "") == "dank:hub:find:preferences:v1")
    assert not any(option.value.startswith("page:") for option in menu.options)
