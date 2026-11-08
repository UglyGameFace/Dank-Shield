"""Voice #517: genuine explicit opt-in, no auto-consent from server membership."""
from __future__ import annotations

import asyncio
import sys
import types
from functools import wraps

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


@sync_async
async def test_only_explicit_voice_agreement_is_admissible(store):
    assert not await consent.read_voice_safety_agreement(71, 24)
    for legacy in ("server_membership_v2", "unknown", "ordinary_verify"):
        store[(71, 24)] = record(source=legacy)
        assert not await consent.read_voice_safety_agreement(71, 24)
    for source in ("voice_safety_notice_v1", "basic_verify_v1"):
        store[(71, 24)] = record(source=source)
        assert await consent.read_voice_safety_agreement(71, 24)
    store[(71, 24)] = record(accepted=False)
    assert not await consent.read_voice_safety_agreement(71, 24)


@sync_async
async def test_join_does_not_regrant_declined_or_legacy_member(store):
    store[(71, 24)] = record(accepted=False)
    before = dict(store[(71, 24)])
    assert await consent.record_membership_agreement(71, 24) is False
    assert store[(71, 24)] == before
    assert not await consent.read_voice_safety_agreement(71, 24)
    store[(71, 24)] = record(source="server_membership_v2")
    assert await consent.record_membership_agreement(71, 24) is False
    assert not await consent.read_voice_safety_agreement(71, 24)


@sync_async
async def test_membership_backfill_has_no_consent_side_effects(store):
    guild = FakeGuild(71, [FakeMember(None, 24), FakeMember(None, 25)])
    guild.members[0].guild = guild
    guild.members[1].guild = guild
    assert await consent.sync_guild_membership_agreement(guild) == {
        "recorded": 0, "skipped": 2, "failed": 0,
    }
    assert not store
    await sync_runtime._run_backfill(FakeBot([guild]))
    assert not store


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


def test_install_only_wires_opt_out_once():
    bot = FakeBot()
    assert sync_runtime.install_voice_safety_membership_sync(bot)
    assert sync_runtime.install_voice_safety_membership_sync(bot)
    assert [label for _, label in bot.listeners] == ["on_member_remove"]
    assert "on_member_join" not in bot.handlers
    assert "on_ready" not in bot.handlers
