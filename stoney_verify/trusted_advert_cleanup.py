from __future__ import annotations

"""Opt-in durable advertisement cleanup, isolated from Invite Shield deletes.

Only messages recorded from exact trusted bot/channel pairs are claimed. This
worker uses the existing Supabase connection and does not scan guild history.
"""

import asyncio
from datetime import datetime, timezone, timedelta
from typing import Any
import discord

from .globals import get_supabase
from .invite_trusted_advertisers import (
    trusted_rule, is_advert_like, expires_at, retention_deadline,
)
from .guild_config import get_guild_config

TABLE = "dank_trusted_advert_messages"
_TASK: asyncio.Task | None = None
_POLL_SECONDS = 120


def _db() -> Any:
    client = get_supabase()
    if client is None:
        raise RuntimeError("Trusted ad cleanup requires its configured database.")
    return client


async def _db_run(callback: Any) -> Any:
    return await asyncio.to_thread(callback)


def _iso(time_value: datetime) -> str:
    return time_value.astimezone(timezone.utc).isoformat()


async def track_advert(message: Any) -> bool:
    """Record only a new, identifiable, non-interaction advert.

    Never guess a message's timestamp or silently insert during DB outage.
    """
    guild = getattr(message, "guild", None)
    if guild is None:
        return False
    gid = int(getattr(guild, "id", 0) or 0)
    if gid <= 0:
        return False
    # Hot path: almost every Discord message is human. Do not perform a
    # config/DB lookup unless this is a bot-authored advert candidate.
    author = getattr(message, "author", None)
    if (not bool(getattr(author, "bot", False))
            or getattr(message, "webhook_id", None)
            or not is_advert_like(message)):
        return False
    # Configuration is re-read from the existing cached policy owner.
    try:
        from .invite_policy_engine import load_invite_policy
        cfg, _settings = await load_invite_policy(guild)
        rule = trusted_rule(message, cfg)
        if not rule or not rule["cleanup_enabled"] or not is_advert_like(message):
            return False
        expiry = expires_at(message, rule)
        posted = getattr(message, "created_at", None)
        if expiry is None or posted is None:
            return False
        payload = {
            "guild_id": str(gid),
            "channel_id": str(rule["channel_id"]),
            "bot_id": str(rule["bot_id"]),
            "message_id": str(int(message.id)),
            "posted_at": _iso(posted),
            "delete_after": _iso(expiry),
            "state": "pending",
        }
        def insert() -> None:
            # Do not replace any already-claimed/done row after reconnects.
            _db().table(TABLE).insert(payload).execute()
        try:
            await _db_run(insert)
        except Exception as exc:
            if "23505" in str(exc) or "duplicate key" in str(exc).lower():
                return True
            print(f"⚠️ trusted_advert: tracking_failed guild={gid} type={type(exc).__name__}")
            return False
        return True
    except Exception as exc:
        print(f"⚠️ trusted_advert: message_tracking_error guild={gid} type={type(exc).__name__}")
        return False


async def _claim_due() -> list[dict[str, Any]]:
    def fetch() -> list[dict[str, Any]]:
        rows = _db().rpc("dank_claim_due_trusted_adverts", {"p_limit": 8}).execute().data
        return [dict(row) for row in (rows or []) if isinstance(row, dict)]
    return await _db_run(fetch)


async def _finish(record: dict[str, Any], state: str, *, error: str = "",
                  due_at: datetime | None = None) -> None:
    gid = str(record.get("guild_id") or "")
    mid = str(record.get("message_id") or "")
    if state not in {"done", "cancelled", "pending", "failed"}:
        return
    attempts = int(record.get("attempts") or 0)
    if state == "pending" and attempts >= 8 and due_at is None:
        state = "failed"
    update: dict[str, Any] = {
        "state": state,
        "claimed_until": None,
        "last_error": error[:120] or None,
        "updated_at": _iso(datetime.now(timezone.utc)),
    }
    if state == "pending":
        if due_at is not None:
            update["delete_after"] = _iso(due_at)
            # A longer retention setting is a scheduled wait, not a failed
            # deletion. Do not exhaust retries while waiting for its deadline.
            update["attempts"] = 0
        else:
            update["delete_after"] = _iso(datetime.now(timezone.utc) + timedelta(minutes=30))
    def finish() -> None:
        _db().table(TABLE).update(update).eq("guild_id", gid).eq("message_id", mid).eq("state", "claimed").execute()
    await _db_run(finish)


async def process_claim(bot: Any, record: dict[str, Any]) -> str:
    """Re-authorize before REST: revocation or moved bot/channel cancels."""
    try:
        gid, cid, mid = (int(record[x]) for x in ("guild_id", "channel_id", "message_id"))
        author_id = int(record["bot_id"])
        posted = datetime.fromisoformat(str(record["posted_at"]).replace("Z", "+00:00"))
    except (TypeError, KeyError, ValueError):
        await _finish(record, "cancelled", error="invalid_stored_row")
        return "invalid-row"
    guild = bot.get_guild(gid)
    if guild is None:
        await _finish(record, "pending", error="guild_not_connected")
        return "guild-unknown"
    try:
        cfg = await get_guild_config(gid, refresh=True)
        if str(cfg.get("source", "")).startswith("unavailable:"):
            await _finish(record, "pending", error="config_unavailable")
            return "config-unavailable"
        from .invite_trusted_advertisers import clean_rules
        rule = next((v for v in clean_rules(cfg)
                     if v["bot_id"] == author_id and v["channel_id"] == cid), None)
        if rule is None or not rule["cleanup_enabled"]:
            await _finish(record, "cancelled", error="trusted_rule_revoked")
            return "rule-revoked"
        expected = retention_deadline(posted, rule["retention_hours"])
        if expected is None:
            await _finish(record, "cancelled", error="invalid_stored_row")
            return "invalid-row"
        if datetime.now(timezone.utc) < expected:
            await _finish(record, "pending", error="retention_not_elapsed", due_at=expected)
            return "not-due"
        channel = guild.get_channel(cid)
        if not isinstance(channel, discord.TextChannel):
            await _finish(record, "cancelled", error="channel_missing")
            return "channel-missing"
        me = guild.me
        if not me:
            await _finish(record, "pending", error="bot_member_unavailable")
            return "bot-unknown"
        perm = channel.permissions_for(me)
        if not perm.view_channel or not perm.read_message_history or not perm.manage_messages:
            await _finish(record, "pending", error="missing_manage_messages")
            return "no-permission"
        try:
            message = await channel.fetch_message(mid)
        except discord.NotFound:
            await _finish(record, "done")
            return "already-removed"
        if (int(message.author.id) != author_id
                or int(message.channel.id) != cid
                or not trusted_rule(message, cfg)
                or not is_advert_like(message)):
            await _finish(record, "cancelled", error="message_no_longer_eligible")
            return "not-eligible"
        # Do not delete if a message created after tracked timestamp somehow
        # reuses an ID or the recorded timestamp does not match.
        actual_expiry = expires_at(message, rule)
        if actual_expiry is None:
            await _finish(record, "cancelled", error="invalid_message_timestamp")
            return "invalid-timestamp"
        if datetime.now(timezone.utc) < actual_expiry:
            await _finish(record, "pending", error="message_too_new", due_at=actual_expiry)
            return "message-too-new"
        try:
            await message.delete(reason="Trusted advertising retention expired (server opt-in)")
        except discord.NotFound:
            pass
        await _finish(record, "done")
        print(f"🧹 trusted_advert: expired guild={gid} channel={cid} message={mid}")
        return "deleted"
    except (discord.Forbidden, discord.HTTPException) as exc:
        await _finish(record, "pending", error=type(exc).__name__)
        return "discord-error"
    except Exception as exc:
        print(f"⚠️ trusted_advert: cleanup_failed guild={gid} type={type(exc).__name__}")
        try:
            await _finish(record, "pending", error=type(exc).__name__)
        except Exception:
            pass
        return "error"


async def _worker(bot: Any) -> None:
    await bot.wait_until_ready()
    print("🧹 trusted_advert: opt-in cleanup worker started (durable due claims)")
    while not bot.is_closed():
        try:
            batch = await _claim_due()
            for record in batch[:8]:
                await process_claim(bot, record)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            print(f"⚠️ trusted_advert: due_poll_failed type={type(exc).__name__}")
        await asyncio.sleep(_POLL_SECONDS)


def start_trusted_advert_worker(bot: Any) -> asyncio.Task | None:
    global _TASK
    marker = "_dank_trusted_advert_listener_installed"
    if not getattr(bot, marker, False):
        async def on_message(message: discord.Message) -> None:
            if getattr(message, "guild", None) is not None:
                await track_advert(message)
                # Suppression is separate from cleanup; it can be enabled
                # when 24-hour deletion is disabled and never changes it.
                from .trusted_advert_embeds import suppress_new_trusted_advert
                await suppress_new_trusted_advert(message)

        async def on_message_edit(before: discord.Message, after: discord.Message) -> None:
            # Link previews may arrive after the initial message event.
            # This is cache-based only: no global fetch or history scanning.
            if getattr(after, "guild", None) is not None:
                from .trusted_advert_embeds import suppress_new_trusted_advert
                await suppress_new_trusted_advert(after)

        bot.add_listener(on_message, "on_message")
        bot.add_listener(on_message_edit, "on_message_edit")
        setattr(bot, marker, True)
    if _TASK is None or _TASK.done():
        _TASK = asyncio.create_task(_worker(bot), name="trusted_advert_expiry")
    return _TASK


async def recent_cleanup_status(guild_id: int, channel_id: int) -> dict[str, Any]:
    """Staff-only UI calls this on demand; never exposes raw advert content."""
    gid, cid = str(int(guild_id)), str(int(channel_id))
    def read() -> list[dict[str, Any]]:
        rows = (
            _db().table(TABLE)
            .select("state,delete_after,last_error,bot_id")
            .eq("guild_id", gid).eq("channel_id", cid)
            .order("delete_after", desc=True).limit(50).execute().data
        )
        return [dict(row) for row in (rows or []) if isinstance(row, dict)]
    rows = await _db_run(read)
    counts = {state: sum(row.get("state") == state for row in rows)
              for state in ("pending", "claimed", "done", "cancelled", "failed")}
    return {"total_shown": len(rows), "counts": counts,
            "next_due": min((str(row.get("delete_after")) for row in rows
                             if row.get("state") == "pending" and row.get("delete_after")), default="None")}


async def cancel_scheduled_for_pair(guild_id: int, channel_id: int, bot_id: int) -> None:
    """Best-effort immediate ledger cancellation following a verified config revoke.

    Existing row status is not revived if the pair is approved again later.
    """
    def cancel() -> None:
        (
            _db().table(TABLE).update({
                "state": "cancelled", "claimed_until": None,
                "last_error": "staff_revoked_or_paused",
                "updated_at": _iso(datetime.now(timezone.utc)),
            })
            .eq("guild_id", str(int(guild_id)))
            .eq("channel_id", str(int(channel_id)))
            .eq("bot_id", str(int(bot_id)))
            .in_("state", ["pending", "claimed"])
            .execute()
        )
    await _db_run(cancel)
