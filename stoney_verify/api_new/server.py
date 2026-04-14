from __future__ import annotations

from typing import Any, Dict, Optional

from aiohttp import web

import discord

from ..globals import bot
from ..tickets_new.service import (
    create_ticket_channel,
    find_open_ticket_for_owner,
    mark_ticket_closed,
    reopen_ticket,
    assign_ticket,
)
from ..tickets_new.transcript_service import delete_ticket_with_optional_transcript
from ..tickets_new.sync_service import (
    sync_active_ticket_channels_for_guild,
    sync_one_ticket_channel,
)
from ..events_new.members import (
    run_full_member_sync_for_guild,
    run_departed_reconciliation_for_guild,
    run_role_member_sync,
)

_API_RUNNER: Optional[web.AppRunner] = None
_API_SITE: Optional[web.TCPSite] = None


def _json_error(msg: str, code: int = 400, **extra: Any):
    payload: Dict[str, Any] = {"ok": False, "error": msg}
    payload.update(extra)
    return web.json_response(payload, status=code)


def _json_ok(**extra: Any):
    payload: Dict[str, Any] = {"ok": True}
    payload.update(extra)
    return web.json_response(payload)


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(str(value).strip())
    except Exception:
        return default


def _safe_str(value: Any) -> str:
    try:
        return str(value)
    except Exception:
        return ""


def _channel_to_payload(channel: discord.TextChannel) -> Dict[str, Any]:
    return {
        "channel_id": str(channel.id),
        "channel_name": channel.name,
        "guild_id": str(channel.guild.id),
        "mention": channel.mention,
        "category_id": str(channel.category.id) if channel.category else None,
        "category_name": channel.category.name if channel.category else None,
    }


async def _get_guild_or_error(guild_id: Any) -> tuple[Optional[discord.Guild], Optional[web.Response]]:
    if not guild_id:
        return None, _json_error("guild_id required")

    gid = _safe_int(guild_id, 0)
    if gid <= 0:
        return None, _json_error("guild_id must be a valid integer string")

    guild = bot.get_guild(gid)
    if guild is None:
        try:
            await bot.fetch_guild(gid)
            guild = bot.get_guild(gid)
        except Exception:
            guild = None

    if guild is None:
        return None, _json_error("Guild not found", 404)

    return guild, None


async def _get_member_from_guild(
    guild: discord.Guild,
    user_id: Any,
) -> tuple[Optional[discord.Member], Optional[web.Response]]:
    if not user_id:
        return None, _json_error("user_id required")

    uid = _safe_int(user_id, 0)
    if uid <= 0:
        return None, _json_error("user_id must be a valid integer string")

    member = guild.get_member(uid)
    if member is None:
        try:
            member = await guild.fetch_member(uid)
        except Exception:
            member = None

    if member is None:
        return None, _json_error("User not found in guild", 404)

    return member, None


async def _get_text_channel(
    channel_id: Any,
) -> tuple[Optional[discord.TextChannel], Optional[web.Response]]:
    if not channel_id:
        return None, _json_error("channel_id required")

    cid = _safe_int(channel_id, 0)
    if cid <= 0:
        return None, _json_error("channel_id must be a valid integer string")

    channel = bot.get_channel(cid)
    if channel is None:
        try:
            channel = await bot.fetch_channel(cid)
        except Exception:
            channel = None

    if not isinstance(channel, discord.TextChannel):
        return None, _json_error("Channel not found or is not a text channel", 404)

    return channel, None


async def create_ticket(request: web.Request):
    try:
        data: Dict[str, Any] = await request.json()
    except Exception:
        return _json_error("Invalid JSON body")

    guild, err = await _get_guild_or_error(data.get("guild_id"))
    if err:
        return err
    assert guild is not None

    member, err = await _get_member_from_guild(guild, data.get("user_id"))
    if err:
        return err
    assert member is not None

    category = _safe_str(data.get("category") or "support").strip() or "support"
    is_ghost = bool(data.get("ghost", False))
    opening_message = data.get("opening_message")
    priority = _safe_str(data.get("priority") or "medium").strip().lower() or "medium"

    parent_category_id = None
    if data.get("parent_category_id") is not None:
        parsed_parent = _safe_int(data.get("parent_category_id"), 0)
        parent_category_id = parsed_parent if parsed_parent > 0 else None

    staff_role_ids = None
    if isinstance(data.get("staff_role_ids"), list):
        parsed: list[int] = []
        for rid in data["staff_role_ids"]:
            role_id = _safe_int(rid, 0)
            if role_id > 0:
                parsed.append(role_id)
        staff_role_ids = parsed or None

    allow_duplicate = bool(data.get("allow_duplicate", False))
    normalized_category = "ghost" if is_ghost else category

    if not allow_duplicate:
        existing = await find_open_ticket_for_owner(
            guild_id=guild.id,
            owner_id=member.id,
            category=normalized_category,
        )
        if existing:
            return _json_ok(
                created=False,
                duplicate=True,
                existing_ticket=existing,
            )

    channel = await create_ticket_channel(
        guild=guild,
        owner=member,
        category=category,
        source="dashboard",
        is_ghost=is_ghost,
        parent_category_id=parent_category_id,
        staff_role_ids=staff_role_ids,
        opening_message=opening_message,
        priority=priority,
    )

    if channel is None:
        return _json_error("Failed to create ticket", 500)

    return _json_ok(
        created=True,
        duplicate=False,
        ticket=_channel_to_payload(channel),
    )


async def close_ticket(request: web.Request):
    try:
        data = await request.json()
    except Exception:
        return _json_error("Invalid JSON body")

    channel, err = await _get_text_channel(data.get("channel_id"))
    if err:
        return err
    assert channel is not None

    closed_by = None
    staff_id = data.get("staff_id")
    if staff_id:
        closed_by, _ = await _get_member_from_guild(channel.guild, staff_id)

    reason = data.get("reason")
    ok = await mark_ticket_closed(
        channel=channel,
        closed_by=closed_by,
        reason=reason,
    )

    if not ok:
        return _json_error("Failed to mark ticket closed", 500)

    return _json_ok(closed=True, channel_id=str(channel.id))


async def reopen_ticket_endpoint(request: web.Request):
    try:
        data = await request.json()
    except Exception:
        return _json_error("Invalid JSON body")

    channel_id = data.get("channel_id")
    if not channel_id:
        return _json_error("channel_id required")

    actor = None
    actor_id = data.get("actor_id") or data.get("staff_id")
    if actor_id:
        channel, err = await _get_text_channel(channel_id)
        if err:
            return err
        assert channel is not None
        actor, _ = await _get_member_from_guild(channel.guild, actor_id)

    ok = await reopen_ticket(
        channel_id=channel_id,
        actor=actor,
        reason=data.get("reason"),
    )
    if not ok:
        return _json_error("Failed to reopen ticket", 500)

    return _json_ok(reopened=True, channel_id=str(channel_id))


async def assign_ticket_endpoint(request: web.Request):
    try:
        data = await request.json()
    except Exception:
        return _json_error("Invalid JSON body")

    channel_id = data.get("channel_id")
    staff_id = data.get("staff_id")

    if not channel_id:
        return _json_error("channel_id required")
    if not staff_id:
        return _json_error("staff_id required")

    channel, err = await _get_text_channel(channel_id)
    if err:
        return err
    assert channel is not None

    staff, err = await _get_member_from_guild(channel.guild, staff_id)
    if err:
        return err
    assert staff is not None

    ok = await assign_ticket(channel_id=channel.id, staff_member=staff)
    if not ok:
        return _json_error("Failed to assign ticket", 500)

    return _json_ok(assigned=True, channel_id=str(channel.id), staff_id=str(staff.id))


async def delete_ticket(request: web.Request):
    try:
        data = await request.json()
    except Exception:
        return _json_error("Invalid JSON body")

    channel, err = await _get_text_channel(data.get("channel_id"))
    if err:
        return err
    assert channel is not None

    ghost = bool(data.get("ghost", False))
    force_transcript = bool(data.get("force_transcript", False))
    reason = data.get("reason") or "Deleted from dashboard"

    deleted_by = None
    staff_id = data.get("staff_id")
    if staff_id:
        deleted_by, _ = await _get_member_from_guild(channel.guild, staff_id)

    result = await delete_ticket_with_optional_transcript(
        channel=channel,
        deleted_by=deleted_by,
        is_ghost=ghost,
        force_transcript_for_ghost=force_transcript,
        reason=reason,
    )

    if isinstance(result, dict):
        return web.json_response(result)

    return _json_ok(deleted=bool(result), channel_id=str(channel.id))


async def sync_active_tickets(request: web.Request):
    try:
        data: Dict[str, Any] = await request.json()
    except Exception:
        data = {}

    guild, err = await _get_guild_or_error(data.get("guild_id"))
    if err:
        return err
    assert guild is not None

    include_closed_visible_channels = bool(
        data.get("include_closed_visible_channels", True)
    )
    dry_run = bool(data.get("dry_run", False))

    summary = await sync_active_ticket_channels_for_guild(
        guild,
        source="dashboard_ticket_sync",
        include_closed_visible_channels=include_closed_visible_channels,
        dry_run=dry_run,
    )

    return _json_ok(summary=summary)


async def sync_one_ticket(request: web.Request):
    try:
        data: Dict[str, Any] = await request.json()
    except Exception:
        return _json_error("Invalid JSON body")

    channel, err = await _get_text_channel(data.get("channel_id"))
    if err:
        return err
    assert channel is not None

    dry_run = bool(data.get("dry_run", False))

    summary = await sync_one_ticket_channel(
        channel,
        source="dashboard_ticket_sync_one",
        dry_run=dry_run,
    )

    return _json_ok(summary=summary)


async def force_member_sync(request: web.Request):
    try:
        data = await request.json()
    except Exception:
        return _json_error("Invalid JSON body")

    guild, err = await _get_guild_or_error(data.get("guild_id"))
    if err:
        return err
    assert guild is not None

    summary = await run_full_member_sync_for_guild(guild)
    return _json_ok(summary=summary)


async def reconcile_departed(request: web.Request):
    try:
        data = await request.json()
    except Exception:
        return _json_error("Invalid JSON body")

    guild, err = await _get_guild_or_error(data.get("guild_id"))
    if err:
        return err
    assert guild is not None

    summary = await run_departed_reconciliation_for_guild(guild)
    return _json_ok(summary=summary)


async def role_member_sync(request: web.Request):
    try:
        data = await request.json()
    except Exception:
        return _json_error("Invalid JSON body")

    guild, err = await _get_guild_or_error(data.get("guild_id"))
    if err:
        return err
    assert guild is not None

    role_id = data.get("role_id")
    if not role_id:
        return _json_error("role_id required")

    rid = _safe_int(role_id, 0)
    if rid <= 0:
        return _json_error("role_id must be a valid integer string")

    role = guild.get_role(rid)
    if role is None:
        return _json_error("Role not found", 404)

    summary = await run_role_member_sync(role)
    return _json_ok(summary=summary)


async def health(request: web.Request):
    guild_count = 0
    try:
        guild_count = len(getattr(bot, "guilds", []) or [])
    except Exception:
        guild_count = 0

    return _json_ok(
        status="online",
        guild_count=guild_count,
        api="structured_bot_api",
    )


async def start_api(bot_instance: discord.Client):
    global _API_RUNNER, _API_SITE

    if _API_RUNNER is not None:
        print("⚠️ New structured Bot API already running; skipping duplicate start.")
        return

    app = web.Application()

    app.router.add_get("/health", health)

    app.router.add_post("/ticket/create", create_ticket)
    app.router.add_post("/ticket/close", close_ticket)
    app.router.add_post("/ticket/delete", delete_ticket)
    app.router.add_post("/ticket/reopen", reopen_ticket_endpoint)
    app.router.add_post("/ticket/assign", assign_ticket_endpoint)

    app.router.add_post("/tickets/sync-active", sync_active_tickets)
    app.router.add_post("/tickets/sync-one", sync_one_ticket)

    app.router.add_post("/members/sync", force_member_sync)
    app.router.add_post("/members/reconcile", reconcile_departed)
    app.router.add_post("/members/role-sync", role_member_sync)

    runner = web.AppRunner(app)
    await runner.setup()

    site = web.TCPSite(runner, "0.0.0.0", 8081)
    await site.start()

    _API_RUNNER = runner
    _API_SITE = site

    print("🌐 New structured Bot API started on port 8081")
