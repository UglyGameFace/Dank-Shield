"""Contract coverage for free outside-player discovery, no accounts or live writes."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
import pytest

from stoney_verify import community_free_lfg as free
from stoney_verify.community_external_lfg import ExternalDiscoveryError
from stoney_verify.commands_ext import public_community_hub as ui


def _payload():
    return {
        "data": [
            {
                "username": "Tester", "displayName": "@everyone Explorer",
                "headline": "Looking for Bedrock friends", "edition": "BEDROCK",
                "modes": ["SURVIVAL", "SMP"], "language": "en",
                "email": "NEVER-LEAK@example.com",
                "privateMessage": "do not copy me", "discordToken": "secret",
                "url": "https://attacker.example/",
            },
            {"username": "Tester", "displayName": "duplicate", "edition": "JAVA"},
            {"username": "", "displayName": "no name", "edition": "JAVA"},
        ],
        "meta": {"page": 1, "pages": 4, "perPage": 20, "total": 61},
    }


def test_documented_public_profile_projection_excludes_private_data():
    rows, pages = free.normalize_minecraft_players(_payload(), page=1)
    assert pages == 4
    assert rows == [{
        "username": "Tester",
        "name": "＠everyone Explorer",
        "edition": "BEDROCK",
        "headline": "Looking for Bedrock friends",
        "language": "en", "modes": ["SURVIVAL", "SMP"],
        "source": "Minecraft LFG",
    }]
    for private_value in ("NEVER-LEAK", "privateMessage", "discordToken", "attacker.example"):
        assert private_value not in repr(rows)


def test_provider_rejects_invalid_api_envelopes_and_malformed_records():
    for bad in ([], {}, {"data": {}}, None):
        with pytest.raises(ExternalDiscoveryError, match="unfamiliar"):
            free.normalize_minecraft_players(bad, page=1)
    with pytest.raises(ExternalDiscoveryError, match="oversized"):
        free.normalize_minecraft_players({"data": [{}] * 51}, page=1)
    assert free.normalize_minecraft_players({"data": [{"username": "A", "edition": "UNKNOWN"}]}, page=1)[0] == []


class _Content:
    async def read(self, n):
        return b'{"data":[{"username":"Tester","displayName":"Tester","edition":"JAVA"}],"meta":{"pages":2}}'[:n]


class _Response:
    status = 200
    content = _Content()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass


class _Session:
    calls = []

    def __init__(self, *, timeout):
        assert timeout.total == 8

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    def get(self, url, *, params, headers, allow_redirects):
        self.calls.append((url, params, headers, allow_redirects))
        return _Response()


def test_provider_uses_exact_official_url_and_caches(monkeypatch):
    monkeypatch.setattr(free, "_NEXT_ALLOWED_REQUEST", 0.0)
    monkeypatch.setattr(free, "_cache", {})
    monkeypatch.setattr(free.aiohttp, "ClientSession", _Session)
    _Session.calls = []
    async def exercise():
        first = await free.list_minecraft_players(page=1, edition="JAVA")
        second = await free.list_minecraft_players(page=1, edition="JAVA")
        return first, second
    first, second = asyncio.run(exercise())
    assert first == second
    assert len(first[0]) == 1
    assert len(_Session.calls) == 1
    url, params, headers, redirects = _Session.calls[0]
    assert url == "https://minecraftlfg.com/api/v1/players"
    assert params == {"page": 1, "perPage": 20, "edition": "JAVA"}
    assert redirects is False
    assert "Authorization" not in headers
    assert "DankShield" in headers["User-Agent"]


def test_no_upstream_request_burst_and_invalid_filters_fail_before_fetch(monkeypatch):
    monkeypatch.setattr(free, "_NEXT_ALLOWED_REQUEST", 0.0)
    monkeypatch.setattr(free, "_cache", {})
    monkeypatch.setattr(free.aiohttp, "ClientSession", _Session)
    async def exercise():
        await free.list_minecraft_players(page=1)
        with pytest.raises(ExternalDiscoveryError, match="cooling down"):
            await free.list_minecraft_players(page=2)
    asyncio.run(exercise())
    with pytest.raises(ExternalDiscoveryError, match="edition"):
        asyncio.run(free.list_minecraft_players(edition="malicious"))
    with pytest.raises(ExternalDiscoveryError, match="Language"):
        asyncio.run(free.list_minecraft_players(language="../../etc"))


def test_public_manual_communities_have_whitelisted_static_urls():
    destinations = free.provider_navigation()
    assert len(destinations) >= 5
    assert len({d["url"] for d in destinations}) == len(destinations)
    assert all(d["url"].startswith("https://www.reddit.com/r/") for d in destinations)
    assert not any("discord.gg" in d["url"] for d in destinations)


def test_minecraft_view_and_navigation_fit_discord_mobile():
    view = ui.MinecraftPlayersView(123, page=2, pages=4, edition="JAVA")
    assert len(view.children) <= 5
    assert {getattr(x, "row", 0) for x in view.children} <= {0, 1, 2}
    select = next(x for x in view.children if isinstance(x, discord.ui.Select))
    assert len(select.options) == 4
    assert select.custom_id == "dank:hub:outside:minecraft:edition:v1"
    link = next(x for x in view.children if isinstance(x, discord.ui.Button) and x.style == discord.ButtonStyle.link)
    assert link.url == free.MINECRAFT_SITE_URL
    outbound = ui.OutsideCommunitiesView(123)
    assert sum(isinstance(x, discord.ui.Button) and x.style == discord.ButtonStyle.link for x in outbound.children) == 5


def test_outside_minecraft_search_honors_guild_maintenance(monkeypatch):
    read_settings = AsyncMock(return_value={"enabled": True, "maintenance_mode": True})
    listing = AsyncMock(return_value=([{"username": "Never"}], 1))
    edit = AsyncMock()
    monkeypatch.setattr(ui.hub, "get_settings", read_settings)
    monkeypatch.setattr(free, "list_minecraft_players", listing)
    monkeypatch.setattr(ui, "_edit_private_original", edit)
    asyncio.run(ui._open_minecraft_players(SimpleNamespace(guild_id=123), 44))
    listing.assert_not_awaited()
    assert "unavailable" in edit.await_args.kwargs["embed"].fields[0].value


def test_minecraft_search_filters_are_forwarded(monkeypatch):
    read_settings = AsyncMock(return_value={"enabled": True, "maintenance_mode": False})
    listing = AsyncMock(return_value=([{
        "username": "Tester", "name": "Tester", "edition": "JAVA",
        "headline": "SMP", "modes": ["SMP"], "language": "en",
    }], 3))
    edit = AsyncMock()
    monkeypatch.setattr(ui.hub, "get_settings", read_settings)
    monkeypatch.setattr(free, "list_minecraft_players", listing)
    monkeypatch.setattr(ui, "_edit_private_original", edit)
    asyncio.run(ui._open_minecraft_players(SimpleNamespace(guild_id=123), 44, page=2, edition="JAVA"))
    listing.assert_awaited_once_with(page=2, edition="JAVA")
    assert "Tester" in edit.await_args.kwargs["embed"].fields[0].name
    assert edit.await_args.kwargs["view"].page == 2
