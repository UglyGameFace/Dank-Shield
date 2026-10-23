from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import discord
import pytest

from stoney_verify import somewhere_420_role_headsup as headsup
from stoney_verify import somewhere_420_schedules as schedules
from stoney_verify.commands_ext import public_somewhere_420_settings as settings


def at(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def test_five_minute_headsup_is_dst_aware_and_no_immediate_repeat():
    assert schedules.next_daily_fire("America/New_York", now=at("2026-07-01T12:00:00Z"), reminder_minutes=5) == at("2026-07-01T20:15:00Z")
    assert schedules.next_daily_fire("America/New_York", now=at("2026-01-01T12:00:00Z"), reminder_minutes=5) == at("2026-01-01T21:15:00Z")
    assert schedules.next_daily_fire("America/New_York", now=at("2026-03-07T22:00:00Z"), reminder_minutes=5) == at("2026-03-08T20:15:00Z")
    assert schedules.next_daily_fire("Asia/Kathmandu", now=at("2026-04-01T10:16:00Z"), reminder_minutes=5) == at("2026-04-02T10:30:00Z")
    with pytest.raises(ValueError):
        schedules.next_daily_fire("UTC", reminder_minutes=30)
    assert headsup.ROLE_MINUTES_BEFORE == 5


def test_role_reminders_are_automatic_for_holders_not_every_member(monkeypatch):
    role = SimpleNamespace(id=345)
    member1 = SimpleNamespace(id=42, bot=False, pending=False, roles=[role])
    member2 = SimpleNamespace(id=47, bot=False, pending=False, roles=[role])
    other = SimpleNamespace(id=57, bot=False, pending=False, roles=[])
    role.members = [member2, member1]
    guild = SimpleNamespace(get_role=lambda rid: role if rid == 345 else None)
    monkeypatch.setattr(headsup.discord, "Role", type(role))
    monkeypatch.setattr(headsup.discord, "Member", type(member1))
    assert [m.id for m in headsup._role_members(guild, 345)] == [42, 47]
    assert other not in headsup._role_members(guild, 345)
    assert headsup._role_members(guild, 999) == []


def test_role_program_disabled_requires_explicit_staff_activation(monkeypatch):
    async def stored(kind, ident):
        return {"role_reminder_enabled": False}
    monkeypatch.setattr(headsup.schedules, "get_schedule", stored)
    fake_bot = SimpleNamespace(get_guild=lambda _id: SimpleNamespace())
    # Claimed guild with program disabled must never try Discord.
    assert asyncio.run(headsup.deliver_role_wave(fake_bot, {
        "guild_id": "123456789012345678"
    })) == "disabled"


def test_role_claim_uses_server_and_member_scope_with_one_atomic_rpc(monkeypatch):
    calls = []
    async def fake_execute(label, callback):
        class DB:
            def rpc(self, name, data):
                calls.append((name,data))
                return SimpleNamespace(execute=lambda: SimpleNamespace(data=True))
        return callback(DB())
    monkeypatch.setattr(schedules, "_execute", fake_execute)
    assert asyncio.run(headsup.claim_member(123456789012345678, 234567890123456789, "2026-10-22")) is True
    assert calls == [("dank_420_claim_role_member", {
        "p_guild": "123456789012345678",
        "p_user": "234567890123456789",
        "p_local_date": "2026-10-22",
    })]


def test_persistent_stop_control_and_search_safe_role_browser():
    view = headsup.StonersHeadsUpOptOut()
    assert view.timeout is None
    buttons = [c for c in view.children if isinstance(c, discord.ui.Button)]
    assert len(buttons) == 1
    assert buttons[0].custom_id == "dank:420:stoners:stop:v1"
    v = settings.Staff420View(123, None)
    labels = {b.label for b in v.children if isinstance(b, discord.ui.Button)}
    assert "Choose Stoners Role" in labels
    assert "Enable Stoners Heads-Up" in labels
    assert "Disable Stoners Heads-Up" in labels
    user = settings.Personal420View(123, None, guild_id=456)
    labels = {b.label for b in user.children if isinstance(b, discord.ui.Button)}
    assert "Stop Stoners Heads-Up" in labels
    assert "Resume Stoners Heads-Up" in labels
    assert len(v.children) <= 25


def test_role_migrations_safe_and_identical():
    root = Path(__file__).resolve().parents[1]
    paths = [
        "supabase/migrations/20261022201500_somewhere_420_role_heads_up.sql",
        "supabase_fresh/migrations/202610200027_dankshield_somewhere_420_role_heads_up.sql",
    ]
    a,b=[(root/p).read_text() for p in paths]
    assert a==b
    assert "for update skip locked" in a.lower()
    assert "role_reminder_enabled boolean not null default false" in a
    assert "dank_420_claim_role_member" in a
    assert "not exists" in a
    assert "on conflict do nothing" in a
    assert "revoke all" in a.lower()
    assert "security invoker" in a.lower()
    assert "security definer" not in a.lower()
