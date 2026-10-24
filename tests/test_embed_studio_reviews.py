"""Dedicated private, cross-moderator review of Embed Studio designs."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from uuid import uuid4
import pytest

from stoney_verify import embed_studio_reviews as reviews
from stoney_verify.embed_studio_core import new_project
from stoney_verify.embed_studio_ui import (
    StudioHomeView,StudioSession,StudioTeamReviewHomeView,
    StudioReviewSubmissionModal,StudioReviewDecisionView,
    StudioReviewQueueView, StudioReviewQueueSelect,
)


def test_review_is_off_by_default_and_requires_storage_v2(monkeypatch):
    monkeypatch.setattr(reviews,"storage_v2_enabled",lambda env: env.get("V2")=="true")
    assert not reviews.reviews_enabled({})
    assert not reviews.reviews_enabled({reviews.FLAG:"1"})
    assert reviews.reviews_enabled({reviews.FLAG:"1","V2":"true"})


@pytest.mark.parametrize("bad",["","\n"," " * 12,"X"*81])
def test_review_titles_must_be_visible(bad):
    with pytest.raises(reviews.StudioReviewError):
        reviews.validate_review_title(bad)
    assert reviews.validate_review_title("  Announcements  ")=="Announcements"


def test_submit_remains_guild_scoped_and_does_not_publish(monkeypatch):
    monkeypatch.setattr(reviews,"reviews_enabled",lambda:True)
    rows=[]
    class Table:
        def insert(self,row):
            rows.append(row)
            return self
        def execute(self):
            return SimpleNamespace(data=[dict(rows[0],id=str(uuid4()))])
    monkeypatch.setattr(reviews,"_client",lambda:SimpleNamespace(table=lambda *_:Table()))
    project=new_project()
    result=asyncio.run(reviews.submit_review(12,34,56,"Welcome",project))
    assert result["state"]=="pending"
    assert rows[0]["guild_id"]=="12" and rows[0]["submitted_by"]=="34"
    assert rows[0]["channel_id"]=="56"
    assert rows[0]["project"]==project


def test_decision_is_atomic_sql_rpc_scoped_to_guild_and_reviewer(monkeypatch):
    monkeypatch.setattr(reviews,"reviews_enabled",lambda:True)
    ident=str(uuid4())
    calls=[]
    class DB:
        def rpc(self,name,args):
            calls.append((name,args))
            return SimpleNamespace(execute=lambda:SimpleNamespace(data=[]))
    monkeypatch.setattr(reviews,"_client",lambda:DB())
    assert asyncio.run(reviews.decide_review(100,ident,200,"approved")) is None
    assert calls==[(reviews.DECIDE_RPC,{
        "p_id":ident,"p_guild_id":"100","p_actor":"200","p_action":"approved",
    })]
    for bad in ("waiting","published","deleted"):
        with pytest.raises(reviews.StudioReviewError):
            asyncio.run(reviews.decide_review(100,ident,200,bad))


def test_loading_only_approved_same_guild():
    project=new_project()
    approved={"guild_id":"100","state":"approved","project":project}
    assert reviews.load_review_project(approved,guild_id=100)==project
    with pytest.raises(reviews.StudioReviewError):
        reviews.load_review_project(approved,guild_id=101)
    with pytest.raises(reviews.StudioReviewError):
        reviews.load_review_project({**approved,"state":"pending"},guild_id=100)


def test_discord_mobile_controls_private_and_bounded():
    session=StudioSession(guild_id=100,owner_id=200)
    home=StudioHomeView(session)
    assert "Team Review" in {getattr(x,"label","") for x in home.children}
    assert max(sum(1 for item in home.children if item.row==row) for row in range(5))<=5
    team=StudioTeamReviewHomeView(session)
    assert {"Submit Current Draft","Pending Reviews","Approved Designs"} <= {
        getattr(x,"label","") for x in team.children
    }
    assert len(StudioReviewSubmissionModal(session).children)==1
    item={"id":str(uuid4()),"title":"Post","submitted_by":"200","channel_id":"300"}
    pending=StudioReviewQueueView(session,[item],state="pending")
    selectors=[x for x in pending.children if isinstance(x,StudioReviewQueueSelect)]
    assert len(selectors)==1
    assert selectors[0].options[0].label=="Post"
    decision=StudioReviewDecisionView(session,str(item["id"]))
    assert {"Approve & Edit Copy","Reject Request","Keep Pending"} <= {
        getattr(x,"label","") for x in decision.children
    }


def test_review_queue_db_error_fails_closed(monkeypatch):
    monkeypatch.setattr(reviews,"reviews_enabled",lambda:True)
    monkeypatch.setattr(reviews,"_client",lambda: (_ for _ in ()).throw(RuntimeError("offline")))
    with pytest.raises(RuntimeError):
        asyncio.run(reviews.list_reviews(100))


def test_service_role_only_review_migration():
    from pathlib import Path
    sql=Path("supabase_fresh/migrations/202610200031_dankshield_embed_studio_reviews.sql").read_text().lower()
    for expected in ("enable row level security","revoke all on public.dank_embed_studio_reviews",
                     "security invoker","r.submitted_by <> p_actor",
                     "and r.state='pending'","returning r.*",
                     "grant execute on function public.dank_embed_studio_review_decide"):
        assert expected in sql
    assert "security definer" not in sql
    assert "drop table" not in sql
