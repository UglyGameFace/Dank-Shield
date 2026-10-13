"""Voice #517: membership-based Voice Safety agreement sync.

Voice Safety is a membership rule in enabled guilds: current members are
grandfathered into agreement, joins record agreement, and leaves withdraw it
(the opt-out).

The consent module and sync runtime import their storage/config dependencies
lazily, so these tests inject in-memory fakes through ``sys.modules`` and
exercise the real validation logic.
"""
from __future__ import annotations

import asyncio
import sys
import types
from contextlib import asynccontextmanager
from functools import wraps
from unittest.mock import AsyncMock  # noqa: F401  (parity with sibling test module)

import pytest

from stoney_verify import voice_safety_consent as consent
from stoney_verify import voice_safety_membership_sync_runtime as sync_runtime


def sync_async(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        return asyncio.run(fn(*args, **kwargs))
    return wrapper


class FakeMember:
    def __init__(self, guild, mid, *, bot=False):
        self.guild = guild
        self.id = mid
        self.bot = bot


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
    """In-memory profile guild settings backing the real consent functions."""
    data: dict[tuple[int, int], dict] = {}

    async def fake_get(guild_id, user_id, *, refresh=False):
        return {"settings": dict(data.get((int(guild_id), int(user_id)), {}))}

    async def fake_upsert(guild_id, user_id, key, value):
        settings = data.setdefault((int(guild_id), int(user_id)), {})
        settings[str(key)] = value
        return {"settings": dict(settings)}

    module = types.ModuleType("stoney_verify.profile_card_service")
    module.get_profile_guild_settings = fake_get
    module.upsert_profile_guild_namespace = fake_upsert
    monkeypatch.setitem(sys.modules, "stoney_verify.profile_card_service", module)
    return data


@pytest.fixture
def guild_config(monkeypatch):
    """In-memory guild config with per-guild voice_safety_enabled and patches."""
    enabled: set[int] = set()
    patches: dict[int, dict] = {}

    async def fake_get(guild_id, *args, **kwargs):
        cfg = {"voice_safety_enabled": int(guild_id) in enabled}
        cfg.update(patches.get(int(guild_id), {}))
        return cfg

    async def fake_upsert(guild_id, patch):
        patches.setdefault(int(guild_id), {}).update(dict(patch))
        return dict(patches[int(guild_id)])

    module = types.ModuleType("stoney_verify.guild_config")
    module.get_guild_config = fake_get
    module.upsert_guild_config = fake_upsert
    monkeypatch.setitem(sys.modules, "stoney_verify.guild_config", module)

    # No real Discord REST or startup delay during unit tests.
    async def fake_collect(guild):
        return types.SimpleNamespace(
            members=tuple(getattr(guild, "fetched_members", guild.members)),
            authoritative=getattr(guild, "authoritative", True),
        )

    @asynccontextmanager
    async def fake_slot(guild_id, label):
        yield

    membership = types.ModuleType("stoney_verify.members_new.membership_authority")
    membership.collect_membership_snapshot = fake_collect
    recovery = types.ModuleType("stoney_verify.startup_recovery_coordinator")
    recovery.startup_recovery_slot = fake_slot
    monkeypatch.setitem(sys.modules, membership.__name__, membership)
    monkeypatch.setitem(sys.modules, recovery.__name__, recovery)
    return types.SimpleNamespace(enabled=enabled, patches=patches)


def _explicit_record(source="voice_safety_notice_v1", accepted=True):
    return {
        consent.VOICE_SAFETY_AGREEMENT_KEY: {
            "accepted": accepted, "version": 1, "source": source,
            "acted_at": "2026-10-01T00:00:00+00:00",
        }
    }


@sync_async
async def test_record_membership_agreement_writes_affirmative_source(store):
    assert await consent.record_membership_agreement(71, 24) is True
    value = store[(71, 24)][consent.VOICE_SAFETY_AGREEMENT_KEY]
    assert value["accepted"] is True
    assert value["version"] == 1
    assert value["source"] == consent.VOICE_SAFETY_MEMBERSHIP_SOURCE
    assert value["acted_at"]
    assert await consent.read_voice_safety_agreement(71, 24) is True


@sync_async
async def test_record_membership_agreement_preserves_explicit_agreement(store):
    store[(71, 24)] = _explicit_record()
    before = store[(71, 24)][consent.VOICE_SAFETY_AGREEMENT_KEY]
    assert await consent.record_membership_agreement(71, 24) is True
    after = store[(71, 24)][consent.VOICE_SAFETY_AGREEMENT_KEY]
    assert after == before  # Original source and timestamp untouched.


@sync_async
async def test_record_membership_agreement_reaffirms_stale_decline(store):
    store[(71, 24)] = _explicit_record(accepted=False)
    assert await consent.read_voice_safety_agreement(71, 24) is False
    assert await consent.record_membership_agreement(71, 24) is True
    value = store[(71, 24)][consent.VOICE_SAFETY_AGREEMENT_KEY]
    assert value["accepted"] is True
    assert value["source"] == consent.VOICE_SAFETY_MEMBERSHIP_SOURCE


@sync_async
async def test_withdraw_agreement_on_leave(store):
    store[(71, 24)] = _explicit_record()
    assert await consent.withdraw_agreement_on_leave(71, 24) is True
    value = store[(71, 24)][consent.VOICE_SAFETY_AGREEMENT_KEY]
    assert value["accepted"] is False
    assert value["source"] == consent.VOICE_SAFETY_MEMBERSHIP_SOURCE
    assert await consent.read_voice_safety_agreement(71, 24) is False


@sync_async
async def test_withdraw_agreement_on_leave_no_record_no_write(store):
    assert await consent.withdraw_agreement_on_leave(71, 99) is False
    assert (71, 99) not in store


@sync_async
async def test_sync_grandfathers_members_skips_bots_and_existing(store):
    guild = FakeGuild(71, [
        FakeMember(None, 1), FakeMember(None, 2),
        FakeMember(None, 3, bot=True), FakeMember(None, 4),
    ])
    for member in guild.members:
        member.guild = guild
    store[(71, 2)] = _explicit_record()
    store[(71, 4)] = _explicit_record(accepted=False)

    result = await consent.sync_guild_membership_agreement(guild)
    assert result == {"recorded": 2, "skipped": 2, "failed": 0}
    assert store[(71, 1)][consent.VOICE_SAFETY_AGREEMENT_KEY]["accepted"] is True
    assert (71, 3) not in store  # Bots never get an agreement record.
    assert store[(71, 2)][consent.VOICE_SAFETY_AGREEMENT_KEY]["source"] == "voice_safety_notice_v1"
    assert store[(71, 4)][consent.VOICE_SAFETY_AGREEMENT_KEY]["accepted"] is True


@sync_async
async def test_sync_counts_failures_without_raising(store, monkeypatch):
    async def flaky_upsert(guild_id, user_id, key, value):
        if int(user_id) == 2:
            raise RuntimeError("simulated storage outage")
        settings = store.setdefault((int(guild_id), int(user_id)), {})
        settings[str(key)] = value
        return {"settings": dict(settings)}

    module = sys.modules["stoney_verify.profile_card_service"]
    monkeypatch.setattr(module, "upsert_profile_guild_namespace", flaky_upsert)
    guild = FakeGuild(71, [FakeMember(None, 1), FakeMember(None, 2)])
    for member in guild.members:
        member.guild = guild

    result = await consent.sync_guild_membership_agreement(guild)
    assert result == {"recorded": 1, "skipped": 0, "failed": 1}


@sync_async
async def test_join_listener_records_agreement_when_enabled(store, guild_config):
    guild = FakeGuild(71)
    guild_config.enabled.add(71)
    await sync_runtime._on_member_join(FakeMember(guild, 24))
    assert store[(71, 24)][consent.VOICE_SAFETY_AGREEMENT_KEY]["accepted"] is True


@sync_async
async def test_join_listener_skips_bots_and_disabled_guilds(store, guild_config):
    guild = FakeGuild(71)
    await sync_runtime._on_member_join(FakeMember(guild, 24, bot=True))
    await sync_runtime._on_member_join(FakeMember(guild, 25))
    assert (71, 24) not in store
    assert (71, 25) not in store


@sync_async
async def test_remove_listener_withdraws_on_leave(store, guild_config):
    guild = FakeGuild(71)
    guild_config.enabled.add(71)
    store[(71, 24)] = _explicit_record()
    await sync_runtime._on_member_remove(FakeMember(guild, 24))
    assert store[(71, 24)][consent.VOICE_SAFETY_AGREEMENT_KEY]["accepted"] is False


@sync_async
async def test_remove_listener_no_record_no_write(store, guild_config):
    guild = FakeGuild(71)
    guild_config.enabled.add(71)
    await sync_runtime._on_member_remove(FakeMember(guild, 99))
    assert (71, 99) not in store


@sync_async
async def test_backfill_runs_once_per_guild(store, guild_config):
    guild = FakeGuild(71, [FakeMember(None, 24)])
    guild.members[0].guild = guild
    guild_config.enabled.add(71)
    bot = FakeBot([guild])

    await sync_runtime._run_backfill(bot)
    assert store[(71, 24)][consent.VOICE_SAFETY_AGREEMENT_KEY]["accepted"] is True
    assert guild_config.patches[71]["voice_safety_membership_backfill_v2"] is True

    # Second ready: marker present, no duplicate writes.
    acted_at = store[(71, 24)][consent.VOICE_SAFETY_AGREEMENT_KEY]["acted_at"]
    await sync_runtime._run_backfill(bot)
    assert store[(71, 24)][consent.VOICE_SAFETY_AGREEMENT_KEY]["acted_at"] == acted_at


@sync_async
async def test_backfill_retries_when_members_failed(store, guild_config, monkeypatch):
    async def flaky_upsert(guild_id, user_id, key, value):
        raise RuntimeError("simulated storage outage")

    module = sys.modules["stoney_verify.profile_card_service"]
    monkeypatch.setattr(module, "upsert_profile_guild_namespace", flaky_upsert)
    guild = FakeGuild(71, [FakeMember(None, 24)])
    guild.members[0].guild = guild
    guild_config.enabled.add(71)

    await sync_runtime._run_backfill(FakeBot([guild]))
    assert "voice_safety_membership_backfill_v2" not in guild_config.patches.get(71, {})


@sync_async
async def test_backfill_skips_disabled_guilds(store, guild_config):
    guild = FakeGuild(71, [FakeMember(None, 24)])
    guild.members[0].guild = guild
    await sync_runtime._run_backfill(FakeBot([guild]))
    assert (71, 24) not in store


def test_install_registers_listeners_once():
    bot = FakeBot()
    assert sync_runtime.install_voice_safety_membership_sync(bot) is True
    assert sync_runtime.install_voice_safety_membership_sync(bot) is True
    names = [name for _, name in bot.listeners]
    assert names.count("on_member_join") == 1
    assert names.count("on_member_remove") == 1
    assert names.count("on_ready") == 1
@sync_async
async def test_backfill_does_not_mark_partial_cache_authoritative(store, guild_config):
    guild = FakeGuild(71, [FakeMember(None, 24)])
    guild.authoritative = False
    guild_config.enabled.add(71)
    await sync_runtime._run_backfill(FakeBot([guild]))
    assert (71, 24) in store  # Cached positive evidence can be written.
    assert "voice_safety_membership_backfill_v2" not in guild_config.patches.get(71, {})


@sync_async
async def test_backfill_uses_full_snapshot_when_cache_is_empty(store, guild_config):
    guild = FakeGuild(71, [])
    guild.fetched_members = [FakeMember(guild, 24)]
    guild_config.enabled.add(71)
    await sync_runtime._run_backfill(FakeBot([guild]))
    assert (71, 24) in store
    assert guild_config.patches[71]["voice_safety_membership_backfill_v2"] is True


@sync_async
async def test_ready_listener_does_not_schedule_duplicate_backfill(store, guild_config):
    bot = FakeBot()
    assert sync_runtime.install_voice_safety_membership_sync(bot) is True
    await bot.handlers["on_ready"]()
    first = bot._voice_safety_membership_backfill_task
    await bot.handlers["on_ready"]()
    assert bot._voice_safety_membership_backfill_task is first
    await first
