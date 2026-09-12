from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import discord
import pytest

from stoney_verify import profile_card_setup_ui as profile
from stoney_verify import profile_card_setup_ui_core as profile_core
from stoney_verify import welcome_setup_ui as welcome
from stoney_verify.commands_ext import public_setup_compact as runtime


runtime.apply_public_setup_runtime()


def run(coroutine: Any) -> Any:
    return asyncio.run(coroutine)


class FakeResponse:
    def __init__(self) -> None:
        self.done = False
        self.deferred = False
        self.sent: list[dict[str, Any]] = []

    def is_done(self) -> bool:
        return self.done

    async def defer(self, **kwargs: Any) -> None:
        assert kwargs == {"thinking": False}
        self.deferred = True
        self.done = True

    async def send_message(self, *args: Any, **kwargs: Any) -> None:
        self.sent.append({"args": args, **kwargs})
        self.done = True


class FakeFollowup:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    async def send(self, *args: Any, **kwargs: Any) -> None:
        self.sent.append({"args": args, **kwargs})


class FakeInteraction:
    def __init__(self, *, component: bool) -> None:
        self.id = 101
        self.guild = SimpleNamespace(id=202)
        self.user = SimpleNamespace(id=303)
        self.data = {"custom_id": "dank_setup_compact:area"}
        self.message = SimpleNamespace(id=404) if component else None
        self.response = FakeResponse()
        self.followup = FakeFollowup()
        self.edits: list[dict[str, Any]] = []

    async def edit_original_response(self, **kwargs: Any) -> None:
        self.edits.append(kwargs)


async def allow(_interaction: Any) -> bool:
    return True


def test_welcome_component_entry_acknowledges_then_edits_same_setup_panel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    interaction = FakeInteraction(component=True)
    events: list[str] = []
    expected_view = object()

    async def get_config(guild_id: int, refresh: bool = False) -> dict[str, Any]:
        assert interaction.response.is_done() is True
        assert guild_id == 202
        assert refresh is True
        events.append("config")
        return {"welcome_card_enabled": False}

    async def embed(_guild: Any, _config: Any) -> discord.Embed:
        events.append("embed")
        return discord.Embed(title="Welcome")

    monkeypatch.setattr(welcome, "_require_setup_permission", allow)
    monkeypatch.setattr(welcome, "get_guild_config", get_config)
    monkeypatch.setattr(welcome, "_welcome_embed", embed)
    monkeypatch.setattr(welcome, "WelcomeSetupView", lambda **_kwargs: expected_view)

    run(welcome.open_welcome_setup(interaction))

    assert interaction.response.deferred is True
    assert events == ["config", "embed"]
    assert len(interaction.edits) == 1
    assert interaction.edits[0]["view"] is expected_view
    assert interaction.followup.sent == []
    assert interaction.response.sent == []


def test_profile_component_entry_acknowledges_then_edits_same_setup_panel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    interaction = FakeInteraction(component=True)
    events: list[str] = []
    expected_view = object()

    async def get_config(guild_id: int, refresh: bool = False) -> dict[str, Any]:
        assert interaction.response.is_done() is True
        assert guild_id == 202
        assert refresh is True
        events.append("config")
        return {}

    def embed(_guild: Any, _config: Any) -> discord.Embed:
        events.append("embed")
        return discord.Embed(title="Profiles")

    monkeypatch.setattr(profile, "_require_setup_permission", allow)
    monkeypatch.setattr(profile, "get_guild_config", get_config)
    monkeypatch.setattr(profile, "_setup_embed", embed)
    monkeypatch.setattr(profile, "ProfileCardSetupView", lambda **_kwargs: expected_view)

    run(profile.open_profile_card_setup(interaction))

    assert interaction.response.deferred is True
    assert events == ["config", "embed"]
    assert len(interaction.edits) == 1
    assert interaction.edits[0]["view"] is expected_view
    assert interaction.followup.sent == []
    assert interaction.response.sent == []


def test_standalone_welcome_and_profile_entrypoints_keep_original_behavior(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    welcome_interaction = FakeInteraction(component=False)
    profile_interaction = FakeInteraction(component=False)
    calls: list[tuple[str, Any]] = []

    async def original_welcome(interaction: Any) -> None:
        calls.append(("welcome", interaction))

    async def original_profile(interaction: Any) -> None:
        calls.append(("profile", interaction))

    monkeypatch.setattr(
        welcome,
        "_dank_setup_original_open_welcome_setup",
        original_welcome,
    )
    monkeypatch.setattr(
        profile,
        "_dank_setup_original_open_profile_card_setup",
        original_profile,
    )

    run(welcome.open_welcome_setup(welcome_interaction))
    run(profile.open_profile_card_setup(profile_interaction))

    assert calls == [
        ("welcome", welcome_interaction),
        ("profile", profile_interaction),
    ]
    assert welcome_interaction.edits == []
    assert profile_interaction.edits == []


def test_profile_refresh_helper_uses_runtime_no_fork_owner() -> None:
    assert profile._edit_or_send is profile_core._edit_or_send
    assert profile._edit_or_send.__name__ == "_profile_edit_or_send"
