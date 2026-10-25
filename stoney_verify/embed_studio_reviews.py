"""Private moderator-to-moderator Embed Studio review handoff.

Approval authorizes loading a copy into a private editor; it NEVER publishes.
Uses the service-role-only dedicated Supabase review table, not guild config.
"""
from __future__ import annotations

import asyncio
import os
from typing import Any, Mapping
from uuid import UUID

from .embed_studio_core import StudioValidationError, validate_project
from .embed_studio_storage_v2 import storage_v2_enabled, verify_project

TABLE = "dank_embed_studio_reviews"
DECIDE_RPC = "dank_embed_studio_review_decide"
FLAG = "DANK_EMBED_STUDIO_REVIEWS_ENABLED"

class StudioReviewError(StudioValidationError):
    pass


def reviews_enabled(environ: Mapping[str, str] | None = None) -> bool:
    env = os.environ if environ is None else environ
    return (str(env.get(FLAG) or "").lower().strip() in {"1","true","on","yes"}
            and storage_v2_enabled(env))


def _client() -> Any:
    verify_project()
    from .globals import get_supabase
    db = get_supabase()
    if db is None:
        raise StudioReviewError("Team review storage is unavailable.")
    return db


def _rows(response: Any) -> list[dict[str, Any]]:
    data = getattr(response, "data", None)
    if not isinstance(data, list) or any(not isinstance(r, Mapping) for r in data):
        raise StudioReviewError("Invalid team review response.")
    return [dict(r) for r in data]


def validate_review_title(title: str) -> str:
    text = str(title or "").strip()
    if not 1 <= len(text) <= 80 or not text.isprintable():
        raise StudioReviewError("Review title must be 1–80 visible characters.")
    return text


def _enabled() -> None:
    if not reviews_enabled():
        raise StudioReviewError("Team Review is disabled until the storage migration is approved.")


async def submit_review(guild_id: int, submitted_by: int, channel_id: int,
                        title: str, project: Mapping[str, Any]) -> dict[str, Any]:
    _enabled()
    if min(guild_id, submitted_by, channel_id) <= 0:
        raise StudioReviewError("Choose a real server and destination channel.")
    project = validate_project(project)
    title = validate_review_title(title)
    def run():
        db = _client()
        return _rows(db.table(TABLE).insert({
            "guild_id":str(guild_id),"submitted_by":str(submitted_by),
            "channel_id":str(channel_id),"title":title,"project":project,
            "state":"pending",
        }).execute())
    try:
        rows = await asyncio.wait_for(asyncio.to_thread(run), timeout=12)
    except StudioValidationError:
        raise
    except Exception as exc:
        raise StudioReviewError("Review could not be submitted. No posts were sent.") from exc
    if len(rows) != 1 or rows[0].get("state") != "pending":
        raise StudioReviewError("Review storage did not confirm the submission.")
    return rows[0]


async def list_reviews(guild_id: int, *, state: str = "pending") -> list[dict[str, Any]]:
    _enabled()
    if guild_id <= 0 or state not in ("pending","approved"):
        raise StudioReviewError("Invalid team review filter.")
    def run():
        return _rows(_client().table(TABLE).select(
            "id,submitted_by,channel_id,title,state,created_at"
        ).eq("guild_id",str(guild_id)).eq("state",state)
         .order("created_at",desc=True).limit(20).execute())
    return await asyncio.wait_for(asyncio.to_thread(run),timeout=12)


async def get_review(guild_id: int, review_id: str) -> dict[str, Any] | None:
    _enabled()
    if guild_id <= 0:
        raise StudioReviewError("Invalid server.")
    try:
        normalized = str(UUID(str(review_id)))
    except ValueError as exc:
        raise StudioReviewError("Invalid review identifier.") from exc
    def run():
        return _rows(_client().table(TABLE).select("*")
            .eq("guild_id",str(guild_id)).eq("id",normalized).limit(1).execute())
    rows = await asyncio.wait_for(asyncio.to_thread(run),timeout=12)
    return rows[0] if rows else None


async def decide_review(guild_id: int, review_id: str, reviewer: int,
                        action: str) -> dict[str, Any] | None:
    _enabled()
    if guild_id <= 0 or reviewer <= 0 or action not in ("approved","rejected"):
        raise StudioReviewError("Invalid review decision.")
    try:
        ident = str(UUID(str(review_id)))
    except ValueError as exc:
        raise StudioReviewError("Invalid review ID.") from exc
    def run():
        return _rows(_client().rpc(DECIDE_RPC,{
            "p_id":ident,"p_guild_id":str(guild_id),"p_actor":str(reviewer),
            "p_action":action,
        }).execute())
    rows=await asyncio.wait_for(asyncio.to_thread(run),timeout=12)
    if len(rows)>1:
        raise StudioReviewError("Invalid duplicate decision; no draft was loaded.")
    return rows[0] if rows else None


def load_review_project(review: Mapping[str, Any], *, guild_id: int) -> dict[str, Any]:
    if review.get("guild_id") != str(guild_id) or review.get("state") != "approved":
        raise StudioReviewError("Only approved designs from your server may be loaded.")
    return validate_project(review.get("project"))
