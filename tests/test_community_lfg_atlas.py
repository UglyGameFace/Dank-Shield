"""LFG Atlas keyless multi-game API with no untrusted outbound contacts."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
import pytest

from stoney_verify import community_lfg_atlas as atlas
from stoney_verify.community_external_lfg import ExternalDiscoveryError
from stoney_verify.commands_ext import public_community_hub as ui


def _item(**overrides):
    record = {
        "id": "post-1", "title": "@everyone Need two for Fortnite",
        "game": "fortnite", "type": "lfm", "region": "NA",
        "mode": "duos", "ready_now": True, "source": "atlas",
        "url": "https://lfgatlas.pages.dev/posts/123",
        "contact": {
            "value": "NEVER_EXPOSE_CONTACT", "kind": "discord",
            "join_url": "https://evil.example/steal"
        }, "description": "NEVER_EXPOSE_DESCRIPTION"
    }
    record.update(overrides)
    return record


def test_atlas_parses_json_and_ignores_private_untrusted_fields():
    got = atlas.normalize_atlas_posts({"posts": [_item()]})
    assert got == [{
        "game": "fortnite", "title": "＠everyone Need two for Fortnite",
        "type": "LFM", "region": "NA", "mode": "duos",
        "source": "atlas", "ready_now": True,
        "url": "https://lfgatlas.pages.dev/posts/123",
    }]
    assert "NEVER_EXPOSE" not in repr(got)
    assert "evil.example" not in repr(got)
    assert atlas.normalize_atlas_posts({"data": {"posts": [_item()]}}) == got


def test_unknown_envelopes_are_not_silent_empty_groups():
    for bad in ({}, None, "bad", {"data": {"foo": []}}, {"posts": {}}):
        with pytest.raises(ExternalDiscoveryError, match="unfamiliar"):
            atlas.normalize_atlas_posts(bad)
    with pytest.raises(ExternalDiscoveryError, match="oversized"):
        atlas.normalize_atlas_posts([_item(id=str(i)) for i in range(101)])


def test_untrusted_urls_never_become_clickable():
    for url in (
        "http://lfgatlas.pages.dev/posts/123",
        "https://evil.example/",
        "https://lfgatlas.pages.dev.evil.example/posts/123",
        "https://user:pass@lfgatlas.pages.dev/",
        "https://lfgatlas.pages.dev:abc/post",
        "https://lfgatlas.pages.dev:1234/post",
        "https://lfgatlas.pages.dev/#frag",
        "https://lfgatlas.pages.dev/\\host",
    ):
        assert atlas.normalize_atlas_posts([_item(url=url)])[0]["url"] == atlas.SITE
    assert atlas.normalize_atlas_posts([_item(url="https://lfgatlas-bot.jonsimm36.workers.dev/groups/x")])[0]["url"].startswith("https://lfgatlas-bot")


def test_duplicates_and_expired_posts_are_excluded():
    result = atlas.normalize_atlas_posts([
        _item(id="x", expires_at="2022-01-01T00:00:00Z"),
        _item(id="y"), _item(id="y", title="duplicate"),
    ])
    assert len(result) == 1
    assert result[0]["title"].startswith("＠everyone")


class _Content:
    async def read(self, n):
        return b'{"posts":[{"id":"1","title":"Need a squad","game":"minecraft","type":"lfg","region":"EU"}]}'[:n]


class _Response:
    status = 200
    content = _Content()
    async def __aenter__(self): return self
    async def __aexit__(self, *args): pass


class _Client:
    calls = []
    def __init__(self, *, timeout):
        assert timeout.total == 8
    async def __aenter__(self): return self
    async def __aexit__(self, *args): pass
    def get(self, url, *, params, headers, allow_redirects):
        self.calls.append((url, params, headers, allow_redirects))
        return _Response()


def test_atlas_read_only_request_params_and_cache(monkeypatch):
    monkeypatch.setattr(atlas, "_next_allowed_request", 0.0)
    monkeypatch.setattr(atlas, "_cache", {})
    monkeypatch.setattr(atlas.aiohttp, "ClientSession", _Client)
    _Client.calls = []
    async def exercise():
        return (
            await atlas.list_atlas_posts(game="minecraft", region="EU", ready_now=True, group_type="lfm"),
            await atlas.list_atlas_posts(game="minecraft", region="EU", ready_now=True, group_type="lfm"),
        )
    first, second = asyncio.run(exercise())
    assert first == second and len(first) == 1
    assert len(_Client.calls) == 1
    url, params, headers, redirects = _Client.calls[0]
    assert url == atlas.API
    assert params == {"limit": 25, "game": "minecraft", "region": "EU", "ready_now": 1, "type": "lfm"}
    assert redirects is False
    assert "Authorization" not in headers


def test_global_rest_pacing_and_safety_filters(monkeypatch):
    monkeypatch.setattr(atlas, "_next_allowed_request", 0.0)
    monkeypatch.setattr(atlas, "_cache", {})
    monkeypatch.setattr(atlas.aiohttp, "ClientSession", _Client)
    async def exercise():
        await atlas.list_atlas_posts(game="minecraft")
        with pytest.raises(ExternalDiscoveryError, match="cooling down"):
            await atlas.list_atlas_posts(game="valorant")
    asyncio.run(exercise())
    with pytest.raises(ExternalDiscoveryError, match="normal game title"):
        asyncio.run(atlas.list_atlas_posts(game="../../etc"))
    with pytest.raises(ExternalDiscoveryError, match="region"):
        asyncio.run(atlas.list_atlas_posts(region="http://evil.example/"))


def test_mobile_atlas_panel_and_outbound_link_are_bounded():
    view = ui.AtlasGroupsView(111, [], game="Minecraft", ready=True)
    assert len(view.children) <= 25
    assert {getattr(item, "row", 0) for item in view.children} <= {0, 1, 2, 3}
    type_menu = next(x for x in view.children if isinstance(x, discord.ui.Select))
    assert len(type_menu.options) == 6
    assert type_menu.custom_id == "dank:hub:atlas:type:v1"
    assert next(x for x in view.children if isinstance(x, discord.ui.Button) and x.style == discord.ButtonStyle.link).url == atlas.SITE
    modal = ui.AtlasSearchModal(111, game="minecraft", region="NA")
    assert modal.game.default == "minecraft"
    assert modal.region.default == "NA"


def test_atlas_ui_never_displays_untrusted_contact(monkeypatch):
    rows = atlas.normalize_atlas_posts({"posts": [_item()]})
    embed = ui._atlas_groups_embed(rows, page=0, game="fortnite", region="", ready=False)
    all_text = str([(field.name, field.value) for field in embed.fields])
    assert "evil.example" not in all_text
    assert "NEVER_EXPOSE_CONTACT" not in all_text
    assert "＠everyone" in all_text


def test_atlas_guild_maintenance_does_not_fetch(monkeypatch):
    settings = AsyncMock(return_value={"enabled": True, "maintenance_mode": True})
    fetch = AsyncMock(return_value=[])
    edit = AsyncMock()
    monkeypatch.setattr(ui.hub, "get_settings", settings)
    monkeypatch.setattr(atlas, "list_atlas_posts", fetch)
    monkeypatch.setattr(ui, "_edit_private_original", edit)
    asyncio.run(ui._open_atlas_groups(SimpleNamespace(guild_id=123), 1))
    fetch.assert_not_awaited()
    assert "unavailable" in edit.await_args.kwargs["embed"].fields[0].value


def test_public_atlas_group_type_choice_is_included_in_subsequent_reads(monkeypatch):
    settings = AsyncMock(return_value={"enabled": True, "maintenance_mode": False})
    upstream = AsyncMock(return_value=[{
        "game": "Minecraft", "title": "Find a clan", "type": "CLAN",
        "region": "NA", "mode": "SMP", "source": "atlas",
        "ready_now": True, "url": atlas.SITE,
    }])
    edit = AsyncMock()
    monkeypatch.setattr(ui.hub, "get_settings", settings)
    monkeypatch.setattr(atlas, "list_atlas_posts", upstream)
    monkeypatch.setattr(ui, "_edit_private_original", edit)
    asyncio.run(ui._open_atlas_groups(
        SimpleNamespace(guild_id=123), 1,
        game="minecraft", region="NA", ready=True, group_type="clan",
    ))
    upstream.assert_awaited_once_with(
        game="minecraft", region="NA", ready_now=True, group_type="clan",
    )
    view = edit.await_args.kwargs["view"]
    assert view.group_type == "clan"
    menu = next(x for x in view.children if isinstance(x, discord.ui.Select))
    assert any(opt.value == "clan" and opt.default for opt in menu.options)
