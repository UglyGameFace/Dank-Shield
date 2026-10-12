from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import discord
import pytest

from stoney_verify import share_router_private_downloads as downloads
from stoney_verify import share_router_runtime as router
from stoney_verify.share_router_media_resolver import MediaResolution


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


def test_over_limit_returns_honest_private_source_link_not_fake_file(monkeypatch):
    message = FakeMessage()

    async def resolve(text, *, max_bytes):
        return _resolution(text, delivery="link")

    monkeypatch.setattr(downloads, "resolve_first_media", resolve)
    assert run(downloads.handle_download_message(message))
    assert len(message.author.sent) == 1
    content, file, _ = message.author.sent[0]
    assert file is None
    assert "couldn't attach" in content
    assert "https://cdn.example.com/movie.mp4" in content
    assert "private large-file download service" in content
    assert message.deleted


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
    assert message.deleted
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
