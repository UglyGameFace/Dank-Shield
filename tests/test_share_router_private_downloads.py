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
    assert "handle_download_message" in source
    assert "ensure_share_router_runtime" in Path("stoney_verify/share_router_runtime.py").read_text()
    assert "bot.add_listener(route_message" in Path("stoney_verify/share_router_runtime.py").read_text()
    assert "add_listener" not in Path("stoney_verify/share_router_private_downloads.py").read_text()
