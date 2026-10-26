"""Find Players opens one outside-first discovery panel, not a local dead end."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord

from stoney_verify.commands_ext import public_community_hub as ui


def _atlas():
    return [{
        "game": "Minecraft", "title": "NA survival friends",
        "type": "LFM", "region": "NA", "mode": "survival",
        "ready_now": False, "source": "Atlas", "url": "https://lfgatlas.pages.dev/",
    }]


def _network():
    return [{
        "game_name": "Minecraft", "capacity": 6,
        "invite_url": "https://discord.gg/goodcode",
        "community_name": "The 420 Lobby",
        "notes": "PRIVATE_NOTES",
        "host_id": "PRIVATE_HOST_ID",
    }]


def _local():
    return [{
        "id": "private-id", "game_name": "Minecraft",
        "notes": "PRIVATE_SESSION_DESCRIPTION", "host_id": "PRIVATE_HOST_ID",
    }]


def _interaction():
    return SimpleNamespace(guild_id=111111111111111111)


def _mock(monkeypatch, *, atlas_rows=None, network_rows=None, local_rows=None):
    fetch_atlas = AsyncMock(return_value=_atlas() if atlas_rows is None else atlas_rows)
    fetch_network = AsyncMock(return_value=_network() if network_rows is None else network_rows)
    fetch_local = AsyncMock(return_value=_local() if local_rows is None else local_rows)
    edit = AsyncMock()
    monkeypatch.setattr(ui.atlas, "list_atlas_posts", fetch_atlas)
    monkeypatch.setattr(ui.hub, "list_public_lfg_directory", fetch_network)
    monkeypatch.setattr(ui.hub, "list_active_sessions", fetch_local)
    monkeypatch.setattr(ui, "_edit_private_original", edit)
    return fetch_atlas, fetch_network, fetch_local, edit


def test_first_click_queries_outside_network_and_local_together(monkeypatch):
    outside, network, local, edit = _mock(monkeypatch)
    asyncio.run(ui._open_unified_player_discovery(_interaction(), 99))
    outside.assert_awaited_once_with(game="", region="", limit=25)
    network.assert_awaited_once_with(limit=100)
    local.assert_awaited_once_with(111111111111111111, limit=30)
    embed = edit.await_args.kwargs["embed"]
    title = embed.title
    assert title == "🎮 Find Players & Gaming Servers"
    fields = embed.fields
    assert "Outside gaming groups" in fields[0].name
    assert "Public Gaming Network" in fields[1].name
    assert "The 420 Lobby" in fields[1].value
    assert "Groups in this server" in fields[2].name
    view = edit.await_args.kwargs["view"]
    assert isinstance(view, ui.UnifiedPlayerDiscoveryView)
    assert len(view.children) <= 25
    assert all((getattr(x, "row", 0) or 0) <= 4 for x in view.children)
    assert len([x for x in view.children if isinstance(x, discord.ui.Select)]) == 1
    assert "PRIVATE_NOTES" not in repr(embed.to_dict())
    assert "PRIVATE_HOST_ID" not in repr(embed.to_dict())
    assert "PRIVATE_SESSION_DESCRIPTION" not in repr(embed.to_dict())


def test_unmigrated_network_does_not_hide_outside_matches(monkeypatch):
    outside, network, local, edit = _mock(monkeypatch)
    network.side_effect = ui.hub.CommunityHubError("RPC missing")
    asyncio.run(ui._open_unified_player_discovery(_interaction(), 44))
    embed = edit.await_args.kwargs["embed"]
    rendered = repr(embed.to_dict())
    assert "NA survival friends" in rendered
    assert "Cross-server listings aren't set up" in rendered
    assert "unavailable or not migrated yet" not in rendered
    menu = next(x for x in edit.await_args.kwargs["view"].children
                if isinstance(x, discord.ui.Select))
    assert menu.disabled


def test_atlas_outage_does_not_hide_approved_network(monkeypatch):
    outside, network, local, edit = _mock(monkeypatch)
    outside.side_effect = RuntimeError("Provider failed")
    asyncio.run(ui._open_unified_player_discovery(_interaction(), 44))
    rendered = repr(edit.await_args.kwargs["embed"].to_dict())
    assert "Outside group listings are temporarily unavailable" in rendered
    assert "The 420 Lobby" in rendered
    menu = next(x for x in edit.await_args.kwargs["view"].children
                if isinstance(x, discord.ui.Select))
    assert not menu.disabled


def test_empty_local_only_does_not_claim_outside_empty(monkeypatch):
    outside, network, local, edit = _mock(monkeypatch, local_rows=[])
    asyncio.run(ui._open_unified_player_discovery(_interaction(), 44))
    rendered = repr(edit.await_args.kwargs["embed"].to_dict())
    assert "No matching sessions hosted here" in rendered
    assert "NA survival friends" in rendered
    assert "The 420 Lobby" in rendered


def test_one_game_search_is_passed_to_every_source(monkeypatch):
    outside, network, local, edit = _mock(monkeypatch)
    asyncio.run(ui._open_unified_player_discovery(
        _interaction(), 44, game="Minecraft", region="NA",
    ))
    outside.assert_awaited_once_with(game="Minecraft", region="NA", limit=25)
    rendered = repr(edit.await_args.kwargs["embed"].to_dict())
    assert "Outside region: NA" in rendered
    assert "The 420 Lobby" in rendered
    view = edit.await_args.kwargs["view"]
    assert view.game == "Minecraft" and view.region == "NA"
    # Private menus retain source-specific join authority, not native joins
    # created for arbitrary external posts.
    assert "Quick Match" not in [getattr(c, "label", "") for c in view.children]


def test_no_untrusted_external_url_in_unified_embed():
    row = dict(_atlas()[0], url="https://evil.example/steal")
    embed = ui._unified_discovery_embed([row], [], [])
    content = repr(embed.to_dict())
    assert "evil.example" not in content
    assert "https://lfgatlas.pages.dev/" in content


def test_public_network_selection_from_unified_rechecks_approval(monkeypatch):
    groups = [{
        "game": "Minecraft", "community": "The 420 Lobby",
        "capacity": 6, "join_url": "https://discord.gg/goodcode",
    }]
    menu = ui.PublicNetworkSelect(99, groups, "Minecraft",
                                  return_to_unified=True, region="NA")
    menu._values = ["0"]
    monkeypatch.setattr(ui, "_defer_update", AsyncMock())
    monkeypatch.setattr(ui.hub, "list_public_lfg_directory",
                        AsyncMock(return_value=[]))
    notice = AsyncMock()
    edit = AsyncMock()
    monkeypatch.setattr(ui, "_followup", notice)
    monkeypatch.setattr(ui, "_edit_private_original", edit)
    asyncio.run(menu.callback(SimpleNamespace()))
    notice.assert_awaited_once()
    edit.assert_not_awaited()


def test_existing_detail_navigation_can_return_to_unified(monkeypatch):
    row = {
        "game": "Minecraft", "community": "The 420 Lobby",
        "capacity": 6, "join_url": "https://discord.gg/goodcode",
    }
    detail = ui.PublicNetworkDetailView(
        99, row, game_query="Minecraft", return_to_unified=True, region="NA",
    )
    back = next(x for x in detail.children
                if getattr(x, "custom_id", "").endswith("detailback:v1"))
    defer = AsyncMock()
    reopen = AsyncMock()
    monkeypatch.setattr(ui, "_defer_update", defer)
    monkeypatch.setattr(ui, "_open_unified_player_discovery", reopen)
    interaction = SimpleNamespace()
    asyncio.run(detail.back(interaction, back))
    reopen.assert_awaited_once_with(
        interaction, 99, game="Minecraft", region="NA",
    )
