from __future__ import annotations

import asyncio
from types import SimpleNamespace

import discord

import stoney_verify.profile_card_runtime_core as core
import stoney_verify.profile_card_service as service
from stoney_verify.commands_ext import public_profile_cards


def test_logo_only_entry_needs_no_username_or_url():
    entry = service.normalize_platform_entry("xbox", shared=True, mode="logo")
    assert entry["shared"] is True
    assert entry["mode"] == "logo"
    assert entry["username"] == ""
    assert entry["url"] == ""


def test_legacy_entries_resolve_without_database_migration():
    assert service.platform_entry_mode({"url": "https://example.test/profile"}) == "link"
    assert service.platform_entry_mode({"username": "UglyGameFace"}) == "username"
    assert service.platform_entry_mode({}) == "logo"


def test_controls_are_text_only_and_logo_mode_has_no_dead_button():
    view = core._platform_view(
        [
            {"platform": "twitch", "username": "Streamer", "url": "https://twitch.tv/streamer", "shared": True, "mode": "link"},
            {"platform": "xbox", "username": "UglyGameFace", "url": "", "shared": True, "mode": "username"},
            {"platform": "playstation", "username": "", "url": "", "shared": True, "mode": "logo"},
        ],
        owner_user_id=42,
    )
    assert view is not None
    buttons = [child for child in view.children if isinstance(child, discord.ui.Button)]
    assert len(buttons) == 2
    assert buttons[0].label == "Twitch"
    assert buttons[0].emoji is None
    assert buttons[1].label == "UglyGameFace"
    assert buttons[1].emoji is None
    assert buttons[1].custom_id == "dank:profilecopy:v1:42:xbox"


def test_copy_button_rechecks_current_privacy_and_returns_copy_ready_text(monkeypatch):
    async def scenario() -> None:
        async def user_row(_user_id: int, refresh: bool = False):
            assert refresh is True
            return {
                "preferences": {"show_platforms": True},
                "platforms": {"xbox": {"username": "UglyGameFace", "shared": True, "mode": "username"}},
            }

        async def guild_row(_guild_id: int, _user_id: int, refresh: bool = False):
            assert refresh is True
            return {"settings": {}}

        sent: dict[str, object] = {}

        class Response:
            def is_done(self) -> bool:
                return False

            async def send_message(self, **kwargs):
                sent.update(kwargs)

        interaction = SimpleNamespace(
            type=discord.InteractionType.component,
            data={"custom_id": "dank:profilecopy:v1:42:xbox"},
            guild=SimpleNamespace(id=7),
            response=Response(),
            followup=SimpleNamespace(send=None),
        )
        monkeypatch.setattr(public_profile_cards, "get_profile_user", user_row)
        monkeypatch.setattr(public_profile_cards, "get_profile_guild_settings", guild_row)
        await public_profile_cards._handle_profile_username_copy(interaction)
        assert sent["ephemeral"] is True
        assert sent["content"] == "```text\nUglyGameFace\n```"

    asyncio.run(scenario())
