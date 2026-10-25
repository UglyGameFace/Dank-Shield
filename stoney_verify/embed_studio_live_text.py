"""Opt-in hourly Smart Text refresh metadata for existing bot-owned Studio posts."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import re
from hashlib import sha256
import json
import os
from typing import Any, Mapping

from .embed_studio_core import (
    SMART_TEXT_TOKENS, StudioValidationError, validate_project,
)
from .embed_studio_storage_v2 import storage_v2_enabled, verify_project

TABLE = "dank_embed_studio_live_posts"
CLAIM_RPC = "dank_embed_studio_claim_live"
FLAG = "DANK_EMBED_STUDIO_LIVE_ENABLED"
TOKEN_NAMES = tuple(name for name, _ in SMART_TEXT_TOKENS)


class StudioLiveError(StudioValidationError):
    pass


def live_enabled(environ: Mapping[str,str] | None=None) -> bool:
    env=os.environ if environ is None else environ
    return (str(env.get(FLAG) or "").strip().lower() in {"1","true","yes","on"}
            and storage_v2_enabled(env))


def smart_text_present(project: Mapping[str,Any]) -> bool:
    draft=validate_project(project)
    fields=[draft["content"]]
    for embed in draft["embeds"]:
        fields.extend(embed.get(k,"") for k in ("title","description","author_name","footer"))
        for item in embed.get("fields",[]):
            fields.extend((item["name"],item["value"]))
    return any(token in field for field in fields for token in TOKEN_NAMES)


def live_project(project: Mapping[str,Any]) -> dict[str,Any]:
    draft=validate_project(project)
    if draft["role_actions"] or draft.get("form"):
        raise StudioLiveError("Live Text cannot update role controls or member forms.")
    if any(embed["timestamp"] for embed in draft["embeds"]):
        raise StudioLiveError("Turn off embed timestamps before enabling Live Text.")
    if not smart_text_present(draft):
        raise StudioLiveError("Add Smart Text to your design before enabling hourly updates.")
    return draft


def digest_project(project: Mapping[str,Any]) -> str:
    draft=validate_project(project)
    data={"content":draft["content"],"embeds":draft["embeds"]}
    return sha256(json.dumps(data,sort_keys=True,ensure_ascii=False).encode("utf-8")).hexdigest()


def _client() -> Any:
    verify_project()
    from .globals import get_supabase
    db=get_supabase()
    if db is None:
        raise StudioLiveError("Live Text database is unavailable.")
    return db


def _rows(response: Any) -> list[dict[str,Any]]:
    data=getattr(response,"data",None)
    if not isinstance(data,list) or any(not isinstance(i,Mapping) for i in data):
        raise StudioLiveError("Live Text database returned an invalid response.")
    return [dict(r) for r in data]


async def enroll(guild_id:int,channel_id:int,message_id:int,staff_id:int,
                 nonce:str,project:Mapping[str,Any]) -> bool:
    if not live_enabled():
        raise StudioLiveError("Live Text is not enabled on this deployment.")
    model=live_project(project)
    if min(guild_id,channel_id,message_id,staff_id)<=0 or not re.fullmatch(r'[0-9a-f]{32}',nonce):
        raise StudioLiveError("Invalid Live Text target.")
    def write():
        row={
            "guild_id":str(guild_id),"channel_id":str(channel_id),
            "message_id":str(message_id),"created_by":str(staff_id),
            "nonce":nonce,"project":model,"enabled":True,
            "next_refresh_at":(datetime.now(timezone.utc)+timedelta(hours=1)).isoformat(),
            "last_hash":None,"last_status":None,
        }
        return _rows(_client().table(TABLE).upsert(
            row,on_conflict="guild_id,channel_id,message_id"
        ).execute())
    try:
        result=await asyncio.wait_for(asyncio.to_thread(write),timeout=12)
    except Exception as exc:
        raise StudioLiveError("Live Text could not be enabled. No message was edited.") from exc
    return len(result)==1 and bool(result[0].get("enabled"))


async def disable(guild_id:int,channel_id:int,message_id:int,nonce:str) -> bool:
    if not live_enabled():
        return False
    def write():
        return bool(_rows(_client().table(TABLE).update({
            "enabled":False,"last_status":"disabled_by_staff",
        }).eq("guild_id",str(guild_id)).eq("channel_id",str(channel_id))
          .eq("message_id",str(message_id)).eq("nonce",nonce).execute()))
    return await asyncio.wait_for(asyncio.to_thread(write),timeout=12)


async def claim_due(*,shard_count:int=1,owned_shards:tuple[int,...]=(0,)) -> list[dict[str,Any]]:
    if not live_enabled():
        return []
    if (not isinstance(shard_count,int) or not 1<=shard_count<=4096
            or not isinstance(owned_shards,tuple) or not owned_shards
            or len(set(owned_shards))!=len(owned_shards)
            or any(type(x) is not int or not 0<=x<shard_count for x in owned_shards)):
        raise StudioLiveError("Unknown or invalid Discord shard ownership.")
    def run():
        return _rows(_client().rpc(CLAIM_RPC,{
            "p_limit":4,"p_shard_count":shard_count,
            "p_owned_shards":list(owned_shards),
        }).execute())
    return await asyncio.wait_for(asyncio.to_thread(run),timeout=12)


async def mark_result(job:Mapping[str,Any],status:str,*,digest:str="",disable_job:bool=False) -> None:
    if not live_enabled():
        return
    allowed={"updated","unchanged","missing","stale","permissions","invalid","api_error","not_owned"}
    if status not in allowed:
        raise StudioLiveError("Invalid Live Text result.")
    payload={"last_status":status}
    if digest:
        if len(digest)!=64:
            raise StudioLiveError("Invalid content digest.")
        payload["last_hash"]=digest
    if disable_job:
        payload["enabled"]=False
    def write():
        return _rows(_client().table(TABLE).update(payload)
            .eq("guild_id",str(job["guild_id"]))
            .eq("channel_id",str(job["channel_id"]))
            .eq("message_id",str(job["message_id"]))
            .eq("nonce",str(job["nonce"])).execute())
    await asyncio.wait_for(asyncio.to_thread(write),timeout=12)
