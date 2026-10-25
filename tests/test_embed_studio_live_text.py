"""Opt-in, shard-scoped Smart Text refresh for bot-owned Studio posts."""
from __future__ import annotations

import asyncio
from copy import deepcopy
from types import SimpleNamespace

import discord
import pytest

from stoney_verify.embed_studio_core import new_project, StudioValidationError
from stoney_verify.embed_studio_ui import StudioSession,PublishedMenu
from stoney_verify.embed_studio_live_ui import LiveTextMenu,LiveTextSelect,LiveTextChoice
from stoney_verify import embed_studio_live_text as live
from stoney_verify import embed_studio_live_runtime as worker


def _draft():
    p=new_project()
    p["embeds"][0]["description"]="Welcome to {server.name}!"
    p["content"]="Today is {today.utc}"
    return p


def test_live_is_off_without_both_runtime_flags(monkeypatch):
    monkeypatch.setattr(live,"storage_v2_enabled",lambda env:env.get("V2")=="true")
    assert not live.live_enabled({})
    assert not live.live_enabled({live.FLAG:"1"})
    assert live.live_enabled({live.FLAG:"1","V2":"true"})


def test_live_content_only_uses_safe_smart_text_and_digests_stably():
    p=_draft()
    assert live.smart_text_present(p)
    assert live.live_project(p)["content"]=="Today is {today.utc}"
    assert live.digest_project(p)==live.digest_project(deepcopy(p))
    changed=deepcopy(p);changed["content"]="2026-10-25"
    assert live.digest_project(changed)!=live.digest_project(p)
    p=new_project()
    with pytest.raises(live.StudioLiveError,match="Smart Text"):
        live.live_project(p)
    p=_draft();p["embeds"][0]["timestamp"]=True
    with pytest.raises(live.StudioLiveError,match="timestamps"):
        live.live_project(p)
    p=_draft();p["form"]={
        "title":"Feedback","button_label":"Respond",
        "questions":[{"label":"Question","style":"short","required":True}],
    }
    with pytest.raises(live.StudioLiveError,match="role controls or member forms"):
        live.live_project(p)


def test_private_ui_attached_to_existing_post_manager():
    session=StudioSession(guild_id=12,owner_id=34)
    menu=PublishedMenu(session,{"published":{}})
    assert "Live Text Updates" in {getattr(i,"label","") for i in menu.children}
    empty=LiveTextMenu(session,[])
    picks=[item for item in empty.children if isinstance(item,LiveTextSelect)]
    assert len(picks)==1 and picks[0].options[0].value=="none"
    choice=LiveTextChoice(session,"1:2")
    assert {"Enable Hourly Updates","Disable Updates"} <= {
        getattr(x,"label","") for x in choice.children
    }


def test_shard_filter_claim_params_and_duplicate_guard(monkeypatch):
    monkeypatch.setattr(live,"live_enabled",lambda:True)
    calls=[]
    class DB:
        def rpc(self,name,args):
            calls.append((name,args))
            return SimpleNamespace(execute=lambda:SimpleNamespace(data=[]))
    monkeypatch.setattr(live,"_client",lambda:DB())
    assert asyncio.run(live.claim_due(shard_count=4,owned_shards=(1,3)))==[]
    assert calls[0][1]=={"p_limit":4,"p_shard_count":4,"p_owned_shards":[1,3]}
    with pytest.raises(live.StudioLiveError,match="shard"):
        asyncio.run(live.claim_due(shard_count=4,owned_shards=(4,)))
    with pytest.raises(live.StudioLiveError,match="shard"):
        asyncio.run(live.claim_due(shard_count=4,owned_shards=(1,1)))


def test_bounded_worker_never_sends_new_discord_messages():
    job={"guild_id":"12","channel_id":"34","message_id":"56",
         "created_by":"78","nonce":"a"*32,"project":_draft()}
    result=asyncio.run(worker._refresh_one(
        SimpleNamespace(get_guild=lambda *_:None),job,
    ))
    assert result==("not_owned","",False)


def test_live_worker_off_by_default(monkeypatch):
    monkeypatch.setattr(worker,"live_enabled",lambda:False)
    assert worker.start_embed_studio_live_worker(object()) is None


def test_enroll_uses_per_message_nonce_and_does_not_touch_guild_config(monkeypatch):
    monkeypatch.setattr(live,"live_enabled",lambda:True)
    captured=[]
    class DB:
        def table(self,name):
            assert name==live.TABLE
            return self
        def upsert(self,row,on_conflict):
            captured.append((row,on_conflict))
            return self
        def execute(self):
            return SimpleNamespace(data=[dict(captured[0][0])])
    monkeypatch.setattr(live,"_client",lambda:DB())
    result=asyncio.run(live.enroll(12,34,56,78,"f"*32,_draft()))
    assert result is True
    assert captured[0][0]["guild_id"]=="12"
    assert captured[0][0]["nonce"]=="f"*32
    assert captured[0][1]=="guild_id,channel_id,message_id"


def test_sql_due_shard_RLS_and_no_unbounded_scans():
    from pathlib import Path
    sql=Path("supabase_fresh/migrations/202610200033_dankshield_embed_studio_live_text.sql").read_text().lower()
    for part in (
        "enable row level security","revoke all on public.dank_embed_studio_live_posts",
        "for update skip locked","p_owned_shards",
        "floor(live.guild_id::numeric / 4194304)",
        "interval '1 hour'","security invoker",
    ):
        assert part in sql
    assert "drop table" not in sql
