from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import discord
import pytest

from stoney_verify import action_delivery_readiness as readiness
from stoney_verify import action_center_postgres as ledger
from stoney_verify.action_builder_policy import ActionBuilderDraft, edit_rule
from stoney_verify.action_delivery_preflight import DeliveryPreflight
from stoney_verify.action_schedule_policy import ActionSchedule
from stoney_verify.commands_ext import public_action_schedule as ui

NOW = datetime(2026, 10, 23, 20, tzinfo=timezone.utc)
START = datetime(2026, 10, 21, 10, tzinfo=timezone.utc)


def sample_member(monkeypatch):
    class Member:
        id = 601
        bot = False
        joined_at = datetime(2026, 10, 1, tzinfo=timezone.utc)
        guild = SimpleNamespace(id=501)
    monkeypatch.setattr(readiness.discord, "Member", Member)
    return Member()


def configure(monkeypatch, *, snapshot=None, prechecks=None, second_schedule=None, included=True):
    primary = ActionSchedule(timezone="UTC", quiet_start=0, quiet_end=0)
    new_schedule = second_schedule or primary
    states = list(prechecks or ["candidate_preview", "candidate_preview"])
    seen = []
    async def preflight(member, *, expected_revision, now):
        seen.append("owner")
        return DeliveryPreflight(states.pop(0), "Source still pending", expected_revision,
                                 condition_started_at=START)
    configs = [({"source":"ok"}, primary), ({"source":"ok"}, new_schedule)]
    async def load(gid, *, refresh=True):
        seen.append("guild")
        return configs.pop(0)
    draft = edit_rule(ActionBuilderDraft(), condition="member_setup_revision",
                      included=included, severity="recommended", note="")
    def read(gid, uid, rev, *, local_day):
        seen.append("ledger")
        assert (gid, uid, rev, local_day.isoformat()) == (501, 601, 3, "2026-10-23")
        if isinstance(snapshot, Exception):
            raise snapshot
        return snapshot or ledger.ClaimSnapshot()
    monkeypatch.setattr(readiness, "inspect_member_setup_candidate", preflight)
    monkeypatch.setattr(readiness, "load_schedule", load)
    monkeypatch.setattr(readiness, "parse_action_builder", lambda cfg: draft)
    monkeypatch.setattr(readiness, "read_member_setup_claim_snapshot_sync", read)
    return seen


def run(member):
    return asyncio.run(readiness.inspect_member_setup_readiness(
        member, expected_revision=3, now=NOW,
    ))


def test_real_ledger_snapshot_due_is_still_never_delivery_authorization(monkeypatch):
    member = sample_member(monkeypatch)
    seen = configure(monkeypatch)
    output = run(member)
    assert output.status == "preview_due"
    assert output.ledger_checked
    assert output.delivery_authorized is False
    assert output.attempts == 0
    assert output.daily_count == 0
    assert seen == ["owner", "guild", "ledger", "owner", "guild"]


def test_repeat_cooldown_uses_real_postgres_claim_time(monkeypatch):
    member = sample_member(monkeypatch)
    claim = ledger.ClaimSnapshot(1, NOW - timedelta(hours=2), 1)
    configure(monkeypatch, snapshot=claim)
    output = run(member)
    assert output.status == "waiting"
    assert output.attempts == 1
    assert output.next_at == NOW + timedelta(hours=70)
    assert not output.delivery_authorized


def test_claim_limit_and_daily_server_cap_are_real(monkeypatch):
    member = sample_member(monkeypatch)
    configure(monkeypatch, snapshot=ledger.ClaimSnapshot(2, NOW-timedelta(days=7), 2))
    assert run(member).status == "exhausted"
    configure(monkeypatch, snapshot=ledger.ClaimSnapshot(0, None, 5))
    assert run(member).status == "capped"


def test_optout_or_completed_requirement_short_circuits_without_ledger(monkeypatch):
    member = sample_member(monkeypatch)
    for state in ("opted_out", "complete", "stale", "unavailable", "exempt"):
        seen = configure(monkeypatch, prechecks=[state])
        output = run(member)
        assert output.status == state
        assert seen == ["owner"]
        assert not output.ledger_checked


def test_member_optout_after_snapshot_and_policy_drift_fail_closed(monkeypatch):
    member = sample_member(monkeypatch)
    seen = configure(monkeypatch, prechecks=["candidate_preview", "opted_out"])
    output = run(member)
    assert output.status == "opted_out"
    assert output.ledger_checked
    assert not output.delivery_authorized
    assert seen == ["owner", "guild", "ledger", "owner"]
    altered = ActionSchedule(timezone="UTC", quiet_start=21, quiet_end=8)
    configure(monkeypatch, second_schedule=altered)
    output = run(member)
    assert output.status == "stale"
    assert not output.delivery_authorized


def test_missing_or_offline_private_4c_migration_blocks_preview(monkeypatch):
    member = sample_member(monkeypatch)
    seen = configure(monkeypatch, snapshot=ledger.ActionLedgerUnavailable("secrets"))
    output = run(member)
    assert output.status == "ledger_unavailable"
    assert not output.ledger_checked
    assert not output.delivery_authorized
    assert "secrets" not in output.reason
    assert seen == ["owner", "guild", "ledger"]


def test_invalid_clock_and_disabled_rule_do_not_read_postgres(monkeypatch):
    member = sample_member(monkeypatch)
    seen = configure(monkeypatch)
    output = asyncio.run(readiness.inspect_member_setup_readiness(
        member, expected_revision=3, now=datetime(2026,10,23,20),
    ))
    assert output.status == "blocked"
    assert not seen
    seen = configure(monkeypatch, included=False)
    assert run(member).status == "excluded"
    assert seen == ["owner", "guild"]


def test_raw_claim_snapshot_never_treats_invalid_counters_as_safe():
    for kwargs in (
        {"attempts": -1}, {"attempts": 4},
        {"guild_claims_today": 11},
        {"attempts": 1},
        {"last_claimed_at": NOW},
        {"attempts": 1, "last_claimed_at": NOW.replace(tzinfo=None)},
    ):
        with pytest.raises(ValueError):
            ledger.ClaimSnapshot(**kwargs)
    with pytest.raises(ValueError):
        ledger.read_member_setup_claim_snapshot_sync(
            0, 123, 1, local_day=NOW.date()
        )


def test_staff_readiness_ui_has_no_unrequested_send_or_claim():
    view = ui.ScheduleView(42, 501, ActionSchedule())
    labels = {c.label for c in view.children if isinstance(c, discord.ui.Button)}
    assert "Check Delivery Readiness" in labels
    source = Path(readiness.__file__).read_text()
    ui_code = Path(ui.__file__).read_text()
    for forbidden in (
        "reserve_member_setup_sync(", ".send(", "create_task(",
        "bot.guilds", "discord.app_commands",
    ):
        assert forbidden not in source
    assert "inspect_member_setup_readiness" in ui_code
    assert "delivery_authorized: bool = False" in source
