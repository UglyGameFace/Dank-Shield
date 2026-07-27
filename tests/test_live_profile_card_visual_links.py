from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from io import BytesIO
from types import SimpleNamespace

import discord
from PIL import Image

import stoney_verify.profile_card_runtime as runtime
import stoney_verify.profile_signature_live_renderer as live_renderer


class Member:
    def __init__(self) -> None:
        self.id = 42
        self.guild = SimpleNamespace(id=7, name="Dank Shield Test Server")
        self.display_name = "UgLy"
        self.name = "UgLy"
        self.roles = []
        self.joined_at = datetime.now(timezone.utc)
        self.created_at = datetime.now(timezone.utc)
        self.display_avatar = SimpleNamespace(url="https://cdn.example/avatar.png")
        self.color = discord.Color.blurple()


class Message:
    def __init__(self, embed: discord.Embed, *, filenames: tuple[str, ...] = ()) -> None:
        self.embeds = [embed]
        self.attachments = [SimpleNamespace(filename=filename) for filename in filenames]


def test_live_renderer_uses_legible_mobile_friendly_dimensions():
    payload = live_renderer.render_profile_signature(
        avatar_bytes=b"",
        display_name="UgLy",
        server_name="Dank Shield Test Server",
        role_labels=["Pronouns: He/Him"],
        date_labels=["Joined Jul 2026"],
        platform_labels=["Steam: @UGLY123"],
        style={},
    )
    with Image.open(BytesIO(payload)) as image:
        assert image.size == (1080, 300)
    assert live_renderer.SIGNATURE_RATIO == 3.6


def test_official_profile_links_use_compact_buttons_without_text_duplication(monkeypatch):
    async def scenario() -> None:
        member = Member()

        async def settings(_guild_id: int, _user_id: int):
            return {
                "preferences": {
                    "live_cards_enabled": True,
                    "show_roles": False,
                    "show_account_dates": False,
                    "show_platforms": True,
                },
                "platforms": {
                    "steam": {
                        "platform": "steam",
                        "username": "@UGLY123",
                        "url": "https://steamcommunity.com/id/UGLY123",
                        "shared": True,
                    }
                },
            }

        async def config(_guild_id: int):
            return {}

        async def image_renderer(_member, **_kwargs):
            return b"image"

        monkeypatch.setattr(runtime, "get_effective_profile_settings", settings)
        monkeypatch.setattr(runtime, "get_guild_config", config)
        monkeypatch.setattr(runtime, "render_member_profile_signature", image_renderer)
        runtime._SIGNATURE_CACHE.clear()

        rendered = await runtime.render_live_profile_card(
            member,
            {"platforms"},
            trigger_message_id=99,
        )

        assert rendered is not None
        assert rendered.embed.description is None
        assert rendered.view is not None
        links = [child for child in rendered.view.children if isinstance(child, discord.ui.Button)]
        assert len(links) == 1
        assert links[0].url == "https://steamcommunity.com/id/UGLY123"
        assert "Steam" in str(links[0].label)
        assert not str(getattr(rendered.embed.footer, "text", "") or "")
        assert rendered.embed.url == runtime.live_card_marker_url(member.id, 99)
        assert rendered.file is not None
        assert rendered.file.filename == "dank-live-profile-42-99.png"
        assert runtime.parse_live_card_footer(
            Message(rendered.embed, filenames=(rendered.file.filename,))
        ) == (member.id, 99)

    asyncio.run(scenario())


def test_url_capable_identity_without_link_stays_in_image_only(monkeypatch):
    async def scenario() -> None:
        member = Member()
        captured: dict[str, object] = {}

        async def settings(_guild_id: int, _user_id: int):
            return {
                "preferences": {
                    "live_cards_enabled": True,
                    "show_roles": False,
                    "show_account_dates": False,
                    "show_platforms": True,
                },
                "platforms": {
                    "steam": {
                        "platform": "steam",
                        "username": "@UGLY123",
                        "url": "",
                        "shared": True,
                    }
                },
            }

        async def config(_guild_id: int):
            return {}

        async def image_renderer(_member, **kwargs):
            captured.update(kwargs)
            return b"image"

        monkeypatch.setattr(runtime, "get_effective_profile_settings", settings)
        monkeypatch.setattr(runtime, "get_guild_config", config)
        monkeypatch.setattr(runtime, "render_member_profile_signature", image_renderer)
        runtime._SIGNATURE_CACHE.clear()

        rendered = await runtime.render_live_profile_card(
            member,
            {"platforms"},
            trigger_message_id=100,
        )

        assert rendered is not None
        assert rendered.embed.description is None
        assert rendered.view is None
        assert captured["platform_labels"] == ["Steam: @UGLY123"]

    asyncio.run(scenario())


def test_username_only_public_accounts_remain_in_image_without_fake_links(monkeypatch):
    async def scenario() -> None:
        member = Member()
        captured: dict[str, object] = {}

        async def settings(_guild_id: int, _user_id: int):
            return {
                "preferences": {
                    "live_cards_enabled": True,
                    "show_roles": False,
                    "show_account_dates": False,
                    "show_platforms": True,
                },
                "platforms": {
                    "xbox": {
                        "platform": "xbox",
                        "username": "UGLY123",
                        "url": "",
                        "shared": True,
                    }
                },
            }

        async def config(_guild_id: int):
            return {}

        async def image_renderer(_member, **kwargs):
            captured.update(kwargs)
            return b"image"

        monkeypatch.setattr(runtime, "get_effective_profile_settings", settings)
        monkeypatch.setattr(runtime, "get_guild_config", config)
        monkeypatch.setattr(runtime, "render_member_profile_signature", image_renderer)
        runtime._SIGNATURE_CACHE.clear()

        rendered = await runtime.render_live_profile_card(
            member,
            {"platforms"},
            trigger_message_id=101,
        )

        assert rendered is not None
        assert rendered.embed.description is None
        assert rendered.view is None
        assert captured["platform_labels"] == ["Xbox: UGLY123"]

    asyncio.run(scenario())


def test_legacy_footer_and_attachment_markers_remain_cleanup_compatible():
    legacy = discord.Embed()
    legacy.set_footer(text=runtime.live_card_footer(55, 66))
    assert runtime.parse_live_card_footer(Message(legacy)) == (55, 66)

    modern = discord.Embed()
    assert runtime.parse_live_card_footer(
        Message(modern, filenames=("dank-live-profile-77-88.png",))
    ) == (77, 88)
