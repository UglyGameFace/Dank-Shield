from __future__ import annotations

import asyncio
from types import SimpleNamespace

import discord

import stoney_verify.profile_card_runtime_core as runtime_core
import stoney_verify.profile_card_service as service
from stoney_verify.commands_ext import public_profile_cards


def test_uploaded_application_logos_are_wired_by_real_ids() -> None:
    expected = {
        "youtube": 1531448153054773288,
        "xbox": 1531448152157061130,
        "nintendo": 1531448151255289917,
        "steam": 1531448150173286460,
        "playstation": 1531448147899977909,
        "roblox": 1531448146968842240,
        "epic": 1531448144796188702,
        "riot": 1531448143609331835,
        "twitch": 1531448142481064047,
        "kick": 1531448141604323338,
    }
    for key, emoji_id in expected.items():
        spec = service.PLATFORM_SPECS[key]
        assert spec.application_emoji_id == emoji_id
        assert spec.emoji == f"<:{key}:{emoji_id}>"
        assert str(emoji_id) in str(spec.logo_url)


def test_logo_only_entry_requires_no_username_or_link() -> None:
    entry = service.normalize_platform_entry(
        "xbox",
        username="",
        profile_url="",
        shared=True,
        mode="logo",
    )
    assert entry["shared"] is True
    assert entry["mode"] == "logo"
    assert entry["username"] == ""
    assert entry["url"] == ""


def test_legacy_entries_resolve_without_database_migration() -> None:
    assert service.platform_entry_mode({"url": "https://twitch.tv/example"}) == "link"
    assert service.platform_entry_mode({"username": "Example"}) == "username"
    assert service.platform_entry_mode({}) == "logo"


def test_component_controls_use_real_logos_and_skip_dead_logo_only_button() -> None:
    view = runtime_core._platform_view(
        [
            {
                "platform": "twitch",
                "username": "Streamer",
                "url": "https://twitch.tv/streamer",
                "shared": True,
                "mode": "link",
            },
            {
                "platform": "xbox",
                "username": "UglyGameFace",
                "url": "",
                "shared": True,
                "mode": "username",
            },
            {
                "platform": "playstation",
                "username": "",
                "url": "",
                "shared": True,
                "mode": "logo",
            },
        ],
        owner_user_id=42,
    )
    assert view is not None
    buttons = [child for child in view.children if isinstance(child, discord.ui.Button)]
    assert len(buttons) == 2
    assert buttons[0].url == "https://twitch.tv/streamer"
    assert int(buttons[0].emoji.id) == 1531448142481064047
    assert buttons[1].label == "UglyGameFace"
    assert buttons[1].custom_id == "dank:profilecopy:v1:42:xbox"
    assert int(buttons[1].emoji.id) == 1531448152157061130


def test_copy_button_rechecks_current_privacy_and_returns_plain_copy_value(monkeypatch) -> None:
    async def scenario() -> None:
        async def user_row(_user_id: int, refresh: bool = False):
            assert refresh is True
            return {
                "preferences": {"show_platforms": True},
                "platforms": {
                    "xbox": {
                        "platform": "xbox",
                        "username": "UglyGameFace",
                        "url": "",
                        "shared": True,
                        "mode": "username",
                    }
                },
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

        handled = await public_profile_cards._handle_profile_username_copy(interaction)
        assert handled is True
        assert sent["ephemeral"] is True
        assert sent["content"] == "UglyGameFace"

    asyncio.run(scenario())
