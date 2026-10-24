from __future__ import annotations

import asyncio
from datetime import date, datetime, timezone
from pathlib import Path

import discord
import pytest

from stoney_verify import action_schedule_policy as policy
from stoney_verify import action_center_postgres as ledger
from stoney_verify.commands_ext import public_action_schedule as ui
from stoney_verify.commands_ext import public_action_builder as builder


def dt(iso):
    return datetime.fromisoformat(iso.replace("Z", "+00:00"))


def test_policy_defaults_not_enabled_and_roundtrip():
    cfg = policy.parse_schedule({})
    assert cfg.enabled is False
    assert cfg.max_attempts == 2
    assert cfg.to_payload()["delivery"] == "preview_only"
    next_cfg = policy.edit_schedule(
        cfg, first_delay_hours=48, repeat_hours=168, max_attempts=2,
        quiet_start=22, quiet_end=8, timezone="America/New_York",
        max_guild_per_day=3,
    )
    assert next_cfg.revision == 1
    assert policy.parse_schedule({policy.KEY: next_cfg.to_payload()}) == next_cfg


def test_fail_closed_on_wrong_schema_and_attempt_to_activate():
    defaults = policy.ActionSchedule()
    for bad in (
        {"version": 2, "enabled": False},
        {"version": 1, "enabled": True, "delivery": "preview_only"},
        {"version": 1, "enabled": False, "delivery": "dm"},
    ):
        with pytest.raises(ValueError):
            policy.parse_schedule({policy.KEY: bad})
    for unexpected in ({"first_delay_hours": 1}, {"repeat_hours": 1},
                       {"max_attempts": 5}, {"max_guild_per_day": 11},
                       {"quiet_start": 24}, {"timezone": "Mars/Base"},
                       {"enabled": True}):
        with pytest.raises(ValueError):
            policy.ActionSchedule(**{**vars(defaults), **unexpected})


def test_quiet_hours_handle_cross_midnight_and_dst():
    assert policy.in_quiet_hours(23,22,8) is True
    assert policy.in_quiet_hours(3,22,8) is True
    assert policy.in_quiet_hours(8,22,8) is False
    assert policy.in_quiet_hours(16,22,8) is False
    assert policy.in_quiet_hours(16,0,0) is False

    cfg = policy.ActionSchedule(
        first_delay_hours=12,timezone="America/New_York",
        quiet_start=22,quiet_end=8,
    )
    # In the autumn fallback, both distinct 1 AM instants stay quiet.
    for utc in ("2026-11-01T05:30:00Z","2026-11-01T06:30:00Z"):
        decision = policy.preview_candidate(
            cfg,condition="member_setup_revision", owner_status="pending",
            now=dt(utc),joined_at=dt("2026-10-30T00:00:00Z"),
            condition_started_at=dt("2026-10-30T00:00:00Z"),
        )
        assert decision.status == "quiet"
        assert decision.next_at and decision.next_at > dt(utc)
        assert not policy.in_quiet_hours(
            decision.next_at.astimezone(__import__("zoneinfo").ZoneInfo(cfg.timezone)).hour,
            cfg.quiet_start,cfg.quiet_end,
        )


def test_canonical_completion_and_unavailable_never_suggest_due():
    schedule = policy.ActionSchedule()
    kwargs = dict(
        condition="member_setup_revision", now=dt("2026-10-23T20:00:00Z"),
        joined_at=dt("2026-10-20T01:00:00Z"),
        condition_started_at=dt("2026-10-21T01:00:00Z"),
    )
    assert policy.preview_candidate(schedule, owner_status="complete", **kwargs).status=="complete"
    assert policy.preview_candidate(schedule, owner_status="unavailable", **kwargs).status=="blocked"
    assert policy.preview_candidate(schedule, owner_status="unknown", **kwargs).status=="blocked"
    assert policy.preview_candidate(schedule, owner_status="pending", included=False, **kwargs).status=="excluded"
    assert policy.preview_candidate(schedule, owner_status="pending", attempts=2, **kwargs).status=="exhausted"
    assert policy.preview_candidate(schedule, owner_status="pending", guild_claims_today=5, **kwargs).status=="capped"
    assert policy.preview_candidate(schedule, owner_status="pending", joined_at=None,
                                    condition="member_setup_revision",now=kwargs["now"]).status=="blocked"


def test_delay_and_repeat_cooldown_are_distinct():
    cfg = policy.ActionSchedule(timezone="UTC", quiet_start=0, quiet_end=0)
    base = dict(condition="member_setup_revision", owner_status="pending",
                joined_at=dt("2026-10-20T00:00:00Z"),
                condition_started_at=dt("2026-10-20T00:00:00Z"))
    early = policy.preview_candidate(cfg,now=dt("2026-10-20T12:00:00Z"),**base)
    assert early.status == "waiting"
    assert early.next_at == dt("2026-10-21T00:00:00Z")
    pending = policy.preview_candidate(cfg,now=dt("2026-10-23T12:00:00Z"),
                                       last_claimed_at=dt("2026-10-22T12:00:00Z"),attempts=1,**base)
    assert pending.status == "waiting"
    assert pending.next_at == dt("2026-10-25T12:00:00Z")
    result=policy.preview_candidate(cfg,now=dt("2026-10-27T12:00:00Z"),**base)
    assert result.status == "preview_due"
    assert "no sending" in result.reason


def test_new_member_setup_revision_starts_after_publication_not_join():
    cfg = policy.ActionSchedule(timezone="UTC",quiet_start=0,quiet_end=0)
    base = dict(condition="member_setup_revision",owner_status="pending",
                joined_at=dt("2025-01-01T00:00:00Z"))
    decision=policy.preview_candidate(
        cfg,now=dt("2026-10-23T12:00:00Z"),
        condition_started_at=dt("2026-10-23T10:00:00Z"),**base,
    )
    assert decision.status=="waiting"
    assert decision.next_at==dt("2026-10-24T10:00:00Z")
    missing=policy.preview_candidate(cfg,now=dt("2026-10-23T12:00:00Z"),**base)
    assert missing.status=="blocked"
    assert "published" in missing.reason


def test_per_guild_schedule_uses_atomic_cas(monkeypatch):
    calls=[]
    async def fake(guild,key,*,expected,value,source):
        calls.append((guild,key,expected,value,source))
        return True,{key:value}
    from stoney_verify import guild_config
    monkeypatch.setattr(guild_config,"compare_and_swap_guild_config_key",fake)
    config={"source":"test"}
    next_cfg=policy.edit_schedule(policy.ActionSchedule(),first_delay_hours=24,
                                  repeat_hours=72,max_attempts=2,quiet_start=22,
                                  quiet_end=8,timezone="UTC",max_guild_per_day=5)
    ok,saved=asyncio.run(policy.save_schedule(123,expected_config=config,schedule=next_cfg))
    assert ok
    assert calls[0][:3]==(123,policy.KEY,None)
    assert calls[0][3]["enabled"] is False
    assert calls[0][4]=="action_center_schedule_draft"
    with pytest.raises(ValueError,match="Stale"):
        asyncio.run(policy.save_schedule(123,expected_config=config,schedule=policy.ActionSchedule(revision=2)))
    assert len(calls)==1


def test_staff_buttons_and_no_scheduler_or_dm():
    v=ui.ScheduleView(42,123,policy.ActionSchedule())
    labels={child.label for child in v.children if isinstance(child,discord.ui.Button)}
    assert labels=={"Enable Automatic Setup DMs","Edit Timing & Limits","Preview Timing","Check Delivery Readiness","Preview Opted-in Members","Owner Canary Console","Back to Action Builder"}
    b=builder.ActionBuilderView(42,123,__import__("stoney_verify.action_builder_policy",fromlist=["ActionBuilderDraft"]).ActionBuilderDraft())
    assert "Safety Schedule (Draft)" in {x.label for x in b.children if isinstance(x,discord.ui.Button)}
    for path in (policy.__file__,ui.__file__):
        txt=Path(path).read_text()
        assert "create_task(" not in txt
        assert "member.send(" not in txt
        assert "channel.send(" not in txt
    assert "ledger_ready_sync" not in Path(ui.__file__).read_text()


def test_postgres_schema_is_staged_and_does_not_touch_verification_claims():
    root=Path(__file__).resolve().parents[1]
    sql=(root/"postgres/migrations/202610230001_action_center_delivery_reservations.sql").read_text().lower()
    assert "create table if not exists public.dank_action_center_delivery" in sql
    assert "create table if not exists public.dank_action_center_daily_budget" in sql
    assert "check (claims between 0 and 10)" in sql
    assert "check (attempts between 1 and 3)" in sql
    assert "dank_verify_reminder_delivery" not in sql
    assert "drop table" not in sql
    assert "alter table" not in sql


def test_claim_adapter_refuses_verification_and_invalid_attempts(monkeypatch):
    def fail():
        raise AssertionError("Should not connect for invalid claim")
    monkeypatch.setattr(ledger,"_connect",fail)
    for args in (
        (0,123,1), (123,0,1), (123,123,0), (123,123,True),
    ):
        assert not ledger.reserve_member_setup_sync(
            *args,local_day=date(2026,10,23),cooldown_hours=72,max_attempts=2,max_guild_per_day=5
        )
    assert not ledger.reserve_member_setup_sync(
        123,456,1,local_day=date(2026,10,23),
        cooldown_hours=2,max_attempts=2,max_guild_per_day=5,
    )
    source=Path(ledger.__file__).read_text()
    assert 'MEMBER_SETUP_CONDITION = "member_setup_revision"' in source
    assert "pending_verification" not in source
    assert "pg_advisory_xact_lock" in source
    assert "raise _BudgetFull()" in source
    assert "return False" in source
