from __future__ import annotations

from types import SimpleNamespace

import pytest

from stoney_verify.members_new import sync_service
from stoney_verify.members_new.membership_authority import MembershipSnapshot


class FakeMember:
    def __init__(self, user_id: int, *, bot: bool = False) -> None:
        self.id = user_id
        self.bot = bot


class FakeGuild:
    def __init__(self, guild_id: int = 123) -> None:
        self.id = guild_id


@pytest.mark.asyncio
async def test_departed_reconciliation_skips_when_full_member_fetch_failed(monkeypatch: pytest.MonkeyPatch) -> None:
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
    monkeypatch.setattr(sync_service._impl, "get_supabase", lambda: object())
    monkeypatch.setattr(sync_service._impl, "_bulk_mark_departed_members_async", fake_bulk)

    result = await sync_service.run_departed_reconciliation_for_guild(guild)

    assert result["marked_departed"] == 0
    assert result["departure_reconciliation_skipped"] is True
    assert result["departure_skip_reason"] == "authoritative_member_fetch_failed"
    assert result["membership_authoritative"] is False
    assert result["checked"] == 1
    assert bulk_calls == []


@pytest.mark.asyncio
async def test_full_sync_uses_cache_only_as_positive_membership_evidence(monkeypatch: pytest.MonkeyPatch) -> None:
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
    monkeypatch.setattr(sync_service._impl, "get_supabase", lambda: object())
    monkeypatch.setattr(sync_service._impl, "sync_member_to_supabase", fake_sync)
    monkeypatch.setattr(sync_service._impl, "_bulk_mark_departed_members_async", forbidden_bulk)

    result = await sync_service.run_full_member_sync_for_guild(guild)

    assert synced == [7]
    assert result["active_members_synced"] == 1
    assert result["marked_departed"] == 0
    assert result["departure_reconciliation_skipped"] is True
    assert result["membership_authoritative"] is False


@pytest.mark.asyncio
async def test_authoritative_reconciliation_preserves_all_live_ids_including_bots(monkeypatch: pytest.MonkeyPatch) -> None:
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
    monkeypatch.setattr(sync_service._impl, "get_supabase", lambda: object())
    monkeypatch.setattr(sync_service._impl, "_bulk_mark_departed_members_async", fake_bulk)

    result = await sync_service.run_departed_reconciliation_for_guild(guild)

    assert captured["guild_id"] == "123"
    assert captured["active_ids"] == {"10", "20"}
    assert result["checked"] == 2
    assert result["marked_departed"] == 3
    assert result["membership_authoritative"] is True
    assert "departure_reconciliation_skipped" not in result


def test_membership_snapshot_authority_contract_is_explicit() -> None:
    authoritative = MembershipSnapshot(tuple(), True, "discord_fetch_members")
    cached = MembershipSnapshot(tuple(), False, "discord_member_cache", "temporary failure")

    assert sync_service.departure_reconciliation_allowed(authoritative) is True
    assert sync_service.departure_reconciliation_allowed(cached) is False
