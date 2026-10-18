from __future__ import annotations

import asyncio
import inspect
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from stoney_verify.community_lfg_discovery import filter_discovery_games, game_search_key
from stoney_verify.commands_ext import public_community_hub as ui


def test_game_key_is_case_insensitive_and_normalizes_selected_aliases() -> None:
    assert game_search_key("  MINECRAFT–BEDROCK ") == "minecraft"
    assert game_search_key("MC") == "minecraft"
    assert game_search_key("GTA 5") == "grand theft auto v"
    assert game_search_key("Grand Theft Auto V") == "grand theft auto v"
    assert game_search_key("HALO: INFINITE") == "halo infinite"


def test_filter_keeps_only_authorized_source_rows_and_preserves_order() -> None:
    local = [
        {"id": "1", "game_name": "Minecraft Java", "private_note": "not searched"},
        {"id": "2", "game_name": "Destiny 2"},
        {"id": "3", "game_name": "Minecraft Bedrock"},
    ]
    result = filter_discovery_games(local, "mc")
    assert [row["id"] for row in result] == ["1", "3"]
    assert filter_discovery_games(local, "not searched") == []
    assert filter_discovery_games(local, "all") == []
    assert [row["id"] for row in filter_discovery_games(local, "")] == ["1", "2", "3"]


def test_external_filter_ignores_untrusted_titles_notes_and_provider_links() -> None:
    rows = [
        {"game": "Warframe", "title": "Destiny 2", "url": "https://www.the100.io/gaming_sessions/1"},
        {"game": "Destiny 2", "title": "private Minecraft group", "url": "https://www.the100.io/gaming_sessions/2"},
    ]
    assert [x["game"] for x in filter_discovery_games(rows, "Destiny", external=True)] == ["Destiny 2"]
    assert not filter_discovery_games(rows, "Minecraft", external=True)


def test_filter_caps_results_and_skips_invalid_rows() -> None:
    rows = [{"id": str(i), "game_name": "Minecraft"} for i in range(1, 140)]
    assert len(filter_discovery_games(rows, "minecraft", limit=999)) == 25
    assert len(filter_discovery_games(rows, "", limit=999)) == 25
    assert len(filter_discovery_games([None, "bad", {"id": "3", "game_name": "MC"}], "mc")) == 1


def test_native_source_navigation_and_external_browser_fit_discord_components() -> None:
    local = ui.FindPlayersView(54, [], partner=False)
    partner = ui.FindPlayersView(54, [], partner=True, game_query="Minecraft")
    external = ui.ExternalGroupsView(54, [], game_query="Minecraft")
    assert local.local_source.disabled
    assert not local.partner_source.disabled
    assert not partner.local_source.disabled
    assert partner.partner_source.disabled
    assert not partner.clear_game.disabled
    assert not external.clear_game.disabled
    for view in (local, partner, external):
        by_row: dict[int, int] = {}
        for child in view.children:
            row = int(getattr(child, "row", 0) or 0)
            by_row[row] = by_row.get(row, 0) + 1
            assert len(str(getattr(child, "custom_id", ""))) <= 100
        assert all(count <= 5 for count in by_row.values())


def test_game_search_modal_routes_to_correct_source_and_rejects_other_user(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    defer = AsyncMock()
    local = AsyncMock()
    external = AsyncMock()
    private = AsyncMock()
    monkeypatch.setattr(ui, "_defer_ephemeral", defer)
    monkeypatch.setattr(ui, "_open_find_players_source", local)
    monkeypatch.setattr(ui, "_open_external_sessions", external)
    monkeypatch.setattr(ui, "_private", private)
    interaction = SimpleNamespace(user=SimpleNamespace(id=54))
    async def submit(source: str) -> None:
        modal = ui.DiscoveryGameSearchModal(54, source=source, current_query="Minecraft")
        modal.game._value = "GTA V"
        await modal.on_submit(interaction)

    asyncio.run(submit("local"))
    assert local.await_args.kwargs == {"partner": False, "game_query": "GTA V"}
    local.reset_mock()
    asyncio.run(submit("partner"))
    assert local.await_args.kwargs == {"partner": True, "game_query": "GTA V"}
    asyncio.run(submit("external"))
    assert external.await_args.kwargs == {"game_query": "GTA V"}
    assert local.await_count == 1
    assert external.await_count == 1

    other = SimpleNamespace(user=SimpleNamespace(id=999))
    modal = ui.DiscoveryGameSearchModal(54, source="external")
    asyncio.run(modal.on_submit(other))
    private.assert_awaited_once()
    assert external.await_count == 1


def test_revoked_hublink_listing_is_checked_again_at_select_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    select = ui.SessionSelect(54, [{"id": "123", "guild_id": 555, "game_name": "Minecraft"}], partner=True)
    select._values = ["123"]
    defer = AsyncMock()
    edited = AsyncMock()
    get_approved = AsyncMock(return_value=[])
    monkeypatch.setattr(ui, "_defer_update", defer)
    monkeypatch.setattr(ui, "_edit_private_original", edited)
    monkeypatch.setattr(ui.hub, "list_partner_discovery_sessions", get_approved)
    interaction = SimpleNamespace(guild_id=111)
    asyncio.run(select.callback(interaction))
    get_approved.assert_awaited_once_with(111, limit=100)
    assert "no longer publicly available" in edited.await_args.kwargs["content"]


def test_external_search_rechecks_per_guild_consent_on_every_lookup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = AsyncMock(return_value={"enabled": True, "external_the100_enabled": False})
    fetch = AsyncMock()
    edited = AsyncMock()
    monkeypatch.setattr(ui.hub, "get_settings", settings)
    monkeypatch.setattr(ui.external, "list_the100_sessions", fetch)
    monkeypatch.setattr(ui, "_edit_private_original", edited)
    interaction = SimpleNamespace(guild_id=321)
    asyncio.run(ui._open_external_sessions(interaction, 54, game_query="Destiny"))
    settings.assert_awaited_once_with(321)
    fetch.assert_not_awaited()
    assert "not enabled" in edited.await_args.kwargs["view"].notice.lower() or "not enabled" in edited.await_args.kwargs["embed"].fields[1].value.lower()


def test_local_and_partner_reads_stay_isolated_from_the100_native_join() -> None:
    source = inspect.getsource(ui._open_find_players_source)
    assert "hub.list_active_sessions(guild_id, limit=100)" in source
    assert "hub.list_partner_discovery_sessions(guild_id, limit=100)" in source
    assert "filter_discovery_games(sessions, game_query, limit=25)" in source
    assert "external.list_the100_sessions" not in source
    external_source = inspect.getsource(ui._open_external_sessions)
    assert "hub.get_settings(int(interaction.guild_id or 0))" in external_source
    assert "filter_discovery_games(sessions, game_query, external=True, limit=25)" in external_source
    assert "hub.quick_match" not in external_source
