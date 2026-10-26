"""Behavioral regression for large-server restart activity reconciliation.

All tests use in-memory Discord doubles: no Discord API or production DB writes.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from stoney_verify.members_new import activity_reconciliation as reconciliation


class FakeMember:
    pass


class FakeChannel:
    def __init__(self, channel_id: int):
        self.id = channel_id
        self.name = f"channel-{channel_id}"

    def permissions_for(self, member):
        return SimpleNamespace(
            view_channel=True,
            read_message_history=True,
        )

    def history(self, **kwargs):
        async def messages():
            if False:
                yield None

        return messages()


class FakeGuild:
    def __init__(self, channel_count: int):
        self.id = 12345
        self.me = FakeMember()
        self.channels = [
            FakeChannel(index + 1000)
            for index in range(channel_count)
        ]
        self.threads = []
        self.roles = []


def test_large_guild_reconciles_all_channels_without_network(monkeypatch):
    """The old code rejected 101 channels before reading any history."""
    monkeypatch.delenv("DANK_ACTIVITY_RECONCILE_MAX_CHANNELS", raising=False)
    monkeypatch.setattr(reconciliation.discord, "Member", FakeMember)

    calls = []

    async def paced_reservation(weight, *, label):
        calls.append((weight, label))

    monkeypatch.setattr(
        reconciliation, "reserve_recovery_discord_rest_requests",
        paced_reservation,
    )
    now = datetime.now(timezone.utc)
    result = asyncio.run(reconciliation.reconcile_restart_gap(
        FakeGuild(125),
        after=now - timedelta(minutes=3),
        before=now,
    ))
    assert result.scanned_channels == 125
    assert result.scanned_messages == 0
    assert result.latest_by_user == {}
    # Every history pass still reserves a weighted slot. No unbounded fanout.
    assert len(calls) == 125
    assert all(weight > 0 for weight, _ in calls)


def test_large_guild_timeout_scales_without_unbounded_deadline(monkeypatch):
    monkeypatch.delenv("DANK_ACTIVITY_RECONCILE_TIMEOUT_SECONDS", raising=False)
    assert reconciliation.reconcile_timeout_seconds() == 180
    assert reconciliation.reconcile_timeout_seconds(FakeGuild(125)) > 180
    assert reconciliation.reconcile_timeout_seconds(FakeGuild(2500)) == 1200


def test_channel_ceiling_still_fails_closed_and_respects_override(monkeypatch):
    monkeypatch.setattr(reconciliation.discord, "Member", FakeMember)
    monkeypatch.setenv("DANK_ACTIVITY_RECONCILE_MAX_CHANNELS", "110")
    now = datetime.now(timezone.utc)
    with pytest.raises(RuntimeError, match="more than 110 readable channels"):
        asyncio.run(reconciliation._collect_messageables(
            FakeGuild(111),
            after=now - timedelta(seconds=30),
        ))
    assert reconciliation._max_reconcile_channels() == 110
