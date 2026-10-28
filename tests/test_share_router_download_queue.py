from __future__ import annotations

import asyncio
import sqlite3
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from stoney_verify import share_router_download_queue as queue
from stoney_verify import share_router_runtime as runtime


@pytest.fixture(autouse=True)
def isolated_queue(monkeypatch, tmp_path):
    monkeypatch.setenv("DANK_SHARE_DOWNLOAD_QUEUE_DB", str(tmp_path / "private" / "queue.sqlite3"))
    monkeypatch.setattr(queue, "_LAST_GUILD_ID", "")
    async def legacy_delivery(_guild_id):
        return "dm", 0
    monkeypatch.setattr(runtime, "guild_download_delivery", legacy_delivery)
    yield
    for task in queue._WORKERS:
        if not task.done():
            task.cancel()
    queue._WORKERS = []
    queue._WORKER_BOT = None


def test_queue_records_ids_only_and_survives_new_connection():
    assert queue._submit(101, 201, 301, 401) == "queued"
    assert queue._submit(102, 202, 302, 402) == "queued"
    assert queue._snapshot()["queued"] == 2
    db = queue._db_path()
    assert db.exists()
    sql = sqlite3.connect(db)
    try:
        cols = [row[1] for row in sql.execute("PRAGMA table_info(download_queue)")]
        assert "url" not in cols and "content" not in cols
        assert "guild_id" in cols and "message_id" in cols
    finally:
        sql.close()
    # All state survives re-opening; in-memory process state is irrelevant.
    assert queue._snapshot()["queued"] == 2


def test_fair_claim_rotates_across_guilds_and_per_guild_inflight():
    for guild_id, member_id, message_id in (
        (1, 11, 101), (1, 12, 102), (2, 21, 201), (3, 31, 301),
    ):
        assert queue._submit(guild_id, guild_id + 1000, member_id, message_id) == "queued"
    first = queue._claim_next("")
    assert first.guild_id == 1
    second = queue._claim_next(str(first.guild_id))
    assert second.guild_id == 2
    third = queue._claim_next(str(second.guild_id))
    assert third.guild_id == 3
    # The other job from guild 1 must wait for its guild's active job.
    assert queue._claim_next(str(third.guild_id)) is None
    queue._complete(first.id)
    fourth = queue._claim_next(str(third.guild_id))
    assert fourth.guild_id == 1
    assert fourth.member_id == 12
    for job in (second, third, fourth):
        queue._complete(job.id)
    assert queue._snapshot()["queued"] == queue._snapshot()["running"] == 0


def test_capacity_is_global_guild_bounded_and_one_member(monkeypatch):
    monkeypatch.setattr(queue, "MAX_PENDING_TOTAL", 5)
    monkeypatch.setattr(queue, "MAX_PENDING_PER_GUILD", 2)
    assert queue._submit(1, 11, 100, 201) == "queued"
    assert queue._submit(1, 11, 100, 202) == "member_busy"
    assert queue._submit(1, 11, 101, 201) == "duplicate"
    assert queue._submit(1, 11, 101, 202) == "queued"
    assert queue._submit(1, 11, 102, 203) == "guild_full"
    assert queue._submit(2, 22, 200, 204) == "queued"
    assert queue._submit(2, 22, 201, 205) == "queued"
    assert queue._submit(3, 33, 300, 206) == "queued"
    assert queue._submit(4, 44, 400, 207) == "full"
    assert queue._submit(-1, 11, 20, 900) == "invalid"


def test_parallel_admission_respects_global_capacity(monkeypatch):
    monkeypatch.setattr(queue, "MAX_PENDING_TOTAL", 5)
    monkeypatch.setattr(queue, "MAX_PENDING_PER_GUILD", 5)

    async def scenario():
        returns = await asyncio.gather(*[
            asyncio.to_thread(queue._submit, 100 + i // 4, 500 + i // 4, 1000 + i, 8000 + i)
            for i in range(16)
        ])
        assert returns.count("queued") == 5
        assert all(v in {"queued", "full"} for v in returns)
        assert queue._snapshot()["queued"] == 5

    asyncio.run(scenario())


def test_recover_running_never_replays_ambiguous_dm_and_preserves_pending():
    assert queue._submit(1, 11, 100, 201) == "queued"
    assert queue._submit(2, 22, 200, 202) == "queued"
    active = queue._claim_next("")
    assert active is not None
    assert queue._snapshot() == {
        "queued": 1, "running": 1, "limit": queue.MAX_PENDING_TOTAL,
        "per_guild_limit": queue.MAX_PENDING_PER_GUILD, "workers": queue.MAX_WORKERS,
    }
    assert queue._recover_interrupted() == 1
    assert queue._snapshot()["running"] == 0
    remaining = queue._claim_next("")
    assert remaining.message_id != active.message_id
    queue._complete(remaining.id)


def test_expired_unstarted_jobs_drop_without_download(monkeypatch):
    assert queue._submit(1, 11, 100, 201) == "queued"
    with sqlite3.connect(queue._db_path()) as conn:
        conn.execute(
            "UPDATE download_queue SET created_at=? WHERE message_id='201'",
            (time.time() - queue.MAX_QUEUED_AGE_SECONDS - 10,),
        )
    assert queue._claim_next("") is None
    assert queue._snapshot()["queued"] == 0


def test_queue_gateway_rejects_invalid_url_before_storage(monkeypatch):
    class Member:
        bot = False
        id = 200

    monkeypatch.setattr(queue.discord, "Member", Member)

    class Channel:
        id = 500

        def permissions_for(self, member):
            return SimpleNamespace(view_channel=True, send_messages=True)

        def is_nsfw(self):
            return False

    problem = []

    async def reply(message, content):
        problem.append(content)

    monkeypatch.setattr(queue, "_reply_problem", reply)

    message = SimpleNamespace(
        guild=SimpleNamespace(id=400),
        author=Member(), channel=Channel(),
        id=600, content="http://127.0.0.1/admin.mp4", webhook_id=None,
    )
    assert asyncio.run(queue.enqueue_download_request(message))
    assert problem
    assert queue._snapshot()["queued"] == 0


def test_worker_rechecks_channel_member_and_message_before_download(monkeypatch):
    class Member:
        bot = False
        id = 200

    class Channel:
        id = 500

        async def fetch_message(self, message_id):
            return SimpleNamespace(
                guild=guild, channel=self, author=member, id=message_id,
                content="https://cdn.example.com/test.mp4",
            )

    monkeypatch.setattr(queue.discord, "Member", Member)
    member = Member()
    channel = Channel()
    guild = SimpleNamespace(
        id=400,
        get_channel=lambda channel_id: channel if channel_id == 500 else None,
    )
    bot = SimpleNamespace(get_guild=lambda guild_id: guild if guild_id == 400 else None)
    monkeypatch.setattr(queue.discord, "TextChannel", Channel)

    async def get_download_channel(guild_id):
        return 500

    calls = []

    async def process(message):
        calls.append(message.id)
        return True

    monkeypatch.setattr(runtime, "guild_download_channel_id", get_download_channel)
    monkeypatch.setattr(queue, "handle_download_message", process)

    async def scenario():
        job = queue.DownloadJob(1, 400, 500, 200, 600)
        await queue._process_job(bot, job)
        assert calls == [600]
        async def switched(guild_id):
            return 999
        monkeypatch.setattr(runtime, "guild_download_channel_id", switched)
        await queue._process_job(bot, job)
        assert calls == [600]

    asyncio.run(scenario())


def test_worker_resolves_uncached_guild_member_before_download(monkeypatch):
    class User:
        bot = False
        id = 200

    class Member(User):
        pass

    class Channel:
        id = 500

        async def fetch_message(self, msg_id):
            return SimpleNamespace(
                guild=guild, author=User(), channel=self, id=msg_id,
                content="https://cdn.example.com/video.mp4",
            )

    channel = Channel()
    guild = SimpleNamespace(
        id=400,
        get_channel=lambda cid: channel if cid == 500 else None,
        get_member=lambda member_id: None,
        fetch_member=None,
    )
    fetch_calls = []

    async def fetch_member(member_id):
        fetch_calls.append(member_id)
        return Member()

    guild.fetch_member = fetch_member
    bot = SimpleNamespace(get_guild=lambda guild_id: guild if guild_id == 400 else None)
    monkeypatch.setattr(queue.discord, "Member", Member)
    monkeypatch.setattr(queue.discord, "TextChannel", Channel)

    async def configured(guild_id):
        return 500

    seen = []

    async def deliver(msg):
        seen.append((msg.id, isinstance(msg.author, Member)))

    monkeypatch.setattr(runtime, "guild_download_channel_id", configured)
    monkeypatch.setattr(queue, "handle_download_message", deliver)
    asyncio.run(queue._process_job(bot, queue.DownloadJob(1, 400, 500, 200, 600)))
    assert fetch_calls == [200]
    assert seen == [(600, True)]


def test_route_cache_handles_many_registered_guilds_without_reparsing(monkeypatch, tmp_path):
    monkeypatch.setattr(runtime, "ROUTES_FILE", tmp_path / "routes.json")
    monkeypatch.setattr(runtime, "_ROUTE_STATE_CACHE", None)
    import json
    store = {
        str(g): {
            "routes": [{"source_channel_id": str(g * 10), "target_channel_id": str(g * 10 + 1)}],
            "download_channel_id": str(g * 10 + 2),
        }
        for g in range(1, 10001)
    }
    runtime.ROUTES_FILE.write_text(json.dumps(store))
    reads = []
    original = Path.read_text

    def tracked(self, *args, **kwargs):
        if self == runtime.ROUTES_FILE:
            reads.append(1)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", tracked)

    async def scenario():
        for guild_id in (1, 5000, 10000, 5001, 1):
            routes, download_id = await runtime.guild_route_state(guild_id)
            assert routes[0]["source_channel_id"] == str(guild_id * 10)
            assert download_id == guild_id * 10 + 2
    asyncio.run(scenario())
    assert len(reads) == 1  # one file load, not five full JSON parses


def test_guild_route_read_cache_reuses_snapshot_and_invalidates_on_write(monkeypatch, tmp_path):
    monkeypatch.setattr(runtime, "ROUTES_FILE", tmp_path / "routes.json")
    monkeypatch.setattr(runtime, "_ROUTE_STATE_CACHE", None)
    reads = []
    original = Path.read_text

    def counted(self, *args, **kwargs):
        if self == runtime.ROUTES_FILE:
            reads.append(1)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", counted)

    async def scenario():
        await runtime.save_route(4, source_channel_id=55, target_channel_id=66)
        before = len(reads)
        first = await runtime.guild_route_state(4)
        assert len(reads) == before + 1
        second = await runtime.guild_route_state(4)
        assert second == first
        assert len(reads) == before + 1
        # An admin change must be visible on the very next message.
        await runtime.save_route(4, source_channel_id=55, target_channel_id=99)
        after_write = len(reads)
        updated = await runtime.guild_route_state(4)
        assert len(reads) == after_write + 1
        assert updated[0][0]["target_channel_id"] == "99"
        assert first[0][0]["target_channel_id"] == "66"
        assert updated[0] is not first[0]
        # A changed backing path cannot reuse a previous guild's cache.
        monkeypatch.setattr(runtime, "ROUTES_FILE", tmp_path / "other.json")
        assert await runtime.guild_route_state(4) == ([], 0)

    asyncio.run(scenario())


def test_download_queue_is_not_a_new_gateway_listener():
    runtime_source = Path("stoney_verify/share_router_runtime.py").read_text()
    queue_source = Path("stoney_verify/share_router_download_queue.py").read_text()
    assert runtime_source.count('bot.add_listener(route_message, "on_message")') == 1
    assert "enqueue_download_request(message)" in runtime_source
    assert "await start_download_workers(bot)" in runtime_source
    assert "add_listener" not in queue_source
