from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from stoney_verify.community_lfg_discovery import filter_discovery_games
from stoney_verify.commands_ext import public_community_hub as ui


ROWS = [
    {"id": "1", "game_name": "Minecraft", "play_style": "casual", "mic_preference": "optional"},
    {"id": "2", "game_name": "Minecraft Bedrock", "play_style": "competitive", "mic_preference": "required"},
    {"id": "3", "game_name": "Minecraft Java", "play_style": "casual", "mic_preference": "required"},
    {"id": "4", "game_name": "Minecraft", "play_style": "beginner", "mic_preference": "no_mic"},
    {"id": "5", "game_name": "Minecraft", "play_style": "any", "mic_preference": "preferred"},
    {"id": "6", "game_name": "Minecraft"},  # Legacy unknown fields must not pass active filters.
]


def test_game_and_preference_filters_combine_without_expanding_source() -> None:
    result = filter_discovery_games(
        ROWS, "mc", play_style="casual", mic_preference="required"
    )
    assert [row["id"] for row in result] == ["3"]
    assert [row["id"] for row in filter_discovery_games(ROWS, "", play_style="casual")] == ["1", "3"]
    assert [row["id"] for row in filter_discovery_games(ROWS, "minecraft", mic_preference="no_mic")] == ["4"]
    assert [row["id"] for row in filter_discovery_games(ROWS, "", play_style="any")] == ["5"]
    assert [row["id"] for row in filter_discovery_games(ROWS, "")] == ["1", "2", "3", "4", "5", "6"]


def test_invalid_or_external_metadata_filters_fail_closed() -> None:
    for kwarg in ({"play_style": "private"}, {"mic_preference": "admin"}, {"play_style": "@everyone"}):
        with pytest.raises(ValueError):
            filter_discovery_games(ROWS, "", **kwarg)
    with pytest.raises(ValueError, match="External provider"):
        filter_discovery_games([{"game": "Minecraft", "play_style": "casual"}], "", external=True, play_style="casual")
    assert filter_discovery_games([{"game": "Minecraft"}], "mc", external=True)[0]["game"] == "Minecraft"


def test_missing_preference_fields_are_excluded_not_assumed() -> None:
    legacy = [{"id": "legacy", "game_name": "Destiny 2"}]
    assert filter_discovery_games(legacy, "Destiny", mic_preference="optional") == []
    assert filter_discovery_games(legacy, "Destiny", play_style="casual") == []


def test_mobile_ui_adds_one_select_without_exceeding_discord_rows() -> None:
    for partner in (False, True):
        view = ui.FindPlayersView(
            44, ROWS, partner=partner, game_query="MC",
            play_style="casual", mic_preference="required",
        )
        assert view.play_style == "casual"
        assert view.mic_preference == "required"
        assert not view.clear_game.disabled
        preference = next(
            item for item in view.children
            if getattr(item, "custom_id", "") == "dank:hub:find:preferences:v1"
        )
        assert preference.row == 4
        assert len(preference.options) <= 25
        rows: dict[int, int] = {}
        for item in view.children:
            slot = int(getattr(item, "row", 0) or 0)
            rows[slot] = rows.get(slot, 0) + 1
        assert all(count <= 5 for count in rows.values())
        assert len(rows) <= 5
    unfiltered = ui.FindPlayersView(44, [], partner=True)
    assert unfiltered.clear_game.disabled


def test_preference_picker_updates_only_selected_dimension_and_keeps_game(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lookup = AsyncMock()
    defer = AsyncMock()
    monkeypatch.setattr(ui, "_open_find_players_source", lookup)
    monkeypatch.setattr(ui, "_defer_update", defer)
    interaction = SimpleNamespace(guild_id=42)

    async def select(value: str) -> None:
        selector = ui.SessionPreferenceSelect(
            44, partner=True, game_query="Destiny",
            play_style="casual", mic_preference="required",
        )
        selector._values = [value]
        await selector.callback(interaction)

    asyncio.run(select("style:competitive"))
    assert lookup.await_args.kwargs == {
        "partner": True, "game_query": "Destiny",
        "play_style": "competitive", "mic_preference": "required",
    }
    asyncio.run(select("mic:off"))
    assert lookup.await_args.kwargs["play_style"] == "casual"
    assert lookup.await_args.kwargs["mic_preference"] == ""
    asyncio.run(select("clear"))
    assert lookup.await_args.kwargs["play_style"] == ""
    assert lookup.await_args.kwargs["mic_preference"] == ""
    assert lookup.await_count == 3


def test_game_search_modal_preserves_existing_session_preferences(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lookup = AsyncMock()
    defer = AsyncMock()
    monkeypatch.setattr(ui, "_open_find_players_source", lookup)
    monkeypatch.setattr(ui, "_defer_ephemeral", defer)
    modal = ui.DiscoveryGameSearchModal(
        44, source="partner", play_style="beginner", mic_preference="no_mic",
    )
    modal.game._value = "Minecraft"
    asyncio.run(modal.on_submit(SimpleNamespace(user=SimpleNamespace(id=44))))
    assert lookup.await_args.kwargs == {
        "partner": True, "game_query": "Minecraft",
        "play_style": "beginner", "mic_preference": "no_mic",
    }


def test_source_refresh_uses_authorized_hublink_rows_before_preference_filter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    approved = AsyncMock(return_value=[dict(row) for row in ROWS])
    settings = AsyncMock()
    edited = AsyncMock()
    activity = AsyncMock(return_value=[])
    monkeypatch.setattr(ui.hub, "list_partner_discovery_sessions", approved)
    monkeypatch.setattr(ui, "_edit_private_original", edited)
    monkeypatch.setattr(ui, "ensure_community_hub_runtime", lambda client: SimpleNamespace(partner_activity_snapshots=activity))
    interaction = SimpleNamespace(guild_id=55, client=object())
    asyncio.run(ui._open_find_players_source(
        interaction, 44, partner=True, game_query="MC",
        play_style="casual", mic_preference="required",
    ))
    approved.assert_awaited_once_with(55, limit=100)
    assert edited.await_args.kwargs["view"].sessions[0]["id"] == "3"
    assert len(edited.await_args.kwargs["view"].sessions) == 1
    assert edited.await_args.kwargs["view"].partner
