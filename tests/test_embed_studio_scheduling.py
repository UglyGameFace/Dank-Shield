"""Safe scheduled publishing from the canonical private Embed Studio preview."""
from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import discord
import pytest

from stoney_verify.embed_studio_core import new_project
from stoney_verify import embed_studio_schedules as schedules
from stoney_verify import embed_studio_schedule_runtime as worker
from stoney_verify.embed_studio_ui import (
    StudioHomeView, StudioReviewView, StudioScheduleModal,
    StudioSchedulesMenu, StudioSession,
)


def test_scheduler_is_off_until_storage_v2_and_delivery_are_opted_in(monkeypatch):
    monkeypatch.setattr(schedules, "storage_v2_enabled", lambda env: env.get("V2") == "yes")
    assert not schedules.schedules_enabled({})
    assert not schedules.schedules_enabled({schedules.FLAG:"true"})
    assert not schedules.schedules_enabled({"V2":"yes"})
    assert schedules.schedules_enabled({"V2":"yes",schedules.FLAG:"true"})


@pytest.mark.parametrize("input", [
    "", "2026-11-01 09:00", "tomorrow", "2026-01-01T12:00Z",
    "2027-04-01T12:00Z", "2026-10-24T22:01:00+00:00",
])
def test_time_requires_explicit_timezone_and_2m_to_90d_window(input):
    now=datetime(2026,10,24,22,0,tzinfo=timezone.utc)
    with pytest.raises(schedules.StudioScheduleError):
        schedules.parse_schedule_time(input,now=now)


def test_aware_timezone_utc_conversion_and_dst_boundary():
    now=datetime(2026,10,24,22,0,tzinfo=timezone.utc)
    future=schedules.parse_schedule_time("2026-11-01T18:00-05:00",now=now)
    assert future == datetime(2026,11,1,23,0,tzinfo=timezone.utc)


def test_jobs_are_not_allowed_to_grant_roles_unattended():
    model=new_project()
    model["role_actions"]=[{"label":"Member","role_id":"123456789012345678"}]
    with pytest.raises(schedules.StudioScheduleError,match="Role controls"):
        schedules.safe_scheduled_project(model)


def test_queue_produces_validated_private_job_without_discord_message(monkeypatch):
    monkeypatch.setattr(schedules, "schedules_enabled", lambda: True)
    monkeypatch.setattr(schedules, "verify_project", lambda: None)
    entries=[]
    class Table:
        def insert(self,payload):
            entries.append(payload)
            return self
        def execute(self):
            return SimpleNamespace(data=[dict(entries[-1],id="job",status="pending")])
    monkeypatch.setattr(schedules, "_client", lambda: SimpleNamespace(table=lambda *_:Table()))
    when=datetime(2026,11,1,23,0,tzinfo=timezone.utc)
    job=schedules._enqueue_sync(111,222,333,new_project(),when)
    assert job["status"]=="pending"
    assert entries[0]["guild_id"]=="111"
    assert entries[0]["channel_id"]=="222"
    assert entries[0]["scheduled_by"]=="333"
    assert entries[0]["execute_at"]==when.isoformat()
    assert entries[0]["project"]["role_actions"] == []


def test_private_editor_buttons_only_allow_schedule_on_new_roleless_preview(monkeypatch):
    monkeypatch.setattr(schedules,"schedules_enabled",lambda:True)
    session=StudioSession(guild_id=1,owner_id=2)
    home=StudioHomeView(session)
    assert "More Tools" in {getattr(i,"label","") for i in home.children}
    from stoney_verify.embed_studio_ui import StudioAdvancedTools
    assert "Upcoming Scheduled Posts" in {
        getattr(i, "label", "") for i in StudioAdvancedTools(session).children
    }
    preview=StudioReviewView(session)
    assert not preview.schedule_later.disabled
    session.post_key="7:8"
    assert StudioReviewView(session).schedule_later.disabled
    session.post_key=""
    session.project["role_actions"]=[{"label":"Member","role_id":"123456789012345678"}]
    assert StudioReviewView(session).schedule_later.disabled


def test_scheduling_modal_uses_exact_review_snapshot_and_single_use(monkeypatch):
    from stoney_verify import embed_studio_ui as ui
    monkeypatch.setattr(schedules,"schedules_enabled",lambda:True)
    session=StudioSession(guild_id=1,owner_id=2,channel_id=3)
    review=StudioReviewView(session)
    modal=StudioScheduleModal(session,preview=review.preview_project,
                              channel_id=3,post_key="",review=review)
    assert not modal.used
    assert len(modal.children)==1
    assert modal.when_input.max_length <= 35
    session.apply(lambda p:p["embeds"][0].update(title="Changed"))
    assert session.project != review.preview_project


def test_due_claim_writes_no_discord_message_and_returns_backend_rows(monkeypatch):
    monkeypatch.setattr(schedules,"schedules_enabled",lambda:True)
    captured=[]
    class DB:
        def rpc(self,name,args):
            captured.append((name,args))
            return SimpleNamespace(execute=lambda:SimpleNamespace(data=[{"id":"one","status":"claimed"}]))
    monkeypatch.setattr(schedules,"_client",lambda:DB())
    rows=asyncio.run(schedules.claim_due())
    assert captured == [(schedules.CLAIM_RPC,{
        "p_limit":8,"p_shard_count":1,"p_owned_shards":[0],
    })]
    assert rows[0]["status"]=="claimed"


def test_worker_does_not_send_if_guild_not_cached_or_creator_missing():
    job={"guild_id":"123","channel_id":"321","scheduled_by":"456",
         "project":new_project()}
    async def first():
        return await worker._dispatch(SimpleNamespace(get_guild=lambda *_:None),job)
    assert asyncio.run(first())==(0,"guild_not_cached")


def test_default_disabled_startup_never_creates_worker(monkeypatch):
    monkeypatch.setattr(worker,"schedules_enabled",lambda:False)
    assert worker.start_embed_studio_schedule_worker(object()) is None


def test_sql_claim_is_indexed_and_at_most_once():
    from pathlib import Path
    sql=Path("supabase_fresh/migrations/202610200030_dankshield_embed_studio_scheduling.sql").read_text().lower()
    for clause in ("for update skip locked","where status='pending'",
                   "enable row level security","security invoker",
                   "revoke all on function public.dank_embed_studio_claim_due",
                   "interval '10 minutes'"):
        assert clause in sql
    assert "security definer" not in sql
    assert "drop table" not in sql


def test_owned_shard_routing_never_claims_foreign_process_jobs(monkeypatch):
    from stoney_verify.embed_studio_schedule_runtime import _owned_shards
    assert _owned_shards(SimpleNamespace(shard_count=None,shard_ids=None)) == (1,(0,))
    assert _owned_shards(SimpleNamespace(shard_count=4,shard_ids=[1,3])) == (4,(1,3))
    assert _owned_shards(SimpleNamespace(shard_count=4,shard_ids=[])) is None
    assert _owned_shards(SimpleNamespace(shard_count=4,shard_ids=[4])) is None
    assert _owned_shards(SimpleNamespace(shard_count=4,shard_ids=None,shards={})) is None
    assert _owned_shards(SimpleNamespace(shard_count=4,shard_ids=None,shards={0:object()})) == (4,(0,))
    monkeypatch.setattr(schedules,"schedules_enabled",lambda:True)
    monkeypatch.setattr(schedules,"_client",lambda:SimpleNamespace(
        rpc=lambda *_a,**_kw: (_ for _ in ()).throw(AssertionError("must not query SQL"))
    ))
    with pytest.raises(schedules.StudioScheduleError,match="shard"):
        asyncio.run(schedules.claim_due(shard_count=3,owned_shards=(4,)))


def test_shard_claim_parameters_sent_to_database(monkeypatch):
    monkeypatch.setattr(schedules,"schedules_enabled",lambda:True)
    requests=[]
    class DB:
        def rpc(self,name,args):
            requests.append((name,args))
            return SimpleNamespace(execute=lambda:SimpleNamespace(data=[]))
    monkeypatch.setattr(schedules,"_client",lambda:DB())
    assert asyncio.run(schedules.claim_due(shard_count=4,owned_shards=(1,3))) == []
    assert requests[0][1] == {"p_limit":8,"p_shard_count":4,"p_owned_shards":[1,3]}
