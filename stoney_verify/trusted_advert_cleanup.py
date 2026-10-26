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
from .invite_trusted_advertisers import trusted_rule, is_advert_like, expires_at
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


async def _finish(record: dict[str, Any], state: str, *, error: str = "") -> None:
    gid = str(record.get("guild_id") or "")
    mid = str(record.get("message_id") or "")
    if state not in {"done", "cancelled", "pending", "failed"}:
        return
    attempts = int(record.get("attempts") or 0)
    if state == "pending" and attempts >= 8:
        state = "failed"
    update: dict[str, Any] = {
        "state": state,
        "claimed_until": None,
        "last_error": error[:120] or None,
        "updated_at": _iso(datetime.now(timezone.utc)),
    }
    if state == "pending":
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
        expected = posted + timedelta(
            hours=rule["retention_hours"], minutes=10,
        )
        if datetime.now(timezone.utc) < expected:
            await _finish(record, "pending", error="retention_not_elapsed")
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
        if actual_expiry is None or datetime.now(timezone.utc) < actual_expiry:
            await _finish(record, "pending", error="message_too_new")
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
        bot.add_listener(on_message, "on_message")
        setattr(bot, marker, True)
    if _TASK is None or _TASK.done():
        _TASK = asyncio.create_task(_worker(bot), name="trusted_advert_expiry")
    return _TASK
