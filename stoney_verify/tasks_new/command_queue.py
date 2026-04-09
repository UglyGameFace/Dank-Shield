from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import discord

from ..globals import bot, GUILD_ID, get_supabase
from ..tickets_new.service import (
    assign_ticket,
    create_ticket_channel,
    find_open_ticket_for_owner,
    mark_ticket_closed,
    reopen_ticket,
)
from ..tickets_new.transcript_service import delete_ticket_with_optional_transcript
from ..events_new.members import (
    run_full_member_sync_for_guild,
    run_departed_reconciliation_for_guild,
    run_role_member_sync,
)

# ============================================================
# Supabase command queue worker
# ------------------------------------------------------------
# Reads public.bot_commands and executes dashboard-requested actions.
#
# Supported actions:
# - create_ticket
# - close_ticket
# - delete_ticket
# - reopen_ticket
# - assign_ticket
# - sync_members
# - reconcile_departed_members
# - sync_role_members
#
# Safe behavior:
# - processes one command at a time
# - marks rows processing/completed/failed
# - stores result/error in row
# - no direct public bot HTTP API required
# ============================================================


_QUEUE_TASK: Optional[asyncio.Task] = None
_QUEUE_STARTED = False
_POLL_SECONDS = 3.0


def _utc_iso(dt: Optional[datetime]) -> Optional[str]:
    if dt is None:
        return None
    try:
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat()
    except Exception:
        try:
            return dt.isoformat()
        except Exception:
            return None


def _sb():
    return get_supabase()


def _safe_str(x: Any) -> str:
    try:
        return str(x)
    except Exception:
        return ""


def _guild_id_str() -> str:
    try:
        return str(int(str(GUILD_ID or "0")))
    except Exception:
        return "0"


async def _get_primary_guild() -> Optional[discord.Guild]:
    gid = _guild_id_str()
    if gid == "0":
        return None

    guild = bot.get_guild(int(gid))
    if guild is not None:
        return guild

    try:
        guild = await bot.fetch_guild(int(gid))
    except Exception:
        guild = None

    return guild


async def _get_text_channel(channel_id: Any) -> Optional[discord.TextChannel]:
    try:
        cid = int(str(channel_id))
    except Exception:
        return None

    channel = bot.get_channel(cid)
    if channel is None:
        try:
            channel = await bot.fetch_channel(cid)
        except Exception:
            channel = None

    if isinstance(channel, discord.TextChannel):
        return channel
    return None


async def _get_member(guild: discord.Guild, user_id: Any) -> Optional[discord.Member]:
    try:
        uid = int(str(user_id))
    except Exception:
        return None

    member = guild.get_member(uid)
    if member is not None:
        return member

    try:
        member = await guild.fetch_member(uid)
    except Exception:
        member = None

    return member


def _claim_command(command_id: str) -> bool:
    sb = _sb()
    if sb is None:
        print("⚠️ Supabase unavailable; cannot claim command.")
        return False

    try:
        resp = (
            sb.table("bot_commands")
            .update(
                {
                    "status": "processing",
                    "picked_up_at": _utc_iso(datetime.now(timezone.utc)),
                }
            )
            .eq("id", command_id)
            .eq("status", "pending")
            .execute()
        )

        rows = getattr(resp, "data", None) or []
        return len(rows) > 0
    except Exception as e:
        print(f"❌ Failed claiming bot command {command_id}:", repr(e))
        return False


def _complete_command(command_id: str, result: Dict[str, Any]) -> None:
    sb = _sb()
    if sb is None:
        print("⚠️ Supabase unavailable; cannot complete command.")
        return

    try:
        (
            sb.table("bot_commands")
            .update(
                {
                    "status": "completed",
                    "result": result,
                    "error": None,
                    "completed_at": _utc_iso(datetime.now(timezone.utc)),
                }
            )
            .eq("id", command_id)
            .execute()
        )
    except Exception as e:
        print(f"❌ Failed completing bot command {command_id}:", repr(e))


def _fail_command(command_id: str, error_text: str, result: Optional[Dict[str, Any]] = None) -> None:
    sb = _sb()
    if sb is None:
        print("⚠️ Supabase unavailable; cannot fail command.")
        return

    try:
        (
            sb.table("bot_commands")
            .update(
                {
                    "status": "failed",
                    "result": result or {},
                    "error": error_text[:4000],
                    "completed_at": _utc_iso(datetime.now(timezone.utc)),
                }
            )
            .eq("id", command_id)
            .execute()
        )
    except Exception as e:
        print(f"❌ Failed marking bot command {command_id} failed:", repr(e))


def _fetch_next_pending_command() -> Optional[Dict[str, Any]]:
    sb = _sb()
    if sb is None:
        print("⚠️ Supabase unavailable; cannot fetch command queue.")
        return None

    try:
        resp = (
            sb.table("bot_commands")
            .select("*")
            .eq("guild_id", _guild_id_str())
            .eq("status", "pending")
            .order("created_at", desc=False)
            .limit(1)
            .execute()
        )
        rows = getattr(resp, "data", None) or []
        return rows[0] if rows else None
    except Exception as e:
        print("❌ Failed fetching next pending bot command:", repr(e))
        return None


async def _handle_create_ticket(payload: Dict[str, Any]) -> Dict[str, Any]:
    guild = await _get_primary_guild()
    if guild is None:
        raise RuntimeError("Primary guild not found")

    member = await _get_member(guild, payload.get("user_id"))
    if member is None:
        raise RuntimeError("Requested user not found in guild")

    category = _safe_str(payload.get("category") or "support").strip() or "support"
    priority = _safe_str(payload.get("priority") or "medium").strip() or "medium"
    opening_message = payload.get("opening_message")
    is_ghost = bool(payload.get("ghost", False))
    allow_duplicate = bool(payload.get("allow_duplicate", False))

    parent_category_id = None
    try:
        if payload.get("parent_category_id") is not None:
            parent_category_id = int(str(payload["parent_category_id"]))
    except Exception:
        parent_category_id = None

    staff_role_ids = None
    if isinstance(payload.get("staff_role_ids"), list):
        parsed = []
        for rid in payload["staff_role_ids"]:
            try:
                parsed.append(int(str(rid)))
            except Exception:
                continue
        staff_role_ids = parsed or None

    if not allow_duplicate:
        existing = await find_open_ticket_for_owner(
            guild_id=guild.id,
            owner_id=member.id,
            category=("ghost" if is_ghost else category),
        )
        if existing:
            return {
                "created": False,
                "duplicate": True,
                "existing_ticket": existing,
            }

    channel = await create_ticket_channel(
        guild=guild,
        owner=member,
        category=category,
        source="dashboard_queue",
        is_ghost=is_ghost,
        parent_category_id=parent_category_id,
        staff_role_ids=staff_role_ids,
        opening_message=opening_message if isinstance(opening_message, str) else None,
        priority=priority,
    )

    if channel is None:
        raise RuntimeError("Failed to create ticket channel")

    return {
        "created": True,
        "duplicate": False,
        "ticket": {
            "channel_id": str(channel.id),
            "channel_name": channel.name,
            "guild_id": str(guild.id),
            "mention": channel.mention,
        },
    }


async def _handle_close_ticket(payload: Dict[str, Any]) -> Dict[str, Any]:
    channel = await _get_text_channel(payload.get("channel_id"))
    if channel is None:
        raise RuntimeError("Ticket channel not found")

    closed_by = None
    if payload.get("staff_id"):
        closed_by = await _get_member(channel.guild, payload.get("staff_id"))

    reason = payload.get("reason")
    ok = await mark_ticket_closed(channel=channel, closed_by=closed_by, reason=reason if isinstance(reason, str) else None)

    if not ok:
        raise RuntimeError("Failed to close ticket")

    return {
        "closed": True,
        "channel_id": str(channel.id),
    }


async def _handle_delete_ticket(payload: Dict[str, Any]) -> Dict[str, Any]:
    channel = await _get_text_channel(payload.get("channel_id"))
    if channel is None:
        raise RuntimeError("Ticket channel not found")

    deleted_by = None
    if payload.get("staff_id"):
        deleted_by = await _get_member(channel.guild, payload.get("staff_id"))

    result = await delete_ticket_with_optional_transcript(
        channel=channel,
        deleted_by=deleted_by,
        is_ghost=bool(payload.get("ghost", False)),
        force_transcript_for_ghost=bool(payload.get("force_transcript", False)),
        reason=_safe_str(payload.get("reason") or "Deleted from dashboard"),
    )

    if not result.get("ok"):
        raise RuntimeError(_safe_str(result.get("reason") or "Failed to delete ticket"))

    return result


async def _handle_reopen_ticket(payload: Dict[str, Any]) -> Dict[str, Any]:
    channel_id = payload.get("channel_id")
    if not channel_id:
        raise RuntimeError("channel_id required")

    ok = await reopen_ticket(channel_id=channel_id)
    if not ok:
        raise RuntimeError("Failed to reopen ticket")

    return {
        "reopened": True,
        "channel_id": str(channel_id),
    }


async def _handle_assign_ticket(payload: Dict[str, Any]) -> Dict[str, Any]:
    channel = await _get_text_channel(payload.get("channel_id"))
    if channel is None:
        raise RuntimeError("Ticket channel not found")

    staff = await _get_member(channel.guild, payload.get("staff_id"))
    if staff is None:
        raise RuntimeError("Staff member not found")

    ok = await assign_ticket(channel_id=channel.id, staff_member=staff)
    if not ok:
        raise RuntimeError("Failed to assign ticket")

    return {
        "assigned": True,
        "channel_id": str(channel.id),
        "staff_id": str(staff.id),
    }


async def _handle_sync_members(payload: Dict[str, Any]) -> Dict[str, Any]:
    guild = await _get_primary_guild()
    if guild is None:
        raise RuntimeError("Primary guild not found")

    summary = await run_full_member_sync_for_guild(guild)
    return {"summary": summary}


async def _handle_reconcile_departed_members(payload: Dict[str, Any]) -> Dict[str, Any]:
    guild = await _get_primary_guild()
    if guild is None:
        raise RuntimeError("Primary guild not found")

    summary = await run_departed_reconciliation_for_guild(guild)
    return {"summary": summary}


async def _handle_sync_role_members(payload: Dict[str, Any]) -> Dict[str, Any]:
    guild = await _get_primary_guild()
    if guild is None:
        raise RuntimeError("Primary guild not found")

    role_id = payload.get("role_id")
    if not role_id:
        raise RuntimeError("role_id required")

    try:
        rid = int(str(role_id))
    except Exception:
        raise RuntimeError("role_id must be a valid integer string")

    role = guild.get_role(rid)
    if role is None:
        raise RuntimeError("Role not found")

    summary = await run_role_member_sync(role)
    return {"summary": summary}


async def _execute_command(command: Dict[str, Any]) -> Dict[str, Any]:
    action = _safe_str(command.get("action")).strip()
    payload = command.get("payload") or {}
    if not isinstance(payload, dict):
        payload = {}

    if action == "create_ticket":
        return await _handle_create_ticket(payload)
    if action == "close_ticket":
        return await _handle_close_ticket(payload)
    if action == "delete_ticket":
        return await _handle_delete_ticket(payload)
    if action == "reopen_ticket":
        return await _handle_reopen_ticket(payload)
    if action == "assign_ticket":
        return await _handle_assign_ticket(payload)
    if action == "sync_members":
        return await _handle_sync_members(payload)
    if action == "reconcile_departed_members":
        return await _handle_reconcile_departed_members(payload)
    if action == "sync_role_members":
        return await _handle_sync_role_members(payload)

    raise RuntimeError(f"Unsupported bot command action: {action}")


async def _queue_loop() -> None:
    await bot.wait_until_ready()
    print("🧠 Command queue worker started.")

    while not bot.is_closed():
        try:
            command = _fetch_next_pending_command()

            if not command:
                await asyncio.sleep(_POLL_SECONDS)
                continue

            command_id = _safe_str(command.get("id"))
            if not command_id:
                await asyncio.sleep(_POLL_SECONDS)
                continue

            claimed = _claim_command(command_id)
            if not claimed:
                await asyncio.sleep(0.25)
                continue

            try:
                result = await _execute_command(command)
                _complete_command(command_id, result)
                print(f"✅ bot_commands completed → {command.get('action')} ({command_id})")
            except Exception as e:
                _fail_command(command_id, repr(e))
                print(f"❌ bot_commands failed → {command.get('action')} ({command_id}):", repr(e))

        except Exception as e:
            print("❌ Command queue loop error:", repr(e))

        await asyncio.sleep(_POLL_SECONDS)


def start_command_queue_worker() -> None:
    global _QUEUE_TASK, _QUEUE_STARTED

    if _QUEUE_STARTED:
        print("⚠️ Command queue worker already started; skipping duplicate start.")
        return

    _QUEUE_STARTED = True
    _QUEUE_TASK = asyncio.create_task(_queue_loop())
    print("✅ Command queue worker scheduled.")