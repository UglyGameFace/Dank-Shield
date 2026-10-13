from __future__ import annotations

"""Prevent Discord from showing a Toke GIF twice after live-card edits."""

import asyncio
from types import SimpleNamespace

import discord
import pytest

from stoney_verify.commands_ext import public_toke as toke
from stoney_verify import toke_live_ui as live


class _FakeMember:
    def __init__(self):
        self.id = 7839
        self.display_name = "🎃👻💀UglyGameFace💀👻🎃"


class _FakeChannel:
    def __init__(self):
        self.id = 99981
        self.sent_payload = None
        self.edited_payload = None

    def permissions_for(self, _member):
        return SimpleNamespace(
            view_channel=True, send_messages=True, embed_links=True,
            attach_files=True, mention_everyone=True,
        )

    async def send(self, **kwargs):
        self.sent_payload = kwargs
        channel = self

        class Sent:
            id = 7654321
            jump_url = "https://discord.com/channels/33/99981/7654321"

            async def edit(self, **updates):
                channel.edited_payload = updates

            async def delete(self):
                raise AssertionError("successful send must not delete its Toke card")

        return Sent()


async def _run_publish(monkeypatch, *, upload=None, media=""):
    guild = SimpleNamespace(id=55378, me=_FakeMember())
    member = guild.me
    channel = _FakeChannel()
    starter_role = SimpleNamespace(id=776, mention="<@&776>")
    notify_role = SimpleNamespace(id=555, mention="<@&555>")

    monkeypatch.setattr(toke.discord, "Member", _FakeMember)
    monkeypatch.setattr(toke, "_ping_permission_ready", lambda *_args: True)
    monkeypatch.setattr(toke, "_toke_allowed_mentions", lambda *_args: discord.AllowedMentions.none())
    monkeypatch.setattr(toke, "choose_voice", lambda *_args: 0)
    monkeypatch.setattr(toke, "TokeCheersView", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(
        toke, "TokeMediaLayout",
        lambda _guild, session, *, intro: SimpleNamespace(
            filename=session["media_filename"], intro=intro, v2=True,
        ),
    )
    monkeypatch.setattr(toke, "render_card", lambda _guild, _session, card: card.copy())

    async def no_cooldown(*_args, **_kwargs):
        return 0.0, ""

    async def save_new(data):
        return {**data, "active": True, "cheers_ids": [], "going_ids": []}

    monkeypatch.setattr(toke, "_persistent_cooldown_remaining", no_cooldown)
    monkeypatch.setattr(toke.toke_sessions, "save_new", save_new)
    monkeypatch.setattr(toke, "_TOKE_USER_LAST", {})
    monkeypatch.setattr(toke, "_TOKE_GUILD_LAST", {})

    settings = {
        "session_minutes": 60,
        "max_interested": 100,
        "show_member_names": True,
    }
    await toke._publish_toke(
        guild=guild, member=member, channel=channel,
        starter_role=starter_role, notify_role=notify_role,
        message_text="where's the sesh", clean_media=media,
        upload=upload, settings=settings,
    )
    return channel


def test_uploaded_gif_is_full_width_inline_gallery_without_editable_embed(monkeypatch):
    class Upload:
        filename = "sesh.gif"
        content_type = "image/gif"

        async def to_file(self):
            return SimpleNamespace(filename=self.filename)

    channel = asyncio.run(_run_publish(monkeypatch, upload=Upload()))
    sent = channel.sent_payload
    assert sent is not None
    assert sent["file"].filename.startswith("toke-55378-7839-")
    assert sent["file"].filename.endswith(".gif")
    assert sent["view"].v2 is True
    assert sent["view"].filename == sent["file"].filename
    assert "## 💨 Toke Time" in sent["view"].intro
    assert "<@&555>" in sent["view"].intro
    assert "where's the sesh" in sent["view"].intro
    assert "content" not in sent and "embed" not in sent
    assert channel.edited_payload is None  # No first PATCH of embedded media.


def test_mobile_filename_is_normalized_but_gif_mime_is_preserved(monkeypatch):
    class Upload:
        filename = "mystery 🚀 photo..bin"
        content_type = "image/gif; charset=binary"
        async def to_file(self):
            return SimpleNamespace(filename=self.filename)
    channel = asyncio.run(_run_publish(monkeypatch, upload=Upload()))
    filename = channel.sent_payload["file"].filename
    assert filename.isascii()
    assert filename.startswith("toke-55378-7839-")
    assert filename.endswith(".gif")
    assert channel.sent_payload["view"].filename == filename


def test_public_tenor_gif_is_embedded_only_without_native_attachment(monkeypatch):
    url = "https://media.tenor.com/123/reaction.gif"
    channel = asyncio.run(_run_publish(monkeypatch, media=url))
    sent = channel.sent_payload
    assert "file" not in sent
    assert sent["embed"].image.url == url
    assert channel.edited_payload["embed"].image.url == url
    assert sent["content"] == "<@&555>"


def test_tenor_page_url_is_linked_inside_card_and_not_unfurled_in_content(monkeypatch):
    url = "https://tenor.com/view/reaction-gif-9867"
    channel = asyncio.run(_run_publish(monkeypatch, media=url))
    sent = channel.sent_payload
    assert sent["content"] == "<@&555>"
    assert "file" not in sent
    assert sent["embed"].image.url is None
    assert f"[Open shared GIF or image]({url})" in sent["embed"].description
    assert channel.edited_payload["embed"].description == sent["embed"].description


def test_prefers_tenor_cdn_from_native_picker_over_view_page():
    message = SimpleNamespace(
        content="https://tenor.com/view/yes-gif-9988",
        attachments=[],
        embeds=[SimpleNamespace(
            url="https://tenor.com/view/yes-gif-9988",
            image=SimpleNamespace(url="https://media.tenor.com/98765/yes.gif"),
            video=None, thumbnail=None,
        )],
    )
    assert live.media_url_from_message(message) == "https://media.tenor.com/98765/yes.gif"


def test_discord_cdn_attachment_url_is_not_mistaken_for_persistent_provider_asset():
    assert not toke._is_stable_provider_media_url(
        "https://cdn.discordapp.com/attachments/10/20/temporary.gif?ex=123"
    )
    assert not toke._is_stable_provider_media_url("https://tenor.com/view/yes-gif-9988")
    assert toke._is_stable_provider_media_url("https://media.tenor.com/123/a.gif")
    assert toke._is_stable_provider_media_url("https://i.giphy.com/gif.gif")


def test_native_tenor_video_track_does_not_replace_gif_image():
    # GIF picker can supply a moving MP4 plus a thumbnail GIF. The Toke
    # embed must use the image, never try set_image(url=video.mp4).
    message = SimpleNamespace(
        content="https://tenor.com/view/sesh-gif-8765",
        attachments=[],
        embeds=[SimpleNamespace(
            url="https://tenor.com/view/sesh-gif-8765",
            image=None,
            video=SimpleNamespace(url="https://media.tenor.com/abc/tenor.mp4"),
            thumbnail=SimpleNamespace(url="https://media.tenor.com/abc/tenor.gif"),
        )],
    )
    assert live.media_url_from_message(message) == "https://media.tenor.com/abc/tenor.gif"
    assert not toke._is_stable_provider_media_url("https://media.tenor.com/abc/tenor.mp4")
    assert not toke._is_direct_media_url("https://media.tenor.com/abc/tenor.mp4")


def test_no_suppress_embeds_flag_hides_interactive_toke_embed():
    # Discord's suppress_embeds flag also suppresses bot-authored custom
    # embeds. Do not use it as a workaround for duplicate GIF previews.
    import inspect
    source = inspect.getsource(toke._publish_toke)
    assert "suppress_embeds" not in source
    assert "suppress=True" not in source
