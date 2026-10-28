from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import discord
import pytest

from stoney_verify import share_router_private_downloads as downloads
from stoney_verify import share_router_runtime as router
from stoney_verify.share_router_media_resolver import MediaResolution

ORIGINAL_GUILD_DOWNLOAD_DELIVERY = router.guild_download_delivery


def run(coro):
    return asyncio.run(coro)


class FakeMember:
    def __init__(self, member_id=22, *, dm_blocked=False):
        self.id = member_id
        self.bot = False
        self.sent = []
        self.dm_blocked = dm_blocked

    async def send(self, content, *, allowed_mentions, file=None):
        if self.dm_blocked:
            raise discord.Forbidden(
                SimpleNamespace(status=403, reason="Forbidden"),
                {"code": 50007, "message": "Cannot send messages to this user"},
            )
        self.sent.append((content, file, allowed_mentions))


class FakeChannel:
    def __init__(self, channel_id=77):
        self.id = channel_id
        self.problems = []
        self.allow_manage_messages = True

    def permissions_for(self, member):
        return SimpleNamespace(
            view_channel=True,
            send_messages=True,
            manage_messages=self.allow_manage_messages,
        )

    def is_nsfw(self):
        return False


class FakeMessage:
    def __init__(self, *, guild_id=111, channel=None, member=None, content="https://cdn.example.com/movie.mp4"):
        self.guild = SimpleNamespace(id=guild_id, me=SimpleNamespace(id=999))
        self.channel = channel or FakeChannel()
        self.author = member or FakeMember()
        self.content = content
        self.webhook_id = None
        self.deleted = False

    async def delete(self):
        self.deleted = True

    async def reply(self, content, *, mention_author, allowed_mentions):
        self.channel.problems.append((content, mention_author, allowed_mentions))


@pytest.fixture(autouse=True)
def reset_state(monkeypatch):
    monkeypatch.setattr(downloads.discord, "Member", FakeMember)
    async def legacy_download_mode(_guild_id):
        return "dm", 0
    monkeypatch.setattr(router, "guild_download_delivery", legacy_download_mode)
    downloads.reset_download_state_for_tests()
    yield
    downloads.reset_download_state_for_tests()


def _resolution(source_url, *, delivery="progressive") -> MediaResolution:
    return MediaResolution(
        source_url=source_url,
        canonical_url=source_url,
        identity="direct",
        provider="direct",
        delivery=delivery,
        media_url=source_url if delivery == "progressive" else "",
    )


def test_configured_download_channel_is_guild_scoped_and_opt_in(tmp_path, monkeypatch):
    monkeypatch.setattr(router, "ROUTES_FILE", tmp_path / "routes.json")

    async def scenario():
        assert await router.guild_download_channel_id(111) == 0
        await router.save_guild_download_channel(111, 77)
        assert await router.guild_download_channel_id(111) == 77
        assert await router.guild_download_channel_id(222) == 0
        await router.save_guild_download_channel(111, 0)
        assert await router.guild_download_channel_id(111) == 0

    run(scenario())


def test_download_channel_does_not_change_existing_route_rows(tmp_path, monkeypatch):
    monkeypatch.setattr(router, "ROUTES_FILE", tmp_path / "routes.json")

    async def scenario():
        await router.save_route(111, source_channel_id=88, target_channel_id=99)
        before = await router.guild_routes(111)
        await router.save_guild_download_channel(111, 77)
        assert await router.guild_routes(111) == before
        assert await router.guild_download_channel_id(111) == 77

    run(scenario())


def test_bounded_download_success_dms_video_and_deletes_source(monkeypatch):
    message = FakeMessage()
    calls = []

    async def resolve(text, *, max_bytes):
        assert max_bytes == downloads.DM_ATTACHMENT_BYTES
        return _resolution(text)

    class File:
        closed = False

        def close(self):
            self.closed = True

    file = File()

    async def download(url, *, max_bytes, request_headers):
        calls.append((url, max_bytes))
        return SimpleNamespace(file=file, source_url=url, size_bytes=100, cleanup_path=None)

    monkeypatch.setattr(downloads, "resolve_first_media", resolve)
    monkeypatch.setattr(router, "_download_trusted_video", download)
    assert run(downloads.handle_download_message(message))
    assert calls
    assert len(message.author.sent) == 1
    assert message.author.sent[0][1] is file
    assert message.author.sent[0][2].everyone is False
    assert file.closed
    assert message.deleted



def test_facebook_embed_download_dms_without_broken_page_extractor(monkeypatch):
    facebook = "https://www.facebook.com/reel/1591316522498163"
    cdn = "https://video.xx.fbcdn.net/media/reel.mp4?oh=private"
    message = FakeMessage(content=facebook)
    message.embeds = [SimpleNamespace(video=SimpleNamespace(url=cdn, proxy_url=None))]
    message.attachments = [SimpleNamespace(
        content_type="video/mp4", filename="unrelated.mp4",
        url="https://cdn.discordapp.com/attachments/unrelated.mp4", proxy_url=None,
    )]
    class File:
        closed = False
        def close(self):
            self.closed = True
    file = File()
    calls = []

    async def fetch(url, *, max_bytes, request_headers=None):
        calls.append((url, max_bytes))
        return SimpleNamespace(file=file, size_bytes=1234, source_url=url, cleanup_path=None)

    async def no_extraction(*args, **kwargs):
        raise AssertionError("Trusted embedded Facebook media should avoid the broken extractor")

    monkeypatch.setattr(router, "_download_trusted_video", fetch)
    monkeypatch.setattr(downloads, "resolve_first_media", no_extraction)
    assert run(downloads.handle_download_message(message))
    assert calls == [(cdn, downloads.DM_ATTACHMENT_BYTES)]
    assert message.author.sent[0][1] is file
    assert message.author.sent[0][2].everyone is False
    assert file.closed and message.deleted


def test_facebook_failed_embed_uses_existing_progressive_fallback(monkeypatch):
    facebook = "https://www.facebook.com/watch/?v=1591316522498163"
    cdn = "https://video.xx.fbcdn.net/fail.mp4"
    playable = "https://video.xx.fbcdn.net/working.mp4"
    message = FakeMessage(content=facebook)
    message.embeds = [SimpleNamespace(video=SimpleNamespace(url=cdn, proxy_url=None))]
    attempts = []
    class File:
        closed = False
        def close(self):
            self.closed = True
    file = File()

    async def fetch(url, *, max_bytes, request_headers=None):
        attempts.append(url)
        if url == playable:
            return SimpleNamespace(file=file, size_bytes=500, source_url=url, cleanup_path=None)
        return None

    async def resolve(url, *, max_bytes):
        assert url == facebook
        return MediaResolution(
            source_url=url, canonical_url=url, identity="facebook:1591316522498163",
            provider="facebook", delivery="progressive", media_url=playable,
        )

    monkeypatch.setattr(router, "_download_trusted_video", fetch)
    monkeypatch.setattr(downloads, "resolve_first_media", resolve)
    assert run(downloads.handle_download_message(message))
    assert attempts == [cdn, playable]
    assert file.closed and message.deleted
    assert message.author.sent[0][1] is file


def test_facebook_download_ignores_unrelated_attachment_and_preserves_failure(monkeypatch):
    facebook = "https://www.facebook.com/reel/1591316522498163"
    message = FakeMessage(content=facebook)
    message.embeds = []
    message.attachments = [SimpleNamespace(
        content_type="video/mp4", filename="unrelated.mp4",
        url="https://cdn.discordapp.com/attachments/unrelated.mp4", proxy_url=None,
    )]

    async def no_fetch(*args, **kwargs):
        raise AssertionError("An unrelated attachment must not become the Facebook video")

    async def failed_extract(url, *, max_bytes):
        return MediaResolution(
            source_url=url, canonical_url=url, identity="facebook:1591316522498163",
            provider="facebook", delivery="link", reason="provider_extractor_incompatible",
        )

    monkeypatch.setattr(router, "_download_trusted_video", no_fetch)
    monkeypatch.setattr(downloads, "resolve_first_media", failed_extract)
    assert run(downloads.handle_download_message(message))
    assert not message.deleted
    assert "provider_extractor_incompatible" in message.author.sent[0][0]
    assert message.author.sent[0][1] is None


def test_nonfacebook_download_does_not_use_facebook_embed_path(monkeypatch):
    youtube = "https://www.youtube.com/watch?v=tiq6mOEwsow"
    message = FakeMessage(content=youtube)
    message.embeds = [SimpleNamespace(video=SimpleNamespace(
        url="https://video.xx.fbcdn.net/should-not-be-tried.mp4", proxy_url=None,
    ))]

    async def no_fetch(*args, **kwargs):
        raise AssertionError("Facebook fast path must not run for another provider")

    async def failed_extract(url, *, max_bytes):
        return MediaResolution(
            source_url=url, canonical_url=url, identity="youtube:tiq6mOEwsow",
            provider="youtube", delivery="link", reason="provider_bot_verification",
        )

    monkeypatch.setattr(router, "_download_trusted_video", no_fetch)
    monkeypatch.setattr(downloads, "resolve_first_media", failed_extract)
    assert run(downloads.handle_download_message(message))
    assert not message.deleted
    assert "provider_bot_verification" in message.author.sent[0][0]



def test_provider_title_shows_above_private_dm_attachment(monkeypatch):
    message = FakeMessage(content="https://vimeo.com/123456")

    async def resolve(url, *, max_bytes):
        return MediaResolution(
            source_url=url, canonical_url=url, identity="vimeo:123456",
            provider="vimeo", delivery="progressive",
            media_url="https://cdn.example.com/clip.mp4",
            title="Great Goalie Saves",
        )

    class File:
        filename = "video.mp4"
        closed = False
        def close(self):
            self.closed = True

    file = File()

    async def download(url, *, max_bytes, request_headers=None):
        return SimpleNamespace(
            file=file, source_url=url, size_bytes=100, cleanup_path=None,
        )

    monkeypatch.setattr(downloads, "resolve_first_media", resolve)
    monkeypatch.setattr(router, "_download_trusted_video", download)
    assert run(downloads.handle_download_message(message))
    content, attachment, mentions = message.author.sent[0]
    assert content.startswith("🎬 **Great Goalie Saves**\n")
    assert attachment is file
    assert mentions.everyone is False
    assert file.closed and message.deleted


def test_facebook_embed_title_survives_private_download_without_extractor(monkeypatch):
    fb = "https://www.facebook.com/reel/123456789"
    video_cdn = "https://video.xx.fbcdn.net/v/actual.mp4"
    message = FakeMessage(content=fb)
    message.embeds = [SimpleNamespace(
        url=fb, title="Funny Cat Reel",
        video=SimpleNamespace(url=video_cdn, proxy_url=None),
    )]

    class File:
        filename = "video.mp4"
        def close(self):
            pass

    async def guarded_download(url, *, max_bytes, request_headers=None):
        assert url == video_cdn
        return SimpleNamespace(
            file=File(), source_url=url, size_bytes=100, cleanup_path=None,
        )

    async def no_extract(*_args, **_kwargs):
        raise AssertionError("Facebook embed should avoid extra extraction")

    monkeypatch.setattr(router, "_download_trusted_video", guarded_download)
    monkeypatch.setattr(downloads, "resolve_first_media", no_extract)
    assert run(downloads.handle_download_message(message))
    assert message.author.sent[0][0].startswith("🎬 **Funny Cat Reel**\n")
    assert message.deleted


def test_provider_title_shows_on_hosted_dm_link(monkeypatch):
    import io
    message = FakeMessage(content="https://www.facebook.com/watch/?v=100")

    async def resolve(url, *, max_bytes):
        return MediaResolution(
            source_url=url, canonical_url=url, identity="fb:100",
            provider="facebook", delivery="progressive",
            media_url="https://video.xx.fbcdn.net/real.mp4", title="Live Concert Clip",
        )

    class File:
        filename = "download.mp4"
        fp = io.BytesIO(b"pretend video")
        def close(self):
            pass

    async def download(url, *, max_bytes, request_headers=None):
        return SimpleNamespace(
            file=File(), source_url=url, size_bytes=downloads.DM_ATTACHMENT_BYTES + 1,
            cleanup_path=None,
        )

    async def stage(fp, *, ttl_seconds, ext):
        return ("https://host.example/watch", "https://host.example/download", "asset-a")

    async def expiry(_guild_id):
        return 3600

    monkeypatch.setattr(downloads, "delivery_status", lambda: (True, "ready"))
    monkeypatch.setattr(downloads, "file_max_bytes", lambda: 128 * 1024 * 1024)
    monkeypatch.setattr(downloads, "resolve_first_media", resolve)
    monkeypatch.setattr(router, "_download_trusted_video", download)
    monkeypatch.setattr(router, "guild_download_expiry_seconds", expiry)
    monkeypatch.setattr(downloads, "stage_video", stage)
    assert run(downloads.handle_download_message(message))
    sent, attachment, mentions = message.author.sent[0]
    assert sent.startswith("🎬 **Live Concert Clip**\n")
    assert "▶️ Watch:" in sent and "📥 Download:" in sent
    assert attachment is None and not mentions.everyone
    assert message.deleted


def test_over_limit_returns_honest_private_source_link_not_fake_file(monkeypatch):
    message = FakeMessage()

    async def resolve(text, *, max_bytes):
        return _resolution(text, delivery="link")

    monkeypatch.setattr(downloads, "resolve_first_media", resolve)
    assert run(downloads.handle_download_message(message))
    assert len(message.author.sent) == 1
    content, file, _ = message.author.sent[0]
    assert file is None
    assert "could not prepare a video file" in content
    assert "https://cdn.example.com/movie.mp4" in content
    assert "Large-file hosting is also unavailable" in content
    assert "extract_failed" in content
    assert "size/format limits" not in content
    assert not message.deleted  # Error-only DM must preserve the original source.


def test_youtube_extraction_failure_is_reported_without_false_dm_limit_claim(monkeypatch):
    from stoney_verify.share_router_media_resolver import MediaResolution

    message = FakeMessage(content="https://www.youtube.com/watch?v=tiq6mOEwsow")
    async def resolve(text, *, max_bytes):
        return MediaResolution(
            source_url=text, canonical_url=text, identity="youtube:tiq6mOEwsow",
            provider="youtube", delivery="link", reason="provider_bot_verification",
        )

    monkeypatch.setattr(downloads, "delivery_status", lambda: (False, "Public HTTPS media address is not configured"))
    monkeypatch.setattr(downloads, "resolve_first_media", resolve)

    assert run(downloads.handle_download_message(message))
    assert len(message.author.sent) == 1
    content, file, mentions = message.author.sent[0]
    assert file is None
    assert "bot verification" in content
    assert "provider_bot_verification" in content
    assert "Large-file hosting is also unavailable" in content
    assert "size/format limits" not in content
    assert "isn't configured yet" not in content
    assert "No video was downloaded or hosted" in content
    assert mentions.everyone is False
    assert not message.deleted


def test_host_ready_but_video_fetch_failed_is_reported_as_transfer_failure(monkeypatch):
    message = FakeMessage()

    async def resolve(text, *, max_bytes):
        return _resolution(text)

    async def download(url, *, max_bytes, request_headers):
        return None

    monkeypatch.setattr(downloads, "delivery_status", lambda: (True, "Large-file links available"))
    monkeypatch.setattr(downloads, "resolve_first_media", resolve)
    monkeypatch.setattr(router, "_download_trusted_video", download)
    assert run(downloads.handle_download_message(message))
    assert len(message.author.sent) == 1
    body, file, mentions = message.author.sent[0]
    assert file is None
    assert "media_transfer_failed" in body
    assert "Large-file hosting is ready locally" in body
    assert "isn't configured yet" not in body
    assert "Discord's DM size/format limits" not in body
    assert mentions.everyone is False
    assert not message.deleted


def test_failure_messages_never_contain_raw_extractor_errors_or_access_tokens():
    from stoney_verify.share_router_media_resolver import MediaResolution
    result = MediaResolution(
        source_url="https://youtu.be/demo",
        canonical_url="https://www.youtube.com/watch?v=demo",
        identity="youtube:demo", provider="youtube", delivery="link",
        reason="provider_bot_verification",
    )
    body = downloads.download_failure_message(
        result, reason="unknown provider https://cdn.example.com?token=secret",
        hosting_ready=True, hosting_reason="",
    )
    assert "cdn.example.com" not in body
    assert "token=secret" not in body
    assert "extract_failed" in body


def test_discord_413_upload_error_falls_back_to_honest_dm_link(monkeypatch):
    class LargeFile:
        closed = False

        def close(self):
            self.closed = True

    class UploadRejectMember(FakeMember):
        async def send(self, content, *, allowed_mentions, file=None):
            if file is not None:
                raise discord.HTTPException(
                    SimpleNamespace(status=413, reason="Request Entity Too Large"),
                    {"code": 40005, "message": "Request entity too large"},
                )
            self.sent.append((content, file, allowed_mentions))

    message = FakeMessage(member=UploadRejectMember())
    f = LargeFile()

    async def resolve(text, *, max_bytes):
        return _resolution(text)

    async def download(url, *, max_bytes, request_headers):
        return SimpleNamespace(file=f, source_url=url, size_bytes=100, cleanup_path=None)

    monkeypatch.setattr(downloads, "resolve_first_media", resolve)
    monkeypatch.setattr(router, "_download_trusted_video", download)
    assert run(downloads.handle_download_message(message))
    assert f.closed
    assert not message.deleted
    assert len(message.author.sent) == 1
    content, attachment, mentions = message.author.sent[0]
    assert attachment is None
    assert "Discord rejected this video attachment" in content
    assert "not a downloaded file" in content
    assert mentions.everyone is False


def test_above_dm_cap_stages_private_links_and_reports_ttl(monkeypatch, tmp_path):
    import io
    message = FakeMessage()
    called = []

    async def resolve(text, *, max_bytes):
        assert max_bytes == 128 * 1024 * 1024
        return _resolution(text)

    class LargeFile:
        filename = "download.mp4"
        fp = io.BytesIO(b"pretend-large-file")
        closed = False

        def close(self):
            self.closed = True

    video_file = LargeFile()

    async def download(url, *, max_bytes, request_headers):
        return SimpleNamespace(
            file=video_file, source_url=url,
            size_bytes=downloads.DM_ATTACHMENT_BYTES + 1, cleanup_path=None,
        )

    async def expiry(guild_id):
        assert guild_id == 111
        return 7 * 86400

    async def stage(fp, *, ttl_seconds, ext):
        called.append((fp, ttl_seconds, ext))
        return (
            "https://files.example.com/share-video/secret/watch",
            "https://files.example.com/share-video/secret/download",
            "assetone",
        )

    monkeypatch.setattr(downloads, "delivery_status", lambda: (True, "ready"))
    monkeypatch.setattr(downloads, "file_max_bytes", lambda: 128 * 1024 * 1024)
    monkeypatch.setattr(downloads, "resolve_first_media", resolve)
    monkeypatch.setattr(router, "_download_trusted_video", download)
    monkeypatch.setattr(router, "guild_download_expiry_seconds", expiry)
    monkeypatch.setattr(downloads, "stage_video", stage)
    assert run(downloads.handle_download_message(message))
    assert message.deleted
    assert video_file.closed
    assert called == [(video_file.fp, 7 * 86400, ".mp4")]
    assert len(message.author.sent) == 1
    delivered, attachment, mentions = message.author.sent[0]
    assert attachment is None
    assert "▶️ Watch" in delivered
    assert "📥 Download" in delivered
    assert "expire in 7 day(s)" in delivered
    assert mentions.everyone is False


def test_staged_asset_revoked_when_dm_fails(monkeypatch):
    import io
    message = FakeMessage(member=FakeMember(dm_blocked=True))
    deleted = []

    async def resolve(text, *, max_bytes):
        return _resolution(text)

    file = SimpleNamespace(
        filename="clip.mp4",
        fp=io.BytesIO(b"file"), close=lambda: None,
    )

    async def download(url, *, max_bytes, request_headers):
        return SimpleNamespace(
            file=file, source_url=url,
            size_bytes=downloads.DM_ATTACHMENT_BYTES + 1, cleanup_path=None,
        )

    async def stage(fp, *, ttl_seconds, ext):
        return ("https://host/watch", "https://host/download", "abc123")

    async def revoke(ident):
        deleted.append(ident)

    monkeypatch.setattr(downloads, "delivery_status", lambda: (True, "ready"))
    monkeypatch.setattr(downloads, "file_max_bytes", lambda: 128 * 1024 * 1024)
    monkeypatch.setattr(downloads, "resolve_first_media", resolve)
    monkeypatch.setattr(router, "_download_trusted_video", download)
    monkeypatch.setattr(downloads, "stage_video", stage)
    monkeypatch.setattr(downloads, "revoke_video", revoke)
    assert run(downloads.handle_download_message(message))
    assert deleted == ["abc123"]
    assert not message.deleted


def test_per_guild_expiration_configuration_preserves_routes(monkeypatch, tmp_path):
    monkeypatch.setattr(router, "ROUTES_FILE", tmp_path / "routes.json")

    async def scenario():
        await router.save_route(11, source_channel_id=12, target_channel_id=13)
        before = await router.guild_routes(11)
        assert await router.guild_download_expiry_seconds(11) == 604800
        await router.save_guild_download_expiry_seconds(11, 3600)
        assert await router.guild_download_expiry_seconds(11) == 3600
        assert await router.guild_download_expiry_seconds(22) == 604800
        assert await router.guild_routes(11) == before
        with pytest.raises(ValueError):
            await router.save_guild_download_expiry_seconds(11, 31 * 86400)
        assert await router.guild_download_expiry_seconds(11) == 3600

    run(scenario())


def test_dm_disabled_never_erases_source_and_notifies_without_ping(monkeypatch):
    message = FakeMessage(member=FakeMember(dm_blocked=True))

    async def resolve(text, *, max_bytes):
        return _resolution(text, delivery="link")

    monkeypatch.setattr(downloads, "resolve_first_media", resolve)
    assert run(downloads.handle_download_message(message))
    assert not message.deleted
    assert not message.author.sent
    assert len(message.channel.problems) == 1
    content, mentions_author, mentions = message.channel.problems[0]
    assert "couldn't DM" in content
    assert mentions_author is False
    assert mentions.everyone is False


def test_repeated_request_is_cooled_down_after_success(monkeypatch):
    member = FakeMember()
    message_one = FakeMessage(member=member)
    message_two = FakeMessage(member=member)

    async def resolve(text, *, max_bytes):
        return _resolution(text, delivery="link")

    monkeypatch.setattr(downloads, "resolve_first_media", resolve)
    async def scenario():
        assert await downloads.handle_download_message(message_one)
        assert await downloads.handle_download_message(message_two)

    run(scenario())
    assert len(member.sent) == 1
    assert not message_two.deleted
    assert not message_two.channel.problems  # repeated requests do not spam the channel


def test_global_download_capacity_rejects_waiters_without_unbounded_queue(monkeypatch):
    active = asyncio.Event()
    release = asyncio.Event()
    count = []

    async def resolve(text, *, max_bytes):
        count.append(text)
        active.set()
        await release.wait()
        return _resolution(text, delivery="link")

    monkeypatch.setattr(downloads, "resolve_first_media", resolve)

    async def scenario():
        messages = [FakeMessage(member=FakeMember(i)) for i in (22, 23, 24)]
        first = asyncio.create_task(downloads.handle_download_message(messages[0]))
        await active.wait()
        second = asyncio.create_task(downloads.handle_download_message(messages[1]))
        # Yield until the second has started its resolver. Both available
        # transfer slots are now reserved and a third request must fail fast.
        for _ in range(30):
            if len(count) >= 2:
                break
            await asyncio.sleep(0)
        assert len(count) == 2
        assert await downloads.handle_download_message(messages[2])
        assert len(count) == 2
        assert not messages[2].author.sent
        assert "capacity is busy" in messages[2].channel.problems[0][0]
        release.set()
        await asyncio.gather(first, second)

    run(scenario())


def test_invalid_private_and_multiple_urls_are_not_downloaded(monkeypatch):
    urls = (
        "http://127.0.0.1/private.mp4",
        "http://169.254.169.254/latest/meta-data",
        "https://site.example/movie.mp4 https://other.example/movie.mp4",
        "file:///tmp/movie.mp4",
    )
    async def forbidden_resolver(*args, **kwargs):
        pytest.fail("invalid URL must not be resolved")

    monkeypatch.setattr(downloads, "resolve_first_media", forbidden_resolver)
    for url in urls:
        message = FakeMessage(content=url)
        assert run(downloads.handle_download_message(message))
        assert not message.deleted
        assert not message.author.sent


def test_downloads_use_existing_router_listener_not_second_listener() -> None:
    from inspect import getsourcelines
    source = "".join(getsourcelines(router.route_message)[0])
    assert "guild_route_state" in source
    assert "enqueue_download_request" in source
    assert "handle_download_message" in Path("stoney_verify/share_router_download_queue.py").read_text()
    assert "ensure_share_router_runtime" in Path("stoney_verify/share_router_runtime.py").read_text()
    assert "bot.add_listener(route_message" in Path("stoney_verify/share_router_runtime.py").read_text()
    assert "add_listener" not in Path("stoney_verify/share_router_private_downloads.py").read_text()



def test_owner_delivery_modes_are_guild_scoped_preserve_legacy_and_routes(tmp_path, monkeypatch):
    monkeypatch.setattr(router, "guild_download_delivery", ORIGINAL_GUILD_DOWNLOAD_DELIVERY)
    monkeypatch.setattr(router, "ROUTES_FILE", tmp_path / "routes.json")

    async def check():
        assert await router.guild_download_delivery(111) == ("off", 0)
        await router.save_route(111, source_channel_id=88, target_channel_id=99)
        routes = await router.guild_routes(111)
        # Existing channels with no new setting must keep the prior DM behavior.
        await router.save_guild_download_channel(111, 77)
        assert await router.guild_download_delivery(111) == ("dm", 0)
        await router.configure_guild_download(
            111, channel_id=77, mode="channel", destination_id=123,
        )
        assert await router.guild_download_delivery(111) == ("channel", 123)
        assert await router.guild_download_delivery(222) == ("off", 0)
        await router.configure_guild_download(
            111, channel_id=77, mode="both", destination_id=123,
        )
        assert await router.guild_download_delivery(111) == ("both", 123)
        await router.configure_guild_download(111, channel_id=77, mode="off")
        assert await router.guild_download_channel_id(111) == 77
        assert await router.guild_download_delivery(111) == ("off", 0)
        assert await router.guild_routes(111) == routes
        with pytest.raises(ValueError):
            await router.configure_guild_download(
                111, channel_id=77, mode="channel", destination_id=77,
            )
        with pytest.raises(ValueError):
            await router.configure_guild_download(
                111, channel_id=77, mode="both",
            )
        assert await router.guild_download_delivery(111) == ("off", 0)

    run(check())
    # Corrupted/missing explicit delivery settings must never re-enable DMs.
    import json
    data = json.loads(router.ROUTES_FILE.read_text())
    data["111"]["download_delivery_mode"] = "unknown"
    router.ROUTES_FILE.write_text(json.dumps(data))
    assert run(router.guild_download_delivery(111)) == ("off", 0)


def _outbound_delivery_fixture(monkeypatch, *, mode="channel", dm_blocked=False,
                               destination_permitted=True):
    import io

    class OutboundChannel(FakeChannel):
        name = "video-results"
        category = None
        guild = SimpleNamespace(id=111)

        def __init__(self):
            super().__init__(99)
            self.sent = []

        def permissions_for(self, member):
            return SimpleNamespace(
                view_channel=True, send_messages=True,
                attach_files=destination_permitted, manage_messages=True,
            )

        async def send(self, content, *, allowed_mentions, file=None):
            self.sent.append((content, file, allowed_mentions))

    destination = OutboundChannel()
    message = FakeMessage(member=FakeMember(dm_blocked=dm_blocked))
    message.guild.get_channel = lambda cid: destination if cid == 99 else None
    monkeypatch.setattr(downloads.discord, "TextChannel", FakeChannel)

    async def configured(_guild_id):
        return mode, 99

    monkeypatch.setattr(router, "guild_download_delivery", configured)
    async def resolve(url, *, max_bytes):
        return _resolution(url)

    class File:
        filename = "video.mp4"
        fp = io.BytesIO(b"safe example video bytes")
        closed = False
        resets = 0

        def reset(self, *, seek):
            assert seek
            self.resets += 1
            self.fp.seek(0)

        def close(self):
            self.closed = True

    file = File()

    async def download(url, *, max_bytes, request_headers):
        return SimpleNamespace(
            file=file, source_url=url, size_bytes=100, cleanup_path=None,
        )

    monkeypatch.setattr(downloads, "resolve_first_media", resolve)
    monkeypatch.setattr(router, "_download_resolved_progressive", download)
    return message, destination, file


def test_owner_can_select_server_channel_without_any_dm(monkeypatch):
    message, destination, file = _outbound_delivery_fixture(monkeypatch, mode="channel")
    assert run(downloads.handle_download_message(message))
    assert len(destination.sent) == 1
    assert destination.sent[0][1] is file
    assert destination.sent[0][2].everyone is False
    assert message.author.sent == []
    assert message.deleted
    assert file.closed


def test_owner_can_enable_both_with_explicit_consent(monkeypatch):
    message, destination, file = _outbound_delivery_fixture(monkeypatch, mode="both")
    assert run(downloads.handle_download_message(message))
    assert len(message.author.sent) == 1
    assert len(destination.sent) == 1
    assert file.resets == 1
    assert message.deleted


def test_partial_both_delivery_keeps_original(monkeypatch):
    message, destination, file = _outbound_delivery_fixture(
        monkeypatch, mode="both", dm_blocked=True,
    )
    assert run(downloads.handle_download_message(message))
    assert not message.author.sent
    assert len(destination.sent) == 1
    assert not message.deleted
    assert any("only some" in note[0] for note in message.channel.problems)


def test_destination_permission_revocation_blocks_delivery(monkeypatch):
    message, destination, _file = _outbound_delivery_fixture(
        monkeypatch, mode="channel", destination_permitted=False,
    )
    assert run(downloads.handle_download_message(message))
    assert not message.deleted
    assert not destination.sent
    assert not message.author.sent
    assert "permissions changed" in message.channel.problems[0][0]


def test_paused_downloads_never_contact_media_or_member(monkeypatch):
    message = FakeMessage()

    async def disabled(_guild_id):
        return "off", 0

    async def unexpected(*_args, **_kwargs):
        raise AssertionError("Paused mode must not attempt media retrieval")

    monkeypatch.setattr(router, "guild_download_delivery", disabled)
    monkeypatch.setattr(downloads, "resolve_first_media", unexpected)
    assert run(downloads.handle_download_message(message))
    assert message.author.sent == []
    assert not message.deleted


def test_pause_during_inflight_download_prevents_old_destination_delivery(monkeypatch):
    message, destination, file = _outbound_delivery_fixture(monkeypatch, mode="channel")
    mode = {"value": "channel"}

    async def current(_guild_id):
        return (mode["value"], 99) if mode["value"] == "channel" else ("off", 0)

    async def download(url, *, max_bytes, request_headers):
        # Simulate owner pressing Pause while the large extraction was running.
        mode["value"] = "off"
        return SimpleNamespace(file=file, source_url=url, size_bytes=100, cleanup_path=None)

    monkeypatch.setattr(router, "guild_download_delivery", current)
    monkeypatch.setattr(router, "_download_resolved_progressive", download)
    assert run(downloads.handle_download_message(message))
    assert message.author.sent == []
    assert destination.sent == []
    assert not message.deleted
    assert file.closed
    assert any("settings changed" in note[0] for note in message.channel.problems)
