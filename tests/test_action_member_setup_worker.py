from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import discord
import pytest

from stoney_verify import action_member_setup_worker as delivery
from stoney_verify import action_member_setup_automation as automation
from stoney_verify.action_builder_policy import ActionBuilderDraft, edit_rule, ACTION_BUILDER_KEY
from stoney_verify.action_schedule_policy import ActionSchedule
from stoney_verify.action_delivery_readiness import ActionDeliveryReadiness
from stoney_verify.action_delivery_preflight import DeliveryPreflight
from stoney_verify.action_member_consent import MemberSetupOptInPage


class FakeGuild:
    def __init__(self, guild_id=123):
        self.id = guild_id
        self.members = {}
        self.fetched = []
        self.fetch_result = None

    def get_member(self, uid):
        return self.members.get(uid)

    async def fetch_member(self, uid):
        self.fetched.append(uid)
        if self.fetch_result is None:
            raise discord.NotFound(SimpleNamespace(status=404, reason="Not Found"), "Not Found")
        return self.fetch_result


class FakeMember:
    def __init__(self, guild, uid=456):
        self.guild = guild
        self.id = uid
        self.bot = False
        self.joined_at = datetime(2024, 10, 1, tzinfo=timezone.utc)
        self.messages = []

    async def send(self, text, *, allowed_mentions):
        self.messages.append((text, allowed_mentions))


def _valid_cfg(enabled=True):
    d = edit_rule(
        ActionBuilderDraft(), condition="member_setup_revision",
        included=True, severity="recommended", note="",
    )
    return {
        "source": "supabase:guild_configs",
        ACTION_BUILDER_KEY: d.to_payload(),
        "member_setup_state": {
            "enabled": True, "current_revision": 3,
            "history": [{"revision": 3, "published_at": "2026-10-22T10:00:00+00:00"}],
        },
        automation.KEY: automation.MemberSetupAutomation(enabled).to_payload(),
    }


def _set_owners(monkeypatch, guild, *, send_allowed=True, claim=True):
    cfg = _valid_cfg()
    schedule = ActionSchedule(timezone="UTC", quiet_start=0, quiet_end=0)
    calls = []

    async def load(gid):
        assert gid == guild.id
        return cfg, automation.MemberSetupAutomation(True)

    async def sched(gid, *, refresh):
        assert gid == guild.id and refresh
        return cfg, schedule

    async def readiness(member, *, expected_revision):
        assert expected_revision == 3
        calls.append("readiness")
        return ActionDeliveryReadiness(
            status="preview_due", reason="private ledger due",
            revision=3, attempts=0, daily_count=0, ledger_checked=True,
        )

    async def final(member, *, expected_revision):
        calls.append("final")
        return DeliveryPreflight(
            "candidate_preview" if send_allowed else "opted_out",
            "fresh owner / consent checked", revision=expected_revision,
        )

    def claim_fn(gid, uid, revision, **limits):
        calls.append(("claim", gid, uid, revision, limits))
        return claim

    monkeypatch.setattr(delivery.discord, "Member", FakeMember)
    monkeypatch.setattr(delivery, "load_automation", load)
    monkeypatch.setattr(delivery, "load_schedule", sched)
    monkeypatch.setattr(delivery, "inspect_member_setup_readiness", readiness)
    monkeypatch.setattr(delivery, "inspect_member_setup_candidate", final)
    monkeypatch.setattr(delivery, "reserve_member_setup_sync", claim_fn)
    monkeypatch.setattr(delivery, "_utcnow", lambda: datetime(2026, 10, 24, 16, 0, tzinfo=timezone.utc))
    return calls


def test_claim_before_send_and_rechecks_consent_after_claim(monkeypatch):
    guild = FakeGuild()
    member = FakeMember(guild)
    guild.members[member.id] = member
    calls = _set_owners(monkeypatch, guild)
    result = asyncio.run(delivery.dispatch_member_setup_automatic(member, expected_revision=3))
    assert result == delivery.AutoDeliveryResult("sent", claimed=True, sent=True)
    assert len(member.messages) == 1
    assert calls.count("readiness") == 2
    assert calls.index("final") > next(i for i,x in enumerate(calls) if isinstance(x, tuple))
    assert all(isinstance(text, str) and "My Member Setup" in text for text, _ in member.messages)
    assert not member.messages[0][1].everyone

    calls = _set_owners(monkeypatch, guild, send_allowed=False)
    member.messages.clear()
    result = asyncio.run(delivery.dispatch_member_setup_automatic(member, expected_revision=3))
    assert result.status == "cancelled" and result.claimed and not result.sent
    assert member.messages == []


def test_claim_denied_or_owner_outage_cannot_send(monkeypatch):
    guild = FakeGuild()
    member = FakeMember(guild)
    guild.members[member.id] = member
    _set_owners(monkeypatch, guild, claim=False)
    assert asyncio.run(delivery.dispatch_member_setup_automatic(member, expected_revision=3)).status == "claim_denied"
    assert not member.messages

    async def down(gid):
        raise RuntimeError("sensitive DB error")
    monkeypatch.setattr(delivery, "load_automation", down)
    result = asyncio.run(delivery.dispatch_member_setup_automatic(member, expected_revision=3))
    assert result.status == "unavailable"
    assert not member.messages


def test_member_cache_must_match_and_optin_fetched_member_rechecked_postclaim(monkeypatch):
    guild = FakeGuild()
    member = FakeMember(guild)
    calls = _set_owners(monkeypatch, guild)
    assert asyncio.run(delivery.dispatch_member_setup_automatic(member, expected_revision=3)).status == "not_cached"
    guild.fetch_result = FakeMember(guild, member.id)
    result = asyncio.run(delivery.dispatch_member_setup_automatic(
        member, expected_revision=3, fetched_from_discord=True,
    ))
    assert result.sent
    assert len(guild.fetched) == 1
    assert len(guild.fetch_result.messages) == 1
    assert member.messages == []


def test_worker_bounded_optin_only_scans_roundrobin_and_restart_claim_survival(monkeypatch):
    guild = FakeGuild()
    member = FakeMember(guild, 456)
    guild.members[456] = member
    _set_owners(monkeypatch, guild)
    seen = []

    async def page(gid, *, after_user_id, limit):
        assert gid == 123 and limit == 5
        seen.append(after_user_id)
        return MemberSetupOptInPage((456,), None)

    async def fake_send(member, *, expected_revision, fetched_from_discord):
        return delivery.AutoDeliveryResult("sent", claimed=True, sent=True)

    monkeypatch.setattr(delivery, "read_member_setup_opt_in_page", page)
    monkeypatch.setattr(delivery, "dispatch_member_setup_automatic", fake_send)
    worker = delivery.MemberSetupReminderWorker(SimpleNamespace(guilds=[guild]))
    assert asyncio.run(worker.run_pass()) == 1
    assert worker.counters.sent == 1
    assert worker.guild_counters[123].sent == 1
    assert worker.member_cursors[123] == 0
    assert seen == [0]
    assert delivery.MAX_CLAIMS_PER_PASS <= 4
    assert delivery.MAX_TARGETED_MEMBER_FETCHES_PER_PASS <= 8
    # The actual atomic PostgreSQL ledger is never reset by a process reboot.
    assert "reserve_member_setup_sync" in Path(delivery.__file__).read_text()
    assert "reset" not in Path(delivery.__file__).read_text().split("reserve_member_setup_sync,")[1].split("\n", 2)[0]


def test_rest_quota_exhaustion_does_not_skip_candidate(monkeypatch):
    guild = FakeGuild()
    _set_owners(monkeypatch, guild)
    worker = delivery.MemberSetupReminderWorker(SimpleNamespace(guilds=[guild]))
    worker.remaining_targeted_fetches = 0
    async def page(gid, *, after_user_id, limit):
        return MemberSetupOptInPage((456, 457), None)
    monkeypatch.setattr(delivery, "read_member_setup_opt_in_page", page)
    assert asyncio.run(worker.scan_guild(guild, 4)) == 0
    assert worker.member_cursors[123] == 0
    assert not guild.fetched


def test_do_not_create_verification_sender_or_duplicate_worker():
    code = Path(delivery.__file__).read_text()
    assert "pending_verification" not in code
    assert "claim_sync as _pg_claim_sync" not in code
    assert "guild.fetch_members(" not in code
    assert "MAX_TARGETED_MEMBER_FETCHES_PER_PASS = 8" in code
    assert "await self.bot.wait_until_ready()" in code
    assert "STARTUP_DELAY_SECONDS = 120" in code
    assert "POLL_SECONDS = 180" in code
    assert "await asyncio.sleep(POLL_SECONDS)" in code
    assert "create_task(" in code
    assert "_WORKER.guild_counters.get(guild_id)" in code
