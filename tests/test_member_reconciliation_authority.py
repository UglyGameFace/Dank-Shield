from __future__ import annotations

from pathlib import Path

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

    monkeypatch.setattr(membership_authority, "collect_membership_snapshot", fake_collect)
    monkeypatch.setattr(sync_service, "get_supabase", lambda: object())
    monkeypatch.setattr(sync_service, "_bulk_mark_departed_members_async", fake_bulk)

    result = await membership_authority.run_safe_departed_reconciliation_for_guild(guild)

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

    monkeypatch.setattr(membership_authority, "collect_membership_snapshot", fake_collect)
    monkeypatch.setattr(sync_service, "get_supabase", lambda: object())
    monkeypatch.setattr(sync_service, "sync_member_to_supabase", fake_sync)
    monkeypatch.setattr(sync_service, "_bulk_mark_departed_members_async", forbidden_bulk)

    result = await membership_authority.run_safe_full_member_sync_for_guild(guild)

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

    monkeypatch.setattr(membership_authority, "collect_membership_snapshot", fake_collect)
    monkeypatch.setattr(sync_service, "get_supabase", lambda: object())
    monkeypatch.setattr(sync_service, "_bulk_mark_departed_members_async", fake_bulk)

    result = await membership_authority.run_safe_departed_reconciliation_for_guild(guild)

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


def test_authority_guard_loads_before_app_import_and_replaces_both_runtime_entry_points() -> None:
    main_source = Path("main.py").read_text(encoding="utf-8")
    guard_source = Path("stoney_verify/startup_guards/member_reconciliation_authority_guard.py").read_text(encoding="utf-8")

    guard_import = "import stoney_verify.startup_guards.member_reconciliation_authority_guard"
    app_import = "from stoney_verify.app import run as _run_dank_shield"
    assert guard_import in main_source
    assert main_source.index(guard_import) < main_source.index(app_import)
    assert "sync_service.run_full_member_sync_for_guild = membership_authority.run_safe_full_member_sync_for_guild" in guard_source
    assert "sync_service.run_departed_reconciliation_for_guild = membership_authority.run_safe_departed_reconciliation_for_guild" in guard_source
