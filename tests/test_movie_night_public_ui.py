from __future__ import annotations

import asyncio
from types import SimpleNamespace

import discord
from discord import app_commands

from stoney_verify.command_surface_contract import (
    PUBLIC_GLOBAL_COMMAND_COUNT,
    PUBLIC_GLOBAL_COMMAND_NAMES,
)
from stoney_verify.commands_ext import public_movie_night as movie_ui
from stoney_verify.commands_ext.public_command_surface_v2 import _standalone
from stoney_verify.media_source_registry import MediaSourceRegistry
from stoney_verify.navigation_registry import feature_by_key, search_features


def _labels(view: discord.ui.View) -> set[str]:
    return {
        str(getattr(item, "label", "") or "")
        for item in view.children
        if str(getattr(item, "label", "") or "")
    }


def test_movie_is_one_compact_public_doorway() -> None:
    assert PUBLIC_GLOBAL_COMMAND_COUNT == 10
    assert PUBLIC_GLOBAL_COMMAND_NAMES == (
        "dank",
        "captions",
        "mod",
        "movie",
        "role",
        "ticket",
        "tickets",
        "toke",
        "verify",
        "View Dank Profile",
    )

    command = _standalone(
        "movie",
        "Open Movie Night.",
        movie_ui.open_movie_night_command,
    )
    assert isinstance(command, app_commands.Command)
    params = getattr(command, "_params", {})
    assert set(params) == {"magnet", "torrent"}
    assert params["magnet"].type is discord.AppCommandOptionType.string
    assert params["torrent"].type is discord.AppCommandOptionType.attachment
    assert not bool(getattr(params["magnet"], "required", True))
    assert not bool(getattr(params["torrent"], "required", True))


def test_movie_night_hub_and_setup_are_mobile_sized_and_action_complete() -> None:
    hub = movie_ui.MovieNightHubView(1)
    setup = movie_ui.MovieNightSetupView(1)
    sources = movie_ui.MovieNightSourcesView(1)

    assert {
        "Start / Join",
        "Search / Vote",
        "Queue",
        "Vote Yes",
        "Vote No",
        "Sources",
        "Setup",
        "Community & Pings",
        "Refresh",
        "Close",
    } <= _labels(hub)
    assert {
        "Create / Repair Role",
        "Sources",
        "Test Media Endpoint",
        "Community & Pings",
        "Refresh",
        "Back to Movie Night",
        "Close",
    } <= _labels(setup)
    assert {
        "Add / Update Source",
        "Manage Source",
        "Back",
        "Close",
    } <= _labels(sources)

    assert len(hub.children) <= 25
    assert len(setup.children) <= 25
    assert len(sources.children) <= 25


def test_movie_night_is_reachable_from_home_registry_and_normal_search_words() -> None:
    feature = feature_by_key("movie_night")
    assert feature is not None
    assert feature.category == "community"
    assert feature.label == "Movie Night"

    for query in ("movie night", "watch party", "group streaming", "torrent streaming"):
        matches = search_features(query)
        assert matches
        assert matches[0].key == "movie_night"


def test_setup_readiness_blocks_public_url_bound_only_to_loopback(monkeypatch) -> None:
    role = SimpleNamespace(mentionable=True)
    perms = SimpleNamespace(
        view_channel=True,
        send_messages=True,
        embed_links=True,
        attach_files=True,
        mention_everyone=False,
        administrator=False,
    )
    guild = SimpleNamespace(
        me=SimpleNamespace(
            guild_permissions=SimpleNamespace(
                administrator=False,
                manage_roles=True,
            )
        )
    )

    monkeypatch.setattr(movie_ui, "_movie_role", lambda guild, raw: role)
    monkeypatch.setattr(movie_ui, "_channel_permissions", lambda guild, channel: perms)
    monkeypatch.setattr(movie_ui, "media_public_base_url", lambda: "https://media.example.com")
    monkeypatch.setattr(movie_ui, "media_bind_host", lambda: "127.0.0.1")
    monkeypatch.setattr(movie_ui, "media_bind_port", lambda: 8080)
    monkeypatch.setattr(movie_ui, "media_server_ready", lambda: True)
    monkeypatch.setattr(
        movie_ui.importlib.util,
        "find_spec",
        lambda name: object() if name in {"libtorrent", "av"} else None,
    )
    monkeypatch.setenv("DANK_TORRENT_STREAM_SECRET", "test-secret")

    result = movie_ui._setup_readiness(
        guild,
        object(),
        {},
        MediaSourceRegistry(),
    )
    assert not result["launch_ready"]
    assert not result["externally_bound"]
    assert any("DANK_MEDIA_BIND_HOST" in item for item in result["blockers"])


def test_setup_readiness_accepts_complete_public_runtime(monkeypatch) -> None:
    role = SimpleNamespace(mentionable=True)
    perms = SimpleNamespace(
        view_channel=True,
        send_messages=True,
        embed_links=True,
        attach_files=True,
        mention_everyone=False,
        administrator=False,
    )
    guild = SimpleNamespace(
        me=SimpleNamespace(
            guild_permissions=SimpleNamespace(
                administrator=False,
                manage_roles=True,
            )
        )
    )

    monkeypatch.setattr(movie_ui, "_movie_role", lambda guild, raw: role)
    monkeypatch.setattr(movie_ui, "_channel_permissions", lambda guild, channel: perms)
    monkeypatch.setattr(movie_ui, "media_public_base_url", lambda: "https://media.example.com")
    monkeypatch.setattr(movie_ui, "media_bind_host", lambda: "0.0.0.0")
    monkeypatch.setattr(movie_ui, "media_bind_port", lambda: 8080)
    monkeypatch.setattr(movie_ui, "media_server_ready", lambda: True)
    monkeypatch.setattr(
        movie_ui.importlib.util,
        "find_spec",
        lambda name: object() if name in {"libtorrent", "av"} else None,
    )
    monkeypatch.setenv("DANK_TORRENT_STREAM_SECRET", "test-secret")

    result = movie_ui._setup_readiness(
        guild,
        object(),
        {},
        MediaSourceRegistry(),
    )
    assert result["launch_ready"]
    assert result["blockers"] == []
    assert result["externally_bound"]
    assert result["runtime_ready"]


def test_media_endpoint_check_acknowledges_before_network(monkeypatch) -> None:
    events: list[str] = []

    class FakeResponseState:
        def __init__(self) -> None:
            self.done = False

        def is_done(self) -> bool:
            return self.done

        async def defer(self, *, ephemeral: bool, thinking: bool) -> None:
            assert ephemeral and thinking
            self.done = True
            events.append("defer")

    class FakeHTTPResponse:
        status = 200

        async def __aenter__(self):
            events.append("http-enter")
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def json(self, *, content_type=None):
            _ = content_type
            return {"ok": True, "service": "dank_torrent_media"}

    class FakeSession:
        def __init__(self, *, timeout) -> None:
            _ = timeout
            assert interaction.response.done
            events.append("session")

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        def get(self, url: str, *, allow_redirects: bool):
            assert url == "https://media.example.com/health"
            assert not allow_redirects
            return FakeHTTPResponse()

    class FakeInteraction:
        def __init__(self) -> None:
            self.user = SimpleNamespace(id=123)
            self.response = FakeResponseState()
            self.message = None
            self.edits: list[dict] = []

        async def edit_original_response(self, **kwargs):
            self.edits.append(kwargs)

    interaction = FakeInteraction()
    monkeypatch.setattr(movie_ui, "media_public_base_url", lambda: "https://media.example.com")
    monkeypatch.setattr(movie_ui.aiohttp, "ClientSession", FakeSession)

    asyncio.run(movie_ui._test_public_media(interaction))

    assert events[:2] == ["defer", "session"]
    assert "http-enter" in events
    assert interaction.edits
    assert "reachable" in str(interaction.edits[-1].get("content", "")).lower()
