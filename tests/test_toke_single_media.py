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


async def _run_publish(monkeypatch, *, upload=None, media="", voice_channel_id=0, voice_access=True):
    class FakeVoice:
        id = 99880

        def permissions_for(self, _member):
            return SimpleNamespace(view_channel=voice_access, connect=voice_access)

    voice = FakeVoice()
    guild = SimpleNamespace(
        id=55378, me=_FakeMember(),
        get_channel=lambda cid: voice if cid == 99880 else None,
    )
    member = guild.me
    channel = _FakeChannel()
    monkeypatch.setattr(toke.discord, "VoiceChannel", FakeVoice)
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
        channel.saved_session = dict(data)
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
        upload=upload, settings=settings, voice_channel_id=voice_channel_id,
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


def test_tenor_page_resolves_to_inline_gif_without_link_or_extra_preview(monkeypatch):
    url = "https://tenor.com/view/reaction-gif-9867"
    media = "https://media.tenor.com/123/reaction.gif"

    async def resolve(value):
        assert value == url
        return media

    monkeypatch.setattr(toke, "_resolve_toke_media_url", resolve)
    channel = asyncio.run(_run_publish(monkeypatch, media=url))
    sent = channel.sent_payload
    assert sent["content"] == "<@&555>"
    assert "file" not in sent
    assert sent["embed"].image.url == media
    assert channel.edited_payload["embed"].image.url == media
    assert "Open shared GIF" not in sent["embed"].description


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



def test_giphy_share_page_uses_animated_media_in_embed(monkeypatch):
    link = "https://giphy.com/gifs/dancing-with-friends-aBcD1234"
    channel = asyncio.run(_run_publish(monkeypatch, media=link))
    image = "https://media.giphy.com/media/aBcD1234/giphy.gif"
    assert channel.sent_payload["embed"].image.url == image
    assert channel.edited_payload["embed"].image.url == image
    assert channel.sent_payload["content"] == "<@&555>"


def test_generic_metadata_prefers_gif_and_rejects_private_or_insecure_assets():
    html = """
    <meta name="twitter:image" content="https://preview.other-site.example/static.png">
    <meta content="https://192.168.1.5/secret.gif" property="og:image">
    <meta content="https://cdn.other-site.example/funny.gif?foo=1&amp;bar=2" property="og:image">
    """
    assert toke._provider_page_image(html) == "https://cdn.other-site.example/funny.gif?foo=1&bar=2"
    assert toke._provider_page_image('<meta property="og:image" content="http://localhost/a.gif">') == ""
    assert toke._provider_page_image('<meta property="og:image" content="https://127.0.0.1/a.gif">') == ""
    assert toke._provider_page_image('<meta property="og:image" content="https://cdn.site.example/movie.mp4">') == ""


def _fake_page(monkeypatch, *, html="", status=200, headers=None, destination=None):
    """Stub only the network boundary; production still uses public_get."""
    page = SimpleNamespace(status=status, headers=headers or {"Content-Type": "text/html"})
    chunks = [html.encode("utf-8")]
    async def read(max_bytes):
        assert 0 < max_bytes <= 16 * 1024
        body = chunks.pop(0) if chunks else b""
        if len(body) > max_bytes:
            chunks.insert(0, body[max_bytes:])
        return body[:max_bytes]
    page.content = SimpleNamespace(read=read)
    page.released = False
    def release():
        page.released = True
    page.release = release

    class Session:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *_args):
            return None

    calls = []
    async def public_get(_session, url, *, max_redirects):
        calls.append((url, max_redirects))
        return page, (destination or url)

    monkeypatch.setattr(toke, "public_tcp_connector", lambda **_kwargs: object())
    monkeypatch.setattr(toke.aiohttp, "ClientSession", lambda **_kwargs: Session())
    monkeypatch.setattr(toke, "public_get", public_get)
    return page, calls


@pytest.mark.parametrize("share_page,embedded", [
    ("https://tenor.com/view/dance-gif-12345678", "https://media.tenor.com/a/party.gif"),
    ("https://imgur.com/gallery/abcd", "https://i.imgur.com/abcd.gif"),
    ("https://www.reddit.com/r/gifs/comments/abcd/party/", "https://i.redd.it/cool.gif"),
    ("https://other-public-site.example/fun/post", "https://cdn.another.example/picture.webp"),
])
def test_any_public_image_sharing_page_can_use_metadata(monkeypatch, share_page, embedded):
    page, calls = _fake_page(
        monkeypatch,
        html=f'<meta property="og:image" content="{embedded}">',
    )
    channel = asyncio.run(_run_publish(monkeypatch, media=share_page))
    assert channel.sent_payload["embed"].image.url == embedded
    assert channel.edited_payload["embed"].image.url == embedded
    assert "file" not in channel.sent_payload
    assert "Open shared GIF or image" not in channel.sent_payload["embed"].description
    assert calls == [(share_page, 3)]
    assert page.released


def test_public_page_metadata_after_first_network_chunk_is_detected(monkeypatch):
    html = ("x" * (20 * 1024)) + (
        '<meta property="og:image" content="https://cdn.example.net/late.gif">'
    )
    _fake_page(monkeypatch, html=html)
    assert asyncio.run(toke._resolve_toke_media_url(
        "https://other-site.example/late-media"
    )) == "https://cdn.example.net/late.gif"


def test_other_hosts_are_accepted_for_stable_gifs_from_discord_composer():
    assert toke._is_stable_provider_media_url("https://i.imgur.com/a.gif")
    assert toke._is_stable_provider_media_url("https://cdn.example.net/funny.gif")
    assert toke._is_stable_provider_media_url("https://pbs.twimg.com/media/X?format=jpg")
    assert not toke._is_stable_provider_media_url("https://discord.com/channels/1/2/3")
    message = SimpleNamespace(
        content="https://other-site.example/post/123",
        attachments=[],
        embeds=[SimpleNamespace(
            image=SimpleNamespace(url="https://i.imgur.com/a.gif"),
            video=None, thumbnail=None, url="https://other-site.example/post/123",
        )],
    )
    assert live.media_url_from_message(message) == "https://i.imgur.com/a.gif"


def test_public_extensionless_image_response_is_supported(monkeypatch):
    final = "https://media.other-site.example/render/12345"
    page, calls = _fake_page(
        monkeypatch, headers={"Content-Type": "image/gif"}, destination=final,
    )
    assert asyncio.run(toke._resolve_toke_media_url("https://short.other-site.example/cool")) == final
    assert calls and page.released


@pytest.mark.parametrize("bad_url", [
    "https://localhost/view",
    "https://127.0.0.1/a",
    "https://10.0.0.2/a",
    "https://tenor.com:8080/view/blocked",
    "ftp://example.net/a",
    "https://user:password@example.net/share",
])
def test_share_page_public_network_guard_rejects_unsafe_links_without_fetch(monkeypatch, bad_url):
    async def unexpected(*_a, **_kw):
        raise AssertionError("unsafe URL was fetched")
    monkeypatch.setattr(toke, "public_get", unexpected)
    with pytest.raises(ValueError, match="public HTTPS|valid public HTTPS"):
        asyncio.run(toke._resolve_toke_media_url(bad_url))


def test_unresolvable_other_provider_page_does_not_publish_link_only_card(monkeypatch):
    page, calls = _fake_page(
        monkeypatch, html="<html><body>No media here.</body></html>",
    )
    with pytest.raises(ValueError, match="does not expose a public inline"):
        asyncio.run(_run_publish(monkeypatch, media="https://example.net/no-media"))
    assert calls and page.released


@pytest.mark.parametrize("status,headers,html,error", [
    (403, {"Content-Type": "text/html"}, "", "unavailable"),
    (200, {"Content-Type": "text/plain"}, "", "did not return"),
    (200, {"Content-Type": "text/html", "Content-Length": "999999"}, "", "too large"),
    (200, {"Content-Type": "text/html"}, "a" * (256 * 1024 + 1), "too large"),
])
def test_bad_provider_responses_do_not_post_broken_card(
    monkeypatch, status, headers, html, error,
):
    page, calls = _fake_page(monkeypatch, status=status, headers=headers, html=html)
    with pytest.raises(ValueError, match=error):
        asyncio.run(_run_publish(monkeypatch, media="https://new-site.example/cool-gif"))
    assert calls and page.released


def test_composer_rejects_discord_draft_cdn_when_no_upload_is_available(monkeypatch):
    class Role:
        def __init__(self, role_id):
            self.id = role_id
            self.mention = f"<@&{role_id}>"

    guild = SimpleNamespace(
        id=55378,
        get_role=lambda rid: Role(rid),
        get_channel=lambda _rid: _FakeChannel(),
    )
    draft = SimpleNamespace(guild_id=55378, destination_id=99981, note="")
    media = SimpleNamespace(
        content="", attachments=[],
        embeds=[SimpleNamespace(
            image=SimpleNamespace(url="https://cdn.discordapp.com/attachments/1/2/temp.gif?ex=123"),
            video=None, thumbnail=None, url=None,
        )],
    )
    async def cfg(_guild):
        return {}
    monkeypatch.setattr(toke.discord, "Member", _FakeMember)
    monkeypatch.setattr(toke.discord, "Role", Role)
    monkeypatch.setattr(toke.discord, "TextChannel", _FakeChannel)
    monkeypatch.setattr(toke, "_config", cfg)
    monkeypatch.setattr(toke, "toke_role_ids", lambda *_args: (776, 555))
    monkeypatch.setattr(toke, "_member_has_role_id", lambda *_args: True)
    with pytest.raises(ValueError, match="temporary media preview"):
        asyncio.run(toke.publish_toke_from_draft(guild, _FakeMember(), draft, media))
    assert toke._is_temporary_discord_media_url(
        "https://images-ext-6.discordapp.net/external/a/b.gif"
    )


def test_no_suppress_embeds_flag_hides_interactive_toke_embed():
    # Discord's suppress_embeds flag also suppresses bot-authored custom
    # embeds. Do not use it as a workaround for duplicate GIF previews.
    import inspect
    source = inspect.getsource(toke._publish_toke)
    assert "suppress_embeds" not in source
    assert "suppress=True" not in source


def test_session_specific_voice_requires_member_and_bot_access(monkeypatch):
    with pytest.raises(ValueError, match="Both you and Dank Shield"):
        asyncio.run(_run_publish(monkeypatch, voice_channel_id=99880, voice_access=False))
    channel = asyncio.run(_run_publish(monkeypatch, voice_channel_id=99880))
    assert channel.saved_session["voice_channel_id"] == 99880
    assert channel.sent_payload is not None


def test_deleted_session_voice_does_not_publish_toke_card(monkeypatch):
    with pytest.raises(ValueError, match="no longer exists"):
        asyncio.run(_run_publish(monkeypatch, voice_channel_id=12345678))
