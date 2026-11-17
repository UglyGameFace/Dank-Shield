from __future__ import annotations

import asyncio
import inspect
from types import SimpleNamespace

import discord

from stoney_verify.commands_ext import public_toke
from stoney_verify.commands_ext import public_owner_authority


def labels(view: discord.ui.View) -> set[str]:
    return {str(item.label) for item in view.children if isinstance(item, discord.ui.Button)}


def test_member_toke_home_is_compact_and_staff_controls_are_scoped() -> None:
    member = public_toke.TokeHomeView(123, can_manage=False)
    staff = public_toke.TokeHomeView(123, can_manage=True)
    assert member.timeout == 600
    assert labels(member) == {"Start Sesh", "My 4:20 Reminders", "World 4:20 Clock"}
    assert labels(staff) == labels(member) | {"Server 4:20 Setup"}
    assert all(button.style is not discord.ButtonStyle.link for button in member.children)


def test_toke_slash_is_shorter_but_keeps_upload_and_voice() -> None:
    parameters = inspect.signature(public_toke.open_toke_command).parameters
    assert {"interaction", "message", "upload", "voice"} == set(parameters)
    assert "media" not in parameters  # GIF links still work in the normal composer.
    assert "media" in inspect.signature(public_toke._open_toke_command_impl).parameters


def test_bare_toke_opens_home_but_explicit_post_uses_existing_send_path(monkeypatch) -> None:
    events: list[tuple[str, object]] = []

    async def fake_home(interaction):
        events.append(("home", interaction))

    async def fake_send(interaction, **kwargs):
        events.append(("send", kwargs))

    monkeypatch.setattr(public_toke, "open_toke_home", fake_home)
    monkeypatch.setattr(public_toke, "_open_toke_command_impl", fake_send)
    interaction = object()

    async def check():
        await public_toke.open_toke_command(interaction)
        await public_toke.open_toke_command(interaction, message="Let's go")
        await public_toke.open_toke_command(interaction, upload=object())
        await public_toke.open_toke_command(interaction, voice=object())

    asyncio.run(check())
    assert events[0] == ("home", interaction)
    assert len([name for name, _ in events if name == "send"]) == 3
    assert events[1][1]["message"] == "Let's go"
    assert events[2][1]["upload"] is not None
    assert events[3][1]["voice"] is not None


def test_toke_home_open_is_private_and_does_not_require_starter_role(monkeypatch) -> None:
    calls = []

    class Response:
        def is_done(self):
            return False

        async def send_message(self, **kwargs):
            calls.append(kwargs)

    monkeypatch.setattr(public_owner_authority, "interaction_has_manage_guild_authority", lambda _i: False)
    interaction = SimpleNamespace(
        guild=SimpleNamespace(id=1),
        user=SimpleNamespace(id=123),
        response=Response(),
        message=None,
    )
    asyncio.run(public_toke.open_toke_home(interaction))
    assert len(calls) == 1
    assert calls[0]["ephemeral"] is True
    assert labels(calls[0]["view"]) == {"Start Sesh", "My 4:20 Reminders", "World 4:20 Clock"}
    assert calls[0]["allowed_mentions"].everyone is False
    assert calls[0]["embed"].title == "💨 Toke & 4:20"


def test_toke_buttons_handoff_to_existing_owners(monkeypatch) -> None:
    from stoney_verify.commands_ext import public_somewhere_420, public_somewhere_420_settings

    events: list[str] = []

    async def sesh(_interaction):
        events.append("sesh")

    async def personal(_interaction):
        events.append("personal")

    async def world(_interaction):
        events.append("world")

    async def server(_interaction):
        events.append("server")

    monkeypatch.setattr(public_toke, "_open_toke_command_impl", sesh)
    monkeypatch.setattr(public_somewhere_420_settings, "open_personal_420", personal)
    monkeypatch.setattr(public_somewhere_420_settings, "open_staff_420", server)
    monkeypatch.setattr(public_somewhere_420, "open_somewhere_420", world)
    view = public_toke.TokeHomeView(123, can_manage=True)
    async def check():
        for label in ("Start Sesh", "My 4:20 Reminders", "World 4:20 Clock", "Server 4:20 Setup"):
            item = next(button for button in view.children if button.label == label)
            await item.callback(SimpleNamespace(user=SimpleNamespace(id=123)))
    asyncio.run(check())
    assert events == ["sesh", "personal", "world", "server"]
