"""Managed artwork capacity: atomic reservation contract and safe audits."""
from __future__ import annotations

import asyncio
from io import BytesIO
from types import SimpleNamespace

import pytest
from PIL import Image

from stoney_verify import embed_studio_artwork_budget as budget
from stoney_verify import embed_studio_assets as assets


def img() -> bytes:
    mem=BytesIO()
    Image.new("RGB",(4,4)).save(mem,format="PNG")
    return mem.getvalue()


@pytest.mark.parametrize("env", [
    {}, {budget.GUILD_BUDGET_ENV:"",budget.GLOBAL_BUDGET_ENV:""},
    {budget.GUILD_BUDGET_ENV:"abc",budget.GLOBAL_BUDGET_ENV:"700"},
    {budget.GUILD_BUDGET_ENV:"5",budget.GLOBAL_BUDGET_ENV:"500"},
    {budget.GUILD_BUDGET_ENV:"80",budget.GLOBAL_BUDGET_ENV:"79"},
    {budget.GUILD_BUDGET_ENV:"1",budget.GLOBAL_BUDGET_ENV:"999999999"},
])
def test_budget_requires_explicit_valid_verified_owner_allocation(env):
    with pytest.raises(budget.ArtworkBudgetError):
        budget.get_budget(env)


def test_budget_is_owner_configurable_and_uses_bytes_not_link_count():
    result=budget.get_budget({
        budget.GUILD_BUDGET_ENV:"128",budget.GLOBAL_BUDGET_ENV:"768",
    })
    assert result.guild_bytes==128*1024*1024
    assert result.global_bytes==768*1024*1024


def test_reserve_fail_closed_on_database_quota_denial():
    calls=[]
    class DB:
        def rpc(self,name,params):
            calls.append((name,params))
            return SimpleNamespace(execute=lambda:SimpleNamespace(data={
                "reserved":False,"reason":"budget_full",
            }))
    with pytest.raises(budget.ArtworkBudgetError,match="limit"):
        budget.reserve_artwork(DB(),guild_id=100,object_path="100/"+"e"*32+".png",
                               size_bytes=42,budget=budget.ArtworkBudget(100,500))
    assert calls[0][0]==budget.RESERVE_RPC
    assert calls[0][1]["p_guild_id"]=="100"
    assert calls[0][1]["p_bytes"]==42


def test_upload_never_contacts_storage_when_no_quota(monkeypatch):
    monkeypatch.setattr(assets,"uploads_enabled",lambda:True)
    monkeypatch.setattr(budget,"get_budget",
                        lambda:budget.ArtworkBudget(64*budget.MIB,512*budget.MIB))
    class DB:
        def rpc(self,name,params):
            assert name==budget.RESERVE_RPC
            return SimpleNamespace(execute=lambda:SimpleNamespace(data={"reserved":False}))
        storage=SimpleNamespace(from_=lambda bucket:(_ for _ in ()).throw(
            AssertionError("Must not contact Storage without a reservation")))
    # _client verifies project first; substitute a pinned test database.
    monkeypatch.setattr(assets,"_client",lambda:DB())
    with pytest.raises(budget.ArtworkBudgetError):
        assets._upload_sync(123,img(),"logo.png")


def test_uncertain_cleanup_keeps_quota_held(monkeypatch):
    monkeypatch.setattr(assets,"uploads_enabled",lambda:True)
    monkeypatch.setattr(budget,"get_budget",
                        lambda:budget.ArtworkBudget(64*budget.MIB,512*budget.MIB))
    calls=[]
    class Storage:
        def upload(self,**kwargs):
            raise TimeoutError("Upload outcome unknown")
        def remove(self,paths):
            raise TimeoutError("Removal outcome unknown")
    class DB:
        storage=SimpleNamespace(from_=lambda name:Storage())
        def rpc(self,name,args):
            calls.append(name)
            return SimpleNamespace(execute=lambda:SimpleNamespace(data={"reserved":True}))
    monkeypatch.setattr(assets,"_client",lambda:DB())
    with pytest.raises(TimeoutError):
        assets._upload_sync(123,img(),"logo.png")
    assert calls==[budget.RESERVE_RPC]


def test_failed_finalization_releases_only_after_confirmed_remove(monkeypatch):
    monkeypatch.setattr(assets,"uploads_enabled",lambda:True)
    monkeypatch.setattr(budget,"get_budget",
                        lambda:budget.ArtworkBudget(64*budget.MIB,512*budget.MIB))
    calls=[]
    class Storage:
        def upload(self,**kw):return object()
        def get_public_url(self,path):
            return "https://example.com/"+path
        def remove(self,paths):
            calls.append("storage_removed")
            return []
    class DB:
        storage=SimpleNamespace(from_=lambda name:Storage())
        def rpc(self,name,args):
            calls.append(name)
            if name==budget.FINALIZE_RPC:raise RuntimeError("Postgres down")
            return SimpleNamespace(execute=lambda:SimpleNamespace(data={
                "reserved":True,
            } if name==budget.RESERVE_RPC else True))
    monkeypatch.setattr(assets,"_client",lambda:DB())
    with pytest.raises(RuntimeError):
        assets._upload_sync(123,img(),"logo.png")
    assert calls==[budget.RESERVE_RPC,budget.FINALIZE_RPC,
                   "storage_removed",budget.RELEASE_RPC]


def test_usage_reads_single_aggregate_not_entire_asset_table(monkeypatch):
    calls=[]
    class DB:
        def rpc(self,name,args):
            calls.append((name,args))
            return SimpleNamespace(execute=lambda:SimpleNamespace(data={
                "guild_used":6*budget.MIB,"global_used":14*budget.MIB,
                "asset_count":7,"reservation_count":1,
                "stale_reservations":1,
            }))
        def table(self,*args):
            raise AssertionError("Reading all artwork records is forbidden")
    monkeypatch.setattr(budget,"_client",lambda:DB())
    data=asyncio.run(budget.read_artwork_usage(123))
    assert data["stale_reservations"]==1
    assert data["guild_used"]==6*budget.MIB
    assert calls==[(budget.USAGE_RPC,{"p_guild_id":"123"})]


def test_missing_or_corrupt_usage_does_not_invent_zero():
    class DB:
        def rpc(self,*args):
            return SimpleNamespace(execute=lambda:SimpleNamespace(data={
                "guild_used":0,"global_used":"0",
                "asset_count":0,"reservation_count":0,"stale_reservations":0,
            }))
    old=budget._client
    budget._client=lambda:DB()
    try:
        with pytest.raises(budget.ArtworkBudgetError):
            budget._read_usage_sync(1)
    finally:
        budget._client=old


def test_sql_is_service_only_and_never_deletes_existing_published_assets():
    from pathlib import Path
    sql=Path("supabase_fresh/migrations/202610200034_dankshield_embed_studio_asset_budgets.sql").read_text().lower()
    for term in (
        "pg_advisory_xact_lock","security invoker",
        "from public.dank_embed_studio_assets",
        "from public.dank_embed_studio_asset_holds",
        "revoke all on public.dank_embed_studio_asset_holds",
        "enable row level security",
        "grant execute on function public.dank_embed_studio_asset_reserve",
        "grant execute on function public.dank_embed_studio_asset_finalize",
        "grant execute on function public.dank_embed_studio_asset_usage",
    ):
        assert term in sql
    assert "delete from public.dank_embed_studio_assets" not in sql
    assert "drop table" not in sql
    assert "security definer" not in sql
