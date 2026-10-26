"""Cross-server public discovery is outbound-only and revocation checked."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
import pytest

from stoney_verify.commands_ext import public_community_hub as ui


def _row(name="Minecraft", link="https://discord.gg/the420lobby"):
    return {"game_name": name, "capacity": 6, "invite_url": link, "community_name": "The 420 Lobby"}


def test_network_surface_is_mobile_friendly_and_not_a_native_join():
    groups = [{"game": "Minecraft", "community": "The 420 Lobby", "capacity": 6, "join_url": "https://discord.gg/the420lobby"}]
    view = ui.PublicNetworkView(123, groups, game_query="mc")
    menu = next(x for x in view.children if isinstance(x, discord.ui.Select))
    assert len(menu.options) == 1
    assert menu.options[0].label == "Minecraft"
    assert menu.row == 0
    assert not any(getattr(x, "label", "") == "Quick Match" for x in view.children)
    assert all(getattr(x, "row", 0) <= 4 for x in view.children)
    details = ui.PublicNetworkDetailView(123, groups[0], game_query="mc")
    url_items = [x for x in details.children if isinstance(x, discord.ui.Button) and x.style == discord.ButtonStyle.link]
    assert len(url_items) == 1
    assert url_items[0].url == "https://discord.gg/the420lobby"


def test_public_network_source_reads_only_consent_projection(monkeypatch):
    fetch = AsyncMock(return_value=[_row(), _row("Halo Infinite")])
    edited = AsyncMock()
    monkeypatch.setattr(ui.hub, "list_public_lfg_directory", fetch)
    monkeypatch.setattr(ui, "_edit_private_original", edited)
    asyncio.run(ui._open_public_lfg_network(SimpleNamespace(), 123, game_query="mc"))
    fetch.assert_awaited_once_with(limit=100)
    view = edited.await_args.kwargs["view"]
    assert isinstance(view, ui.PublicNetworkView)
    assert len(view.children) <= 25
    assert all(getattr(x, 'row', 0) <= 4 for x in view.children)
    assert "Minecraft" in edited.await_args.kwargs["embed"].fields[1].name or (
        "Minecraft" in edited.await_args.kwargs["embed"].fields[0].name
    )
    assert len(next(x for x in view.children if isinstance(x, discord.ui.Select)).options) == 1


def test_public_network_denies_stale_or_revoked_invite(monkeypatch):
    groups = [{"game": "Minecraft", "community": "The 420 Lobby", "capacity": 6, "join_url": "https://discord.gg/the420lobby"}]
    menu = ui.PublicNetworkSelect(123, groups, "mc")
    menu._values = ["0"]
    monkeypatch.setattr(ui, "_defer_update", AsyncMock())
    follow = AsyncMock()
    edited = AsyncMock()
    monkeypatch.setattr(ui, "_followup", follow)
    monkeypatch.setattr(ui, "_edit_private_original", edited)
    fetch = AsyncMock(return_value=[])
    monkeypatch.setattr(ui.hub, "list_public_lfg_directory", fetch)
    asyncio.run(menu.callback(SimpleNamespace()))
    follow.assert_awaited_once()
    edited.assert_not_awaited()


def test_invalid_destination_fails_closed(monkeypatch):
    fetch = AsyncMock(return_value=[_row(link="https://discord.com/channels/1/2")])
    edited = AsyncMock()
    monkeypatch.setattr(ui.hub, "list_public_lfg_directory", fetch)
    monkeypatch.setattr(ui, "_edit_private_original", edited)
    asyncio.run(ui._open_public_lfg_network(SimpleNamespace(), 123))
    assert "No groups found" in [field.name for field in edited.await_args.kwargs["embed"].fields]


def test_group_selector_has_finite_options():
    rows = [{"game": f"Game {i}", "community": f"Community {i}", "capacity": 4, "join_url": "https://discord.gg/validcode"} for i in range(100)]
    menu = ui.PublicNetworkSelect(123, rows, "")
    assert len(menu.options) == 25
    assert len(menu.custom_id) <= 100


def test_public_network_paging_reaches_all_approved_groups(monkeypatch):
    items = [_row(name=f"Game {i}", link="https://discord.gg/validcode") for i in range(35)]
    fetch = AsyncMock(return_value=items)
    edited = AsyncMock()
    monkeypatch.setattr(ui.hub, "list_public_lfg_directory", fetch)
    monkeypatch.setattr(ui, "_edit_private_original", edited)
    asyncio.run(ui._open_public_lfg_network(SimpleNamespace(), 123, page=1))
    view = edited.await_args.kwargs["view"]
    select = next(x for x in view.children if isinstance(x, discord.ui.Select))
    assert view.page == 1 and not view.has_next
    assert len(select.options) == 15
    assert select.options[0].label == "Game 20"
    assert any(
        getattr(x, "custom_id", "") == "dank:hub:publicnetwork:prev:v1" and not x.disabled
        for x in view.children
    )
    assert any(
        getattr(x, "custom_id", "") == "dank:hub:publicnetwork:next:v1" and x.disabled
        for x in view.children
    )


def test_public_network_stale_page_resets_to_first(monkeypatch):
    fetch = AsyncMock(return_value=[_row(name="Minecraft")])
    edited = AsyncMock()
    monkeypatch.setattr(ui.hub, "list_public_lfg_directory", fetch)
    monkeypatch.setattr(ui, "_edit_private_original", edited)
    asyncio.run(ui._open_public_lfg_network(SimpleNamespace(), 123, page=1))
    view = edited.await_args.kwargs["view"]
    assert view.page == 0 and not view.has_next


def test_staff_name_is_declassified_not_member_data():
    groups = [{
        "game": "Minecraft", "community": "The 420 Lobby",
        "capacity": 6, "join_url": "https://discord.gg/the420lobby",
    }]
    embed = ui._public_network_embed(groups)
    assert "The 420 Lobby" in embed.fields[0].value
    assert "PRIVATE-HOST-ID" not in embed.description
