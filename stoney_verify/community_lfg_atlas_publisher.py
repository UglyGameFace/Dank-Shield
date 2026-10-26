from __future__ import annotations

"""Durable, rate-limited external LFG posting and revocation.

Publishing is never automatic: a host/co-host explicitly confirmed a queued
action. Only owned Atlas posts with private edit credentials can be removed.
Uncertain POST outcomes fail closed and are never blindly retried.
"""

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any

import aiohttp

from . import community_hub_service as hub
from .community_lfg_atlas_publishing import (
    TABLE, AtlasPublishError, _db, _work, send_post, delete_post,
)

_TASK: asyncio.Task | None = None
POLL_SECONDS = 60


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _update_claim(
    row: dict[str, Any], state: str, *, changes: dict[str, Any] | None = None,
) -> bool:
    values = {
        "state": state, "lease_until": None,
        "updated_at": _now(), **(changes or {}),
    }
    def apply() -> list[dict[str, Any]]:
        records = (
            _db().table(TABLE).update(values)
            .eq("session_id", row["session_id"])
            .eq("guild_id", row["guild_id"])
            .eq("claim_version", int(row["claim_version"]))
            .eq("state", row["state"])
            .execute().data
        )
        return [dict(r) for r in (records or [])]
    return bool(await _work(apply))


async def _queue_claims() -> list[dict[str, Any]]:
    def fetch() -> list[dict[str, Any]]:
        res = _db().rpc("dank_claim_atlas_publications", {"p_limit": 2}).execute()
        return [dict(x) for x in (res.data or []) if isinstance(x, dict)]
    return await _work(fetch)


async def _fail_stale_unknown_posts() -> None:
    # A POST could have succeeded just before a process crash. Do not send it
    # again and accidentally create a second advert with an unmanaged token.
    def mark() -> None:
        (
            _db().table(TABLE).update({
                "state": "unknown", "lease_until": None,
                "last_error": "Publisher restarted or lost confirmation. No automatic retry.",
                "updated_at": _now(),
            }).eq("state", "publishing").lt("lease_until", _now()).execute()
        )
    await _work(mark)


async def _still_authorized(row: dict[str, Any]) -> bool:
    """Check authority and current public consent again immediately before POST."""
    sid, gid = str(row["session_id"]), str(row["guild_id"])
    if row.get("revoke_requested"):
        return False
    try:
        session, settings, members = await asyncio.gather(
            hub.get_session(sid, guild_id=gid),
            hub.get_settings(gid),
            hub.list_session_members(sid),
        )
    except hub.CommunityHubError:
        return False
    if (session.get("privacy") != "public"
            or session.get("state") not in {"open","forming","ready","active"}
            or not bool(session.get("public_lfg_opt_in"))
            or not settings.get("enabled", True)
            or settings.get("maintenance_mode")
            or not settings.get("public_lfg_enabled")
            or not str(settings.get("public_lfg_display_name") or "").strip()
            or settings.get("public_lfg_invite_url") != row.get("contact")):
        return False
    if not any(
        str(m.get("user_id")) == str(row.get("actor_id"))
        and m.get("role") in {"host", "cohost"}
        and not m.get("left_at")
        for m in members
    ):
        return False
    now = datetime.now(timezone.utc)
    try:
        start = datetime.fromisoformat(str(session["created_at"]).replace("Z","+00:00"))
        activity = datetime.fromisoformat(str(session["last_activity_at"]).replace("Z","+00:00"))
        expiry = datetime.fromisoformat(str(row["expires_at"]).replace("Z","+00:00"))
        if any(t.tzinfo is None for t in (start,activity,expiry)):
            return False
        return (
            expiry > now
            and start + timedelta(seconds=int(settings.get("max_session_lifetime_seconds",43200))) > now
            and activity + timedelta(seconds=int(settings.get("idle_timeout_seconds",1800))) > now
        )
    except (KeyError, TypeError, ValueError):
        return False


async def _send_claim(row: dict[str, Any]) -> str:
    if not await _still_authorized(row):
        await _update_claim(row, "failed", changes={
            "last_error": "Public session or host authorization was revoked before publishing.",
        })
        return "revoked-before-publish"
    try:
        rid, url, token = await send_post(row)
    except AtlasPublishError as exc:
        # Provider validation errors are definite, but if it returned 2xx
        # without a manageable receipt the outcome is UNKNOWN. Never repost.
        uncertain = ("unexpected publishing response", "private deletion credentials",
                     "trusted public post URL", "identify the post", "usable private deletion token",
                     "could not confirm")
        state = "unknown" if any(msg in str(exc) for msg in uncertain) else "failed"
        await _update_claim(row, state, changes={"last_error": str(exc)[:180]})
        return state
    except (aiohttp.ClientError, asyncio.TimeoutError):
        await _update_claim(row, "unknown", changes={
            "last_error": "Network outcome unknown; no automatic resend.",
        })
        return "unknown"
    except Exception as exc:
        # No retries after an uncertain outbound write.
        await _update_claim(row, "unknown", changes={
            "last_error": "Provider write outcome unavailable; no automatic resend.",
        })
        print(f"⚠️ atlas_publish: uncertain session={row['session_id']} type={type(exc).__name__}")
        return "unknown"

    try:
        ok = await _update_claim(row, "posted", changes={
            "remote_id": rid, "remote_url": url, "edit_token": token,
            "last_error": None,
        })
        if ok:
            print(f"🌍 atlas_publish: confirmed guild={row['guild_id']} session={row['session_id']}")
            return "posted"
    except Exception:
        pass
    # Critical compensation: never intentionally leave a known, unmanaged
    # public post after failing to durably store its private edit token.
    try:
        await delete_post(rid, token)
    except Exception:
        print(f"⚠️ atlas_publish: orphan_cleanup_failed session={row['session_id']}")
    return "unconfirmed"


async def _remove_claim(row: dict[str, Any]) -> str:
    rid, token = row.get("remote_id"), row.get("edit_token")
    if not rid or not token:
        await _update_claim(row, "failed", changes={
            "last_error": "Cannot remove without a stored post ID and private edit token.",
        })
        return "missing-credentials"
    try:
        await delete_post(str(rid), str(token))
    except (AtlasPublishError, aiohttp.ClientError, asyncio.TimeoutError) as exc:
        attempts = int(row.get("delete_attempts") or 0) + 1
        state = "failed" if attempts >= 8 else "removing"
        await _update_claim(row, state, changes={
            "delete_attempts": attempts,
            "lease_until": (
                datetime.now(timezone.utc) + timedelta(minutes=5)
            ).isoformat() if state == "removing" else None,
            "last_error": type(exc).__name__,
        })
        return "retry" if state == "removing" else "failed"
    # Clear credential only after provider confirmed removal or missing post.
    await _update_claim(row, "removed", changes={
        "remote_id": None, "edit_token": None,
        "remote_url": None, "last_error": None,
    })
    print(f"🌍 atlas_publish: removed guild={row['guild_id']} session={row['session_id']}")
    return "removed"


async def process_claim(row: dict[str, Any]) -> str:
    if row.get("state") == "publishing":
        return await _send_claim(row)
    if row.get("state") == "removing":
        return await _remove_claim(row)
    return "invalid"


async def _worker(bot: Any) -> None:
    await bot.wait_until_ready()
    print("🌍 Community LFG Atlas publisher started (host-consent-only)")
    while not bot.is_closed():
        try:
            await _fail_stale_unknown_posts()
            for row in await _queue_claims():
                await process_claim(row)
                # Avoid burst publishing/removal from shared Discloud IP.
                await asyncio.sleep(2)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            print(f"⚠️ atlas_publish: poll_error type={type(exc).__name__}")
        await asyncio.sleep(POLL_SECONDS)


def start_atlas_publisher(bot: Any) -> asyncio.Task:
    global _TASK
    if _TASK is None or _TASK.done():
        _TASK = asyncio.create_task(_worker(bot), name="lfg_atlas_host_publishing")
    return _TASK
