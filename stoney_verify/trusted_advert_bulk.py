from __future__ import annotations

"""Owner-triggered, resumable, identity-scoped preview collapse.

Unlike 24h advert retention, this NEVER deletes messages. Every batch scans a
small slice of the channel and uses the same small Discord write budget as new
advert suppression. The cursor and counters live in a private Supabase table,
so a Discloud restart resumes without a fresh full-channel scan.
"""

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any

import discord

from .guild_config import get_guild_config
from .invite_trusted_advertisers import clean_rules
from .trusted_advert_cleanup import _db, _db_run
from .trusted_advert_embeds import (
    _take_auto_slot, needs_collapse, suppress_one,
)

TABLE = "dank_trusted_advert_collapse_jobs"
CHUNK_SCAN_LIMIT = 25
CHUNK_EDIT_LIMIT = 6
WORKER_POLL_SECONDS = 20
_IDLE_MAX_SECONDS = 300  # Bulk preview cleanup is not a real-time moderation path.
_WAKE_QUEUE: asyncio.Queue[None] = asyncio.Queue(maxsize=1)
_CANCELLED_LOCAL: set[tuple[str, str, str]] = set()
_TASK: asyncio.Task | None = None


def _key(guild_id: int, channel_id: int, bot_id: int) -> tuple[str, str, str]:
    return str(int(guild_id)), str(int(channel_id)), str(int(bot_id))


def _row_key(row: dict[str, Any]) -> tuple[str, str, str]:
    return str(row["guild_id"]), str(row["channel_id"]), str(row["bot_id"])


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _approved(guild: Any, channel: Any, bot_id: int, *, manual_only: bool) -> bool:
    cfg = await get_guild_config(int(guild.id), refresh=True)
    if str(cfg.get("source", "")).startswith("unavailable:"):
        raise RuntimeError("Server settings unavailable; refusing to modify previews.")
    rule = next((
        x for x in clean_rules(cfg)
        if x["channel_id"] == int(channel.id) and x["bot_id"] == int(bot_id)
    ), None)
    return rule is not None and (manual_only or rule["suppress_embeds"])


def _permissions(guild: Any, channel: Any) -> bool:
    me = getattr(guild, "me", None)
    if me is None or not isinstance(channel, discord.TextChannel):
        return False
    perms = channel.permissions_for(me)
    return bool(
        perms.view_channel and perms.read_message_history and perms.manage_messages
    )


async def start_bulk_collapse(
    guild: discord.Guild, channel: discord.TextChannel,
    *, bot_id: int, requested_by: int, manual_only: bool = False,
) -> dict[str, Any]:
    if not _permissions(guild, channel):
        raise PermissionError(
            "Dank Shield needs View Channel, Read Message History and Manage Messages."
        )
    if not await _approved(guild, channel, bot_id, manual_only=manual_only):
        raise PermissionError("Approve the exact bot/channel pair first, and enable collapse.")
    # Snapshot upper bound so the new-message listener owns later posts.
    latest = int(getattr(channel, "last_message_id", 0) or 0)
    cursor = str(latest + 1) if latest else None
    gid, cid, bid = _key(guild.id, channel.id, bot_id)
    def start() -> list[dict[str, Any]]:
        rows = _db().rpc("dank_start_trusted_embed_job", {
            "p_guild_id": gid, "p_channel_id": cid, "p_bot_id": bid,
            "p_requested_by": str(int(requested_by)),
            "p_cursor_before": cursor, "p_manual_only": bool(manual_only),
        }).execute().data
        return [dict(r) for r in (rows or [])]
    rows = await _db_run(start)
    if not rows:
        raise ValueError(
            "A bulk collapse is already running for this bot and channel. "
            "Use Collapse Status to see progress or cancel it."
        )
    _CANCELLED_LOCAL.discard((gid, cid, bid))
    # Wake the in-process worker immediately for a newly authorized job,
    # rather than polling Supabase every 20s when no work exists.
    try:
        _WAKE_QUEUE.put_nowait(None)
    except asyncio.QueueFull:
        pass
    return rows[0]


async def get_bulk_status(
    guild_id: int, channel_id: int, bot_id: int,
) -> dict[str, Any] | None:
    gid, cid, bid = _key(guild_id, channel_id, bot_id)
    def fetch() -> dict[str, Any] | None:
        rows = (
            _db().table(TABLE)
            .select("status,scanned,collapsed,skipped,failures,last_error,updated_at")
            .eq("guild_id", gid).eq("channel_id", cid).eq("bot_id", bid)
            .limit(1).execute().data
        )
        return dict(rows[0]) if rows else None
    return await _db_run(fetch)


async def cancel_bulk_collapse(
    guild_id: int, channel_id: int, bot_id: int,
) -> bool:
    gid, cid, bid = _key(guild_id, channel_id, bot_id)
    # Take effect immediately for in-process worker, independently of DB write.
    _CANCELLED_LOCAL.add((gid, cid, bid))
    def cancel() -> list[dict[str, Any]]:
        rows = (
            _db().table(TABLE).update({
                "status": "cancelled", "lease_until": None,
                "updated_at": _utc_iso(),
            }).eq("guild_id", gid).eq("channel_id", cid).eq("bot_id", bid)
            .eq("status", "running")
            .execute().data
        )
        return [dict(r) for r in (rows or [])]
    return bool(await _db_run(cancel))


async def _claim_jobs() -> list[dict[str, Any]]:
    def claim() -> list[dict[str, Any]]:
        rows = _db().rpc(
            "dank_claim_trusted_embed_jobs", {"p_limit": 1}
        ).execute().data
        return [dict(r) for r in (rows or [])]
    return await _db_run(claim)


async def _checkpoint(
    row: dict[str, Any], *, cursor: str | None, scanned: int,
    collapsed: int, skipped: int, complete: bool = False,
    error: str = "", retry_delay: int = 20,
) -> bool:
    version = int(row["claim_version"])
    failures = int(row.get("failures") or 0) + (1 if error else 0)
    status = ("completed" if complete else "failed" if failures >= 5 else "running")
    fields = {
        "status": status,
        "cursor_before": cursor,
        "scanned": int(row.get("scanned") or 0) + scanned,
        "collapsed": int(row.get("collapsed") or 0) + collapsed,
        "skipped": int(row.get("skipped") or 0) + skipped,
        "failures": failures if error else 0,
        "last_error": error[:120] or None,
        "lease_until": (
            (datetime.now(timezone.utc) + timedelta(seconds=retry_delay)).isoformat()
            if status == "running" else None
        ),
        "updated_at": _utc_iso(),
    }
    gid, cid, bid = _row_key(row)
    def update() -> list[dict[str, Any]]:
        rows = (
            _db().table(TABLE).update(fields)
            .eq("guild_id", gid).eq("channel_id", cid).eq("bot_id", bid)
            .eq("status", "running").eq("claim_version", version)
            .execute().data
        )
        return rows or []
    return bool(await _db_run(update))


async def _abort(row: dict[str, Any], *, reason: str) -> None:
    """Revoked rule must stop the durable job before any Discord operations."""
    gid, cid, bid = _row_key(row)
    _CANCELLED_LOCAL.add((gid, cid, bid))
    def update() -> None:
        (
            _db().table(TABLE).update({
                "status": "cancelled", "lease_until": None,
                "last_error": reason[:120], "updated_at": _utc_iso(),
            }).eq("guild_id", gid).eq("channel_id", cid).eq("bot_id", bid)
            .eq("status", "running")
            .eq("claim_version", int(row["claim_version"]))
            .execute()
        )
    await _db_run(update)


async def process_bulk_claim(bot: Any, row: dict[str, Any]) -> str:
    gid, cid, bid = _row_key(row)
    key = (gid, cid, bid)
    cursor = row.get("cursor_before")
    scanned = collapsed = skipped = 0
    try:
        guild = bot.get_guild(int(gid))
        if guild is None:
            raise RuntimeError("Guild disconnected")
        channel = guild.get_channel(int(cid))
        if not _permissions(guild, channel):
            raise RuntimeError("Missing channel or Manage Messages/History permission")
        manual_only = bool(row.get("manual_only"))
        if not await _approved(guild, channel, int(bid), manual_only=manual_only):
            await _abort(row, reason="bot/channel trust or collapse setting revoked")
            return "revoked"

        from .invite_policy_engine import load_invite_policy
        cfg, _settings = await load_invite_policy(guild, refresh=True)
        # Separate policy freshness check even when the fast settings cached
        # elsewhere have not expired.
        from .invite_trusted_advertisers import clean_rules
        if not any(
            rule["bot_id"] == int(bid) and rule["channel_id"] == int(cid)
            for rule in clean_rules(cfg)
        ):
            await _abort(row, reason="central Invite Shield trust revoked")
            return "revoked"

        hit_batch_limit = False
        out_of_tokens = False
        async for message in channel.history(
            limit=CHUNK_SCAN_LIMIT,
            before=discord.Object(id=int(cursor)) if cursor else None,
        ):
            if key in _CANCELLED_LOCAL:
                return "cancelled"
            if needs_collapse(message, cfg, require_opt_in=False):
                # Reserved once per actual edit across automatic+bulk actions.
                # If budget runs out, do NOT advance cursor past this message.
                if collapsed >= CHUNK_EDIT_LIMIT:
                    hit_batch_limit = True
                    break
                if not await _take_auto_slot():
                    out_of_tokens = True
                    break
                ok = await suppress_one(
                    message, cfg, automatic=not manual_only, slot_reserved=True,
                )
                if not ok:
                    await _checkpoint(
                        row, cursor=cursor, scanned=scanned,
                        collapsed=collapsed, skipped=skipped,
                        error="Discord suppression failed", retry_delay=60,
                    )
                    return "edit-error"
                collapsed += 1
            else:
                skipped += 1
            scanned += 1
            cursor = str(message.id)

        finished = not hit_batch_limit and not out_of_tokens and scanned < CHUNK_SCAN_LIMIT
        if key in _CANCELLED_LOCAL:
            return "cancelled"
        ok = await _checkpoint(
            row, cursor=cursor, scanned=scanned, collapsed=collapsed,
            skipped=skipped, complete=finished,
            retry_delay=60 if out_of_tokens else 20,
        )
        return "completed" if finished and ok else "checkpoint" if ok else "cancelled"
    except asyncio.CancelledError:
        raise
    except (discord.Forbidden, discord.HTTPException, RuntimeError, ValueError) as exc:
        try:
            await _checkpoint(
                row, cursor=cursor, scanned=scanned, collapsed=collapsed,
                skipped=skipped, error=type(exc).__name__, retry_delay=90,
            )
        except Exception:
            pass
        return "retry"
    except Exception as exc:
        print(f"⚠️ trusted_embed_bulk: error guild={gid} type={type(exc).__name__}")
        # Unknown / database failure: leave 3m lease to expire, then retry.
        return "error"


async def _worker(bot: Any) -> None:
    await bot.wait_until_ready()
    print("🫥 trusted_embed_bulk: durable paced worker started")
    idle_delay = WORKER_POLL_SECONDS
    while not bot.is_closed():
        try:
            jobs = await _claim_jobs()
            if jobs:
                idle_delay = WORKER_POLL_SECONDS
            else:
                idle_delay = min(_IDLE_MAX_SECONDS, idle_delay * 2)
            for row in jobs:
                outcome = await process_bulk_claim(bot, row)
                if outcome in ("completed", "revoked"):
                    print(
                        f"🫥 trusted_embed_bulk: {outcome} "
                        f"guild={row['guild_id']} channel={row['channel_id']}"
                    )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            print(f"⚠️ trusted_embed_bulk: poll_failed type={type(exc).__name__}")
            idle_delay = min(_IDLE_MAX_SECONDS, max(60, idle_delay * 2))
        # A local staff-initiated job wakes immediately. A DB-backed fallback
        # always probes eventually for other processes and after restarts.
        try:
            await asyncio.wait_for(_WAKE_QUEUE.get(), timeout=idle_delay)
            idle_delay = WORKER_POLL_SECONDS
        except asyncio.TimeoutError:
            pass


def start_bulk_worker(bot: Any) -> asyncio.Task:
    global _TASK
    if _TASK is None or _TASK.done():
        _TASK = asyncio.create_task(_worker(bot), name="trusted_embed_bulk_collapse")
    return _TASK
