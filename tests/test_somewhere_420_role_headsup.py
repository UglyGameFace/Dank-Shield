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
    assert schedules.next_daily_fire("Asia/Kathmandu", now=at("2026-04-01T10:31:00Z"), reminder_minutes=5) == at("2026-04-02T10:30:00Z")
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


def test_reminder_footer_is_human_only_and_ownership_is_private():
    import ast
    import inspect
    source = inspect.getsource(headsup)
    assert "Stoners reminder • Opt out anytime below" in source
    assert "Stoners reminder | guild" not in source
    assert "source_guild_for_role_dm" in source
    assert "record_role_dm" in source
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "set_footer":
            for kw in node.keywords:
                if kw.arg == "text":
                    assert isinstance(kw.value, ast.Constant)
                    assert isinstance(kw.value.value, str)
                    assert "guild_id" not in kw.value.value


def test_private_reminder_ownership_rejects_wrong_user(monkeypatch):
    rows = {}
    async def fake_execute(label, operation):
        class Table:
            filters = {}
            def upsert(self, value, on_conflict):
                rows[value["dm_message_id"]] = dict(value)
                self.value = dict(value)
                return self
            def select(self, columns):
                return self
            def eq(self, key, value):
                self.filters[key] = str(value)
                return self
            def limit(self, n):
                return self
            def execute(self):
                if label == "role DM ownership write":
                    return SimpleNamespace(data=[self.value])
                matched = [
                    row for row in rows.values()
                    if all(str(row.get(key)) == value for key, value in self.filters.items())
                ]
                return SimpleNamespace(data=matched)
        class DB:
            def table(self, name):
                assert name == "dank_somewhere_420_role_dm_links"
                return Table()
        return operation(DB())
    monkeypatch.setattr(schedules, "_execute", fake_execute)
    message_id = 345678901234567890
    guild_id = 456789012345678901
    author_id = 567890123456789012
    asyncio.run(headsup.record_role_dm(message_id, guild_id, author_id))
    assert asyncio.run(headsup.source_guild_for_role_dm(message_id, author_id)) == guild_id
    assert asyncio.run(headsup.source_guild_for_role_dm(message_id, 678901234567890123)) == 0
    assert asyncio.run(headsup.source_guild_for_role_dm(message_id + 1, author_id)) == 0
