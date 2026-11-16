from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from stoney_verify.members_new import membership_authority, sync_service
from stoney_verify.members_new.membership_authority import MembershipSnapshot


class FakeMember:
    def __init__(self, user_id: int, *, bot: bool = False) -> None:
        self.id = user_id
        self.bot = bot


class FakeGuild:
    def __init__(self, guild_id: int = 123) -> None:
        self.id = guild_id
        self.members: list[FakeMember] = []


class FakeFetchGuild(FakeGuild):
    def __init__(
        self,
        fetched_members: list[FakeMember],
        *,
        cached_members: list[FakeMember] | None = None,
        fetch_error: Exception | None = None,
        guild_id: int = 123,
    ) -> None:
        super().__init__(guild_id)
        self._fetched_members = list(fetched_members)
        self.members = list(cached_members or [])
        self._fetch_error = fetch_error
        self.requested_limit: int | None | object = object()

    async def fetch_members(self, *, limit=None):
        self.requested_limit = limit
        if self._fetch_error is not None:
            raise self._fetch_error
        for member in self._fetched_members:
            yield member


def test_collect_membership_snapshot_consumes_async_iterator_authoritatively() -> None:
    guild = FakeFetchGuild([FakeMember(10), FakeMember(20, bot=True)])

    snapshot = asyncio.run(membership_authority.collect_membership_snapshot(guild))

    assert guild.requested_limit is None
    assert [member.id for member in snapshot.members] == [10, 20]
    assert snapshot.active_user_ids == {10, 20}
    assert snapshot.authoritative is True
    assert snapshot.source == "discord_fetch_members"
    assert snapshot.error == ""
    assert membership_authority.departure_reconciliation_allowed(snapshot) is True


def test_collect_membership_snapshot_uses_cache_only_after_fetch_failure() -> None:
    guild = FakeFetchGuild(
        [],
        cached_members=[FakeMember(7)],
        fetch_error=RuntimeError("temporary Discord failure"),
    )

    snapshot = asyncio.run(membership_authority.collect_membership_snapshot(guild))

    assert guild.requested_limit is None
    assert [member.id for member in snapshot.members] == [7]
    assert snapshot.authoritative is False
    assert snapshot.source == "discord_member_cache"
    assert snapshot.error == "RuntimeError: temporary Discord failure"
    assert membership_authority.departure_reconciliation_allowed(snapshot) is False


def test_departed_reconciliation_skips_when_full_member_fetch_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    guild = FakeGuild()
    snapshot = MembershipSnapshot(
        members=(FakeMember(1),),
        authoritative=False,
        source="discord_member_cache",
        error="HTTPException: temporary Discord failure",
    )

    async def fake_collect(_guild):
        return snapshot

    bulk_calls: list[set[str]] = []

    async def fake_bulk(_sb, _guild_id, active_ids):
        bulk_calls.append(set(active_ids))
        return 99

    monkeypatch.setattr(sync_service, "collect_membership_snapshot", fake_collect)
    monkeypatch.setattr(sync_service, "get_supabase", lambda: object())
    monkeypatch.setattr(sync_service, "_bulk_mark_departed_members_async", fake_bulk)

    result = asyncio.run(sync_service.run_departed_reconciliation_for_guild(guild))

    assert result["marked_departed"] == 0
    assert result["departure_reconciliation_skipped"] is True
    assert result["departure_skip_reason"] == "authoritative_member_fetch_failed"
    assert result["membership_authoritative"] is False
    assert result["checked"] == 1
    assert bulk_calls == []


def test_full_sync_uses_cache_only_as_positive_membership_evidence(monkeypatch: pytest.MonkeyPatch) -> None:
    guild = FakeGuild()
    cached_member = FakeMember(7)
    snapshot = MembershipSnapshot(
        members=(cached_member,),
        authoritative=False,
        source="discord_member_cache",
        error="Gateway member fetch unavailable",
    )

    async def fake_collect(_guild):
        return snapshot

    synced: list[int] = []

    async def fake_sync(member, *, in_guild=True):
        assert in_guild is True
        synced.append(member.id)

    async def forbidden_bulk(*_args, **_kwargs):
        raise AssertionError("non-authoritative cache absence must never mark departures")

    monkeypatch.setattr(sync_service, "collect_membership_snapshot", fake_collect)
    monkeypatch.setattr(sync_service, "get_supabase", lambda: object())
    monkeypatch.setattr(sync_service, "sync_member_to_supabase", fake_sync)
    monkeypatch.setattr(sync_service, "_bulk_mark_departed_members_async", forbidden_bulk)

    result = asyncio.run(sync_service.run_full_member_sync_for_guild(guild))

    assert synced == [7]
    assert result["active_members_synced"] == 1
    assert result["marked_departed"] == 0
    assert result["departure_reconciliation_skipped"] is True
    assert result["membership_authoritative"] is False


def test_authoritative_reconciliation_preserves_all_live_ids_including_bots(monkeypatch: pytest.MonkeyPatch) -> None:
    guild = FakeGuild()
    snapshot = MembershipSnapshot(
        members=(FakeMember(10), FakeMember(20, bot=True)),
        authoritative=True,
        source="discord_fetch_members",
    )

    async def fake_collect(_guild):
        return snapshot

    captured: dict[str, object] = {}

    async def fake_bulk(_sb, guild_id, active_ids):
        captured["guild_id"] = guild_id
        captured["active_ids"] = set(active_ids)
        return 3

    monkeypatch.setattr(sync_service, "collect_membership_snapshot", fake_collect)
    monkeypatch.setattr(sync_service, "get_supabase", lambda: object())
    monkeypatch.setattr(sync_service, "_bulk_mark_departed_members_async", fake_bulk)

    result = asyncio.run(sync_service.run_departed_reconciliation_for_guild(guild))

    assert captured["guild_id"] == "123"
    assert captured["active_ids"] == {"10", "20"}
    assert result["checked"] == 2
    assert result["marked_departed"] == 3
    assert result["membership_authoritative"] is True
    assert "departure_reconciliation_skipped" not in result


def test_membership_snapshot_authority_contract_is_explicit() -> None:
    authoritative = MembershipSnapshot(tuple(), True, "discord_fetch_members")
    cached = MembershipSnapshot(tuple(), False, "discord_member_cache", "temporary failure")

    assert membership_authority.departure_reconciliation_allowed(authoritative) is True
    assert membership_authority.departure_reconciliation_allowed(cached) is False


def test_native_sync_service_owns_authority_boundary_without_startup_monkey_patch() -> None:
    authority_source = Path("stoney_verify/members_new/membership_authority.py").read_text(encoding="utf-8")
    sync_source = Path("stoney_verify/members_new/sync_service.py").read_text(encoding="utf-8")
    main_source = Path("main.py").read_text(encoding="utf-8")

    assert "tuple(member async for member" not in authority_source
    assert "async for member in guild.fetch_members(limit=None)" in authority_source
    assert "from .membership_authority import collect_membership_snapshot, departure_reconciliation_allowed" in sync_source
    assert sync_source.count("snapshot = await collect_membership_snapshot(guild)") == 2
    assert sync_source.count("if not departure_reconciliation_allowed(snapshot):") == 2
    assert '"departure_skip_reason"] = "authoritative_member_fetch_failed"' in sync_source
    assert "member_reconciliation_authority_guard" not in main_source
    assert not Path("stoney_verify/startup_guards/member_reconciliation_authority_guard.py").exists()


def test_bulk_departed_reconciliation_skips_rows_already_departed(monkeypatch: pytest.MonkeyPatch) -> None:
    rows = [
        {
            "user_id": "10",
            "in_guild": False,
            "data_health": "left_guild",
            "role_state": "left_guild",
            "times_left": 3,
        },
        {
            "user_id": "20",
            "in_guild": True,
            "data_health": "ok",
            "role_state": "verified",
            "times_left": 0,
        },
        {
            "user_id": "30",
            "in_guild": True,
            "data_health": "ok",
            "role_state": "verified",
            "times_left": 0,
        },
    ]

    async def fake_select(_sb, _guild_id, offset=0, limit=500):
        return SimpleNamespace(data=rows[offset:offset + limit])

    updates: list[tuple[str, dict]] = []

    async def fake_update(_sb, _guild_id, user_id, payload):
        updates.append((str(user_id), dict(payload)))

    monkeypatch.setattr(sync_service, "_guild_members_select_guild_rows_async", fake_select)
    monkeypatch.setattr(sync_service, "_guild_members_update_safe_async", fake_update)

    marked = asyncio.run(
        sync_service._bulk_mark_departed_members_async(
            object(),
            "123",
            {"30"},
        )
    )

    assert marked == 1
    assert [user_id for user_id, _payload in updates] == ["20"]
    assert updates[0][1]["in_guild"] is False
    assert updates[0][1]["role_state"] == "left_guild"
    assert updates[0][1]["times_left"] == 1


def test_member_prefetch_reads_exact_chunk_and_skips_unmatched_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    class Query:
        def select(self, *_args):
            return self
        def eq(self, field, value):
            assert (field, value) == ("guild_id", "123")
            return self
        def in_(self, field, value):
            assert field == "user_id" and set(value) == {"10", "20"}
            return self
        def limit(self, value):
            assert value == 2
            return self
        def execute(self):
            return SimpleNamespace(data=[
                {"user_id": "10", "username": "actual"},
                {"user_id": "99", "username": "not-requested"},
            ])
    class DB:
        def table(self, name):
            assert name == "guild_members"
            return Query()
    result = sync_service._guild_members_prefetch_sync(DB(), "123", ["10", "20"])
    assert result == {"10": {"user_id": "10", "username": "actual"}}


def test_full_member_sync_prefetches_once_per_chunk_and_preserves_authority(monkeypatch: pytest.MonkeyPatch) -> None:
    guild = FakeGuild()
    snapshot = MembershipSnapshot(
        members=(FakeMember(10), FakeMember(20)),
        authoritative=True,
        source="discord_fetch_members",
    )
    async def fake_collect(_guild):
        return snapshot
    prefetched = []
    async def fake_prefetch(_db, gid, ids):
        prefetched.append((gid, ids))
        return {"10": {"username": "prior"}}
    received = []
    async def fake_sync(member, *, in_guild=True, existing_member_row=None):
        received.append((member.id, existing_member_row, in_guild))
    active = []
    async def fake_mark(_db, gid, ids):
        active.append((gid, ids))
        return 0
    monkeypatch.setattr(sync_service, "collect_membership_snapshot", fake_collect)
    monkeypatch.setattr(sync_service, "get_supabase", lambda: object())
    monkeypatch.setattr(sync_service, "_guild_members_prefetch_async", fake_prefetch)
    monkeypatch.setattr(sync_service, "sync_member_to_supabase", fake_sync)
    monkeypatch.setattr(sync_service, "_bulk_mark_departed_members_async", fake_mark)
    result = asyncio.run(sync_service.run_full_member_sync_for_guild(guild))
    assert result["prefetched_member_chunks"] == 1
    assert prefetched == [("123", ["10", "20"])]
    assert received == [(10, {"username": "prior"}, True), (20, None, True)]
    assert active == [("123", {"10", "20"})]


def test_full_member_sync_prefetch_failure_falls_back_only_once(monkeypatch: pytest.MonkeyPatch) -> None:
    guild = FakeGuild()
    snapshot = MembershipSnapshot(
        members=tuple(FakeMember(i) for i in range(1, 53)),
        authoritative=False,
        source="discord_member_cache",
    )
    async def fake_collect(_guild):
        return snapshot
    attempts = []
    async def unavailable(_db, gid, ids):
        attempts.append((gid, len(ids)))
        return None
    synced = []
    async def exact_sync(member, *, in_guild=True):
        synced.append(member.id)
    monkeypatch.setattr(sync_service, "collect_membership_snapshot", fake_collect)
    monkeypatch.setattr(sync_service, "get_supabase", lambda: object())
    monkeypatch.setattr(sync_service, "_guild_members_prefetch_async", unavailable)
    monkeypatch.setattr(sync_service, "sync_member_to_supabase", exact_sync)
    result = asyncio.run(sync_service.run_full_member_sync_for_guild(guild))
    assert attempts == [("123", 50)]
    assert result["active_members_synced"] == 52
    assert result["departure_reconciliation_skipped"] is True
    assert synced == list(range(1, 53))


def test_departure_reconciliation_scans_beyond_default_postgrest_1000_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    page_calls = []
    all_rows = [
        {"user_id": str(i), "in_guild": True, "data_health": "ok",
         "role_state": "verified", "times_left": 0}
        for i in range(1205)
    ]
    async def paged(_sb, _guild, offset=0, limit=500):
        page_calls.append((offset, limit))
        return SimpleNamespace(data=all_rows[offset:offset + limit])
    updates = []
    async def update(_sb, _guild, uid, payload):
        updates.append((uid, payload))
    monkeypatch.setattr(sync_service, "_guild_members_select_guild_rows_async", paged)
    monkeypatch.setattr(sync_service, "_guild_members_update_safe_async", update)
    active = {str(i) for i in range(1204)}
    marked = asyncio.run(sync_service._bulk_mark_departed_members_async(object(), "123", active))
    assert marked == 1
    assert [uid for uid, _payload in updates] == ["1204"]
    assert page_calls == [(0, 500), (500, 500), (1000, 500)]


def test_departure_reconciliation_read_failure_is_not_silent_success(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fail_after_first(_sb, _guild, offset=0, limit=500):
        if offset == 0:
            return SimpleNamespace(data=[{"user_id": str(i), "in_guild": True} for i in range(500)])
        raise RuntimeError("storage timeout")
    monkeypatch.setattr(sync_service, "_guild_members_select_guild_rows_async", fail_after_first)
    import pytest as local_pytest
    with local_pytest.raises(RuntimeError, match="storage timeout"):
        asyncio.run(sync_service._bulk_mark_departed_members_async(
            object(), "123", {str(i) for i in range(500)},
        ))


def test_departed_page_query_is_stable_and_bounded() -> None:
    seen = []
    class Query:
        def select(self, *_cols): return self
        def eq(self, *_args): return self
        def order(self, field):
            assert field == "user_id"
            return self
        def range(self, start, end):
            seen.append((start, end))
            return self
        def execute(self): return SimpleNamespace(data=[])
    class DB:
        def table(self, table):
            assert table == "guild_members"
            return Query()
    sync_service._guild_members_select_guild_rows_sync(DB(), "123", 1000, 500)
    assert seen == [(1000, 1499)]
