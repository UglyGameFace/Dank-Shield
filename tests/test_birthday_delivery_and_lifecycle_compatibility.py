from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
import pytest

from stoney_verify import birthday_runtime as runtime, birthday_service as service
from stoney_verify import exit_card_service
from stoney_verify.commands_ext.public_setup_logs import _join_leave_alias_payload
from stoney_verify.commands_ext import public_birthdays as birthdays


NOW = datetime(2026, 6, 20, 9, tzinfo=timezone.utc)


class Clock(datetime):
    @classmethod
    def now(cls, tz=None):
        return NOW.astimezone(tz) if tz else NOW.replace(tzinfo=None)


@pytest.fixture
def worker(monkeypatch):
    events = []

    class Member:
        id = 456
        bot = False
        display_name = "Birthday Member"
        display_avatar = SimpleNamespace(url="https://example.com/avatar.png")

        def __init__(self):
            self.roles = []
            self.messages = []

        async def add_roles(self, role, **kwargs):
            events.append(("grant", role.id))
            self.roles.append(role)

        async def remove_roles(self, role, **kwargs):
            events.append(("remove", role.id))
            self.roles.remove(role)

        async def send(self, *args, **kwargs):
            self.messages.append((args, kwargs))

    class Role:
        id = 987

    class Channel:
        id = 789

        def __init__(self):
            self.messages = []
            self.allowed = True

        def permissions_for(self, _):
            return SimpleNamespace(view_channel=self.allowed, send_messages=self.allowed, embed_links=self.allowed)

        async def send(self, **kwargs):
            self.messages.append(kwargs)

    member, channel, role = Member(), Channel(), Role()

    class Guild:
        id = 123
        name = "Test Server"
        me = Member()

        def get_channel(self, channel_id):
            return channel if channel_id == channel.id else None

        def get_role(self, role_id):
            return role if role_id == role.id else None

        def get_member(self, user_id):
            return member if user_id == member.id else None

    guild = Guild()
    bot = SimpleNamespace(get_guild=lambda gid: guild if gid == guild.id else None)
    settings = dict(guild_id="123", enabled=True, timezone="UTC", channel_id="789",
                    role_id="987", announce_hour=9, leap_day_policy="feb28", updated_at="revision-1")
    claim = {**settings, "last_local_date": "2026-06-20"}
    row = dict(user_id="456", birth_month=6, birth_day=20, birth_year=2002,
               public_wishes=True, birthday_role=True, dm_wishes=True)
    ledger = set()

    async def reserve(gid, uid, day, kind):
        key = (gid, uid, day, kind)
        if key in ledger:
            return False
        ledger.add(key)
        return True

    async def lease(gid, uid, role_id, expires):
        events.append(("lease", role_id, expires))

    monkeypatch.setattr(runtime, "discord", SimpleNamespace(
        Guild=Guild, Member=Member, Role=Role, TextChannel=Channel,
        Embed=discord.Embed, Color=discord.Color, utils=discord.utils,
        AllowedMentions=discord.AllowedMentions, Forbidden=discord.Forbidden,
        HTTPException=discord.HTTPException, NotFound=discord.NotFound,
    ))
    monkeypatch.setattr(runtime, "datetime", Clock)
    monkeypatch.setattr(runtime, "get_guild", AsyncMock(return_value=settings))
    monkeypatch.setattr(runtime, "members_for_day", AsyncMock(return_value=[row]))
    monkeypatch.setattr(service, "get_member", AsyncMock(return_value=row))
    monkeypatch.setattr(runtime, "claim_delivery", reserve)
    monkeypatch.setattr(runtime, "save_role_lease", lease)
    monkeypatch.setattr(runtime, "_manageable_role", lambda *args: True)
    return SimpleNamespace(bot=bot, guild=guild, member=member, channel=channel,
                           role=role, settings=settings, claim=claim, row=row,
                           ledger=ledger, events=events)


def test_worker_reserves_once_keeps_year_private_and_leases_before_grant(worker):
    async def run():
        await runtime._deliver_guild(worker.bot, worker.claim)
        await runtime._deliver_guild(worker.bot, worker.claim)
    asyncio.run(run())
    assert len(worker.channel.messages) == len(worker.member.messages) == 1
    assert "2002" not in str(worker.channel.messages[0]["embed"].to_dict())
    assert worker.channel.messages[0]["allowed_mentions"].to_dict() == {"parse": []}
    assert worker.events[0] == ("lease", 987, datetime(2026, 6, 21, tzinfo=timezone.utc))
    assert worker.events[1] == ("grant", 987)
    assert len(worker.ledger) == 3


def test_worker_rechecks_consent_before_any_side_effect(worker, monkeypatch):
    monkeypatch.setattr(service, "get_member", AsyncMock(return_value=None))
    asyncio.run(runtime._deliver_guild(worker.bot, worker.claim))
    assert not worker.channel.messages and not worker.member.messages
    assert not worker.events and not worker.ledger


def test_worker_keeps_dm_and_role_delivery_when_channel_permissions_are_missing(worker):
    worker.channel.allowed = False
    asyncio.run(runtime._deliver_guild(worker.bot, worker.claim))
    assert not worker.channel.messages
    assert len(worker.member.messages) == 1 and worker.member.roles == [worker.role]


def test_worker_rejects_changed_settings_or_old_claim(worker):
    worker.claim["updated_at"] = "stale-revision"
    asyncio.run(runtime._deliver_guild(worker.bot, worker.claim))
    assert not worker.ledger
    worker.claim["updated_at"] = "revision-1"
    worker.claim["last_local_date"] = "2026-06-19"
    asyncio.run(runtime._deliver_guild(worker.bot, worker.claim))
    assert not worker.ledger


@pytest.mark.parametrize("can_remove", [False, True])
def test_expired_role_lease_survives_unavailable_permissions(worker, monkeypatch, can_remove):
    worker.member.roles.append(worker.role)
    clear = AsyncMock()
    monkeypatch.setattr(runtime, "clear_role_lease", clear)
    monkeypatch.setattr(runtime, "_can_remove_role", lambda *args: can_remove)
    asyncio.run(runtime._remove_expired_role(worker.bot, dict(guild_id=123, user_id=456, role_id=987)))
    assert clear.called == can_remove
    assert (worker.role in worker.member.roles) != can_remove


@pytest.mark.parametrize("shape", ["flat", "settings", "config", "metadata", "meta"])
def test_log_edit_preserves_legacy_goodbye_cards_text_and_other_settings(shape):
    old = dict(leave_channel_id="11", welcome_exit_channel_id="12", welcome_leave_enabled=True,
               modlog_channel_id="13", welcome_card_theme="galaxy", arbitrary_setting="keep")
    current = old if shape == "flat" else {shape: old}
    patch = _join_leave_alias_payload("99", current=current)
    assert patch["join_leave_log_channel_id"] == "99"
    assert patch["exit_card_channel_id"] == "12" and patch["exit_card_enabled"]
    for key in ("leave_channel_id", "welcome_exit_channel_id", "welcome_leave_channel_id",
                "welcome_exit_log_channel_id", "modlog_channel_id", "welcome_card_theme", "arbitrary_setting"):
        assert key not in patch
    merged = {**old, **patch}
    assert merged["leave_channel_id"] == "11" and merged["arbitrary_setting"] == "keep"


def test_log_edit_does_not_enable_unconfigured_exit_cards_and_preserves_explicit_choices():
    patch = _join_leave_alias_payload("99", current={})
    assert patch["exit_card_enabled"] is False
    explicit = dict(exit_card_channel_id="55", exit_card_enabled=False, leave_channel_id="11")
    patch = _join_leave_alias_payload("99", current=explicit)
    assert "exit_card_enabled" not in patch and "exit_card_channel_id" not in patch
    assert not exit_card_service.exit_cards_enabled({**explicit, **patch})
    with pytest.raises(RuntimeError, match="nothing was saved"):
        _join_leave_alias_payload("99", current={"source": "unavailable:connection"})


def test_upcoming_birthdays_lists_each_member_once(monkeypatch):
    async def run():
        from test_production_interaction_stabilization import interaction
        event = interaction()
        event.guild.get_member = lambda uid: SimpleNamespace(bot=False, display_name="Birthday Member")
        monkeypatch.setattr(birthdays, "datetime", Clock)
        monkeypatch.setattr(birthdays, "get_guild", AsyncMock(return_value={"enabled": True, "timezone": "UTC"}))
        monkeypatch.setattr(birthdays, "upcoming", AsyncMock(return_value=[
            dict(user_id="456", birth_month=6, birth_day=20),
        ]))
        await birthdays.birthday_upcoming.callback(event)
        embed = event.edit_original_response.call_args.kwargs["embed"]
        assert embed.description.count("Birthday Member") == 1
    asyncio.run(run())
