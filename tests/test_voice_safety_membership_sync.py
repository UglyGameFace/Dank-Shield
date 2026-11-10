"""Voice #517: membership is the opt-in; one-time grandfathering remains as
the audit trail for members who were already verified when the disclosed
Verify panel arrived."""
from __future__ import annotations

import asyncio
import sys
import types
from contextlib import asynccontextmanager
from functools import wraps
from types import SimpleNamespace

import pytest

from stoney_verify import voice_safety_consent as consent
from stoney_verify import voice_safety_membership_sync_runtime as sync_runtime

VERIFIED_ROLE_ID = 900


def sync_async(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        return asyncio.run(fn(*args, **kwargs))
    return wrapper


class FakeMember:
    def __init__(self, guild, mid, *, bot=False, roles=()):
        self.guild = guild
        self.id = mid
        self.bot = bot
        self.roles = [SimpleNamespace(id=role_id) for role_id in roles]


class FakeGuild:
    def __init__(self, gid, members=()):
        self.id = gid
        self.members = list(members)


class FakeBot:
    def __init__(self, guilds=()):
        self.guilds = list(guilds)
        self.listeners = []
        self.handlers = {}

    def add_listener(self, fn, name):
        self.listeners.append((fn.__name__, name))
        self.handlers[name] = fn


@pytest.fixture
def store(monkeypatch):
    data: dict[tuple[int, int], dict] = {}
    async def fake_get(guild_id, user_id, *, refresh=False):
        assert refresh is True
        return {"settings": dict(data.get((int(guild_id), int(user_id)), {}))}
    async def fake_upsert(guild_id, user_id, key, value):
        state = data.setdefault((int(guild_id), int(user_id)), {})
        state[str(key)] = value
        return {"settings": dict(state)}
    module = types.ModuleType("stoney_verify.profile_card_service")
    module.get_profile_guild_settings = fake_get
    module.upsert_profile_guild_namespace = fake_upsert
    monkeypatch.setitem(sys.modules, module.__name__, module)
    return data


def record(source="voice_safety_notice_v1", accepted=True):
    return {
        consent.VOICE_SAFETY_AGREEMENT_KEY: {
            "accepted": accepted, "version": 1, "source": source,
            "acted_at": "2026-10-01T00:00:00+00:00",
        }
    }


def verified_member(guild, mid, **kwargs):
    return FakeMember(guild, mid, roles=(VERIFIED_ROLE_ID,), **kwargs)


@sync_async
async def test_only_explicit_voice_agreement_is_admissible(store):
    assert not await consent.read_voice_safety_agreement(71, 24)
    for legacy in ("server_membership_v2", "unknown", "ordinary_verify"):
        store[(71, 24)] = record(source=legacy)
        assert not await consent.read_voice_safety_agreement(71, 24)
    for source in (
        "voice_safety_notice_v1",
        "basic_verify_v1",
        consent.VOICE_SAFETY_GRANDFATHER_SOURCE,
        consent.VOICE_SAFETY_DECLINE_SOURCE,
    ):
        store[(71, 24)] = record(source=source)
        assert await consent.read_voice_safety_agreement(71, 24)
    store[(71, 24)] = record(accepted=False)
    assert not await consent.read_voice_safety_agreement(71, 24)


@sync_async
async def test_only_explicit_decline_blocks_membership_admission(store):
    # No record at all: membership admits (no decline).
    assert not await consent.has_voice_safety_decline(71, 24)
    # An affirmative record: no decline.
    store[(71, 24)] = record(source="basic_verify_v1")
    assert not await consent.has_voice_safety_decline(71, 24)
    # A leave withdrawal (notice source): NOT a decline — rejoin re-admits.
    store[(71, 24)] = record(source="voice_safety_notice_v1", accepted=False)
    assert not await consent.has_voice_safety_decline(71, 24)
    # A legacy membership record: not a decline.
    store[(71, 24)] = record(source="server_membership_v2")
    assert not await consent.has_voice_safety_decline(71, 24)
    # A standing explicit decline: blocks.
    store[(71, 24)] = record(source=consent.VOICE_SAFETY_DECLINE_SOURCE, accepted=False)
    assert await consent.has_voice_safety_decline(71, 24)
    # An explicit agreement clears the decline.
    assert await consent.save_voice_safety_agreement(71, 24, True, source="basic_verify_v1")
    assert not await consent.has_voice_safety_decline(71, 24)
    assert await consent.read_voice_safety_agreement(71, 24)


@sync_async
async def test_grandfather_backfill_records_verified_members_only(store):
    guild = FakeGuild(71)
    fresh = verified_member(guild, 24)
    explicit = verified_member(guild, 25)
    legacy = verified_member(guild, 26)
    declined = verified_member(guild, 27)
    unverified = FakeMember(guild, 28)
    bot = verified_member(guild, 29, bot=True)
    guild.members = [fresh, explicit, legacy, declined, unverified, bot]
    store[(71, 25)] = record(source="basic_verify_v1")
    store[(71, 26)] = record(source="server_membership_v2")
    store[(71, 27)] = record(accepted=False)

    result = await consent.sync_guild_membership_agreement(
        guild, members=guild.members, verified_role_id=VERIFIED_ROLE_ID
    )
    assert result == {"recorded": 2, "skipped": 4, "failed": 0}

    # Fresh verified member: new grandfather record, now admissible.
    assert await consent.read_voice_safety_agreement(71, 24) is True
    assert store[(71, 24)][consent.VOICE_SAFETY_AGREEMENT_KEY]["source"] == (
        consent.VOICE_SAFETY_GRANDFATHER_SOURCE
    )
    # Explicit record: preserved exactly, source and timestamp untouched.
    assert store[(71, 25)][consent.VOICE_SAFETY_AGREEMENT_KEY]["source"] == "basic_verify_v1"
    assert store[(71, 25)][consent.VOICE_SAFETY_AGREEMENT_KEY]["acted_at"] == "2026-10-01T00:00:00+00:00"
    # Legacy membership record: converted to the grandfather source.
    assert store[(71, 26)][consent.VOICE_SAFETY_AGREEMENT_KEY]["source"] == (
        consent.VOICE_SAFETY_GRANDFATHER_SOURCE
    )
    assert await consent.read_voice_safety_agreement(71, 26) is True
    # Declined, unverified and bot members: untouched, still not admissible.
    assert store[(71, 27)][consent.VOICE_SAFETY_AGREEMENT_KEY]["accepted"] is False
    assert not await consent.read_voice_safety_agreement(71, 27)
    assert (71, 28) not in store
    assert (71, 29) not in store


@sync_async
async def test_grandfather_backfill_without_verified_role_records_nothing(store):
    guild = FakeGuild(71, [verified_member(None, 24)])
    guild.members[0].guild = guild
    assert await consent.sync_guild_membership_agreement(guild) == {
        "recorded": 0, "skipped": 1, "failed": 0,
    }
    assert not store


@sync_async
async def test_membership_backfill_has_no_consent_side_effects_when_disabled(store, monkeypatch):
    guild = FakeGuild(71, [FakeMember(None, 24), FakeMember(None, 25)])
    guild.members[0].guild = guild
    guild.members[1].guild = guild
    assert await consent.sync_guild_membership_agreement(guild) == {
        "recorded": 0, "skipped": 2, "failed": 0,
    }
    assert not store
    config = types.ModuleType("stoney_verify.guild_config")
    async def fake_get(guild_id, *, refresh=False):
        return {"voice_safety_enabled": False}
    async def fake_upsert(guild_id, values):
        raise AssertionError("backfill must not write when disabled")
    config.get_guild_config = fake_get
    config.upsert_guild_config = fake_upsert
    monkeypatch.setitem(sys.modules, config.__name__, config)
    await sync_runtime._run_backfill(FakeBot([guild]))
    assert not store


@sync_async
async def test_grandfather_backfill_runs_once(store, monkeypatch):
    guild = FakeGuild(71)
    member = verified_member(guild, 24)
    guild.members = [member]
    cfg: dict = {"voice_safety_enabled": True, "verified_role_id": str(VERIFIED_ROLE_ID)}

    config = types.ModuleType("stoney_verify.guild_config")
    async def fake_get(guild_id, *, refresh=False):
        return dict(cfg)
    async def fake_upsert(guild_id, values):
        cfg.update(values)
        return dict(cfg)
    config.get_guild_config = fake_get
    config.upsert_guild_config = fake_upsert
    monkeypatch.setitem(sys.modules, config.__name__, config)

    authority = types.ModuleType("stoney_verify.members_new.membership_authority")
    async def fake_snapshot(guild_arg):
        return SimpleNamespace(members=tuple(guild_arg.members), authoritative=True)
    authority.collect_membership_snapshot = fake_snapshot
    monkeypatch.setitem(sys.modules, authority.__name__, authority)

    coordinator = types.ModuleType("stoney_verify.startup_recovery_coordinator")
    @asynccontextmanager
    async def fake_slot(guild_id, label):
        yield
    coordinator.startup_recovery_slot = fake_slot
    monkeypatch.setitem(sys.modules, coordinator.__name__, coordinator)

    await sync_runtime._run_backfill(FakeBot([guild]))
    assert await consent.read_voice_safety_agreement(71, 24) is True
    assert cfg.get(sync_runtime._BACKFILL_MARKER) is True
    first = dict(store[(71, 24)])

    # Second run: marker set, nothing is re-recorded or overwritten.
    await sync_runtime._run_backfill(FakeBot([guild]))
    assert store[(71, 24)] == first


@sync_async
async def test_explicit_saved_opt_in_survives_join_and_withdraws_on_leave(store):
    assert await consent.save_voice_safety_agreement(71, 24, True) is True
    before = dict(store[(71, 24)])
    await sync_runtime._on_member_join(FakeMember(FakeGuild(71), 24))
    assert store[(71, 24)] == before
    assert await consent.read_voice_safety_agreement(71, 24) is True
    await sync_runtime._on_member_remove(FakeMember(FakeGuild(71), 24))
    assert not await consent.read_voice_safety_agreement(71, 24)
    assert store[(71, 24)][consent.VOICE_SAFETY_AGREEMENT_KEY]["accepted"] is False


@sync_async
async def test_nonconsenting_leave_does_not_invent_record(store):
    await sync_runtime._on_member_remove(FakeMember(FakeGuild(71), 24))
    assert not store
    await sync_runtime._on_member_remove(FakeMember(FakeGuild(71), 24, bot=True))
    assert not store


@sync_async
async def test_no_membership_source_may_save_accepted_voice_consent(store):
    with pytest.raises(ValueError, match="source"):
        await consent.save_voice_safety_agreement(
            71, 24, True, source="server_membership_v2",
        )
    assert not store


def test_install_wires_withdrawal_and_one_time_backfill_only():
    bot = FakeBot()
    assert sync_runtime.install_voice_safety_membership_sync(bot)
    assert sync_runtime.install_voice_safety_membership_sync(bot)
    assert [label for _, label in bot.listeners] == ["on_member_remove", "on_ready"]
    assert "on_member_join" not in bot.handlers
