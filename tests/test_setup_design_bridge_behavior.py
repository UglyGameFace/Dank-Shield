from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import discord
import pytest

from stoney_verify.commands_ext import public_design_bridge as bridge
from stoney_verify.commands_ext import public_design_studio as design


def run(coroutine: Any) -> Any:
    return asyncio.run(coroutine)


class FakeResponse:
    def __init__(self) -> None:
        self.sent: dict[str, Any] = {}
        self.edited = False

    def is_done(self) -> bool:
        return False

    async def send_message(self, **kwargs: Any) -> None:
        self.sent.update(kwargs)

    async def edit_message(self, **kwargs: Any) -> None:
        self.edited = True
        raise AssertionError(
            "setup-origin Design must not replace the Setup message"
        )


class FakeFollowup:
    async def send(self, **kwargs: Any) -> None:
        raise AssertionError(
            "fresh setup interaction should use response.send_message"
        )


class FakeInteraction:
    def __init__(self) -> None:
        self.guild = SimpleNamespace(id=4242)
        self.response = FakeResponse()
        self.followup = FakeFollowup()


def test_setup_design_bridge_opens_separate_ephemeral_panel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    interaction = FakeInteraction()

    async def allow(interaction_arg: Any) -> bool:
        assert interaction_arg is interaction
        return True

    async def load_options(guild_id: int) -> dict[str, Any]:
        assert guild_id == 4242
        return {"theme_id": "gothic_clean", "strength": 2}

    def home_embed(guild: Any, options: Any) -> discord.Embed:
        assert guild is interaction.guild
        assert options["theme_id"] == "gothic_clean"
        return discord.Embed(title="Original Design Home")

    class FakeDesignHomeView:
        def __init__(self, options: Any) -> None:
            self.options = dict(options)

    monkeypatch.setattr(design, "_require_design_permission", allow)
    monkeypatch.setattr(design, "_load_design_options", load_options)
    monkeypatch.setattr(design, "_home_embed", home_embed)
    monkeypatch.setattr(design, "DesignHomeView", FakeDesignHomeView)

    run(bridge.open_design_studio_from_setup(interaction))

    assert interaction.response.edited is False
    assert interaction.response.sent["ephemeral"] is True
    assert isinstance(interaction.response.sent["embed"], discord.Embed)
    assert isinstance(
        interaction.response.sent["view"],
        FakeDesignHomeView,
    )

    embed = interaction.response.sent["embed"]
    assert embed.title == "🎨 Dank Design Studio"
    opened_from_setup = next(
        field
        for field in embed.fields
        if str(field.name) == "Opened from Setup"
    )
    assert "still open" in str(opened_from_setup.value)
    assert "return to Setup" in str(opened_from_setup.value)
