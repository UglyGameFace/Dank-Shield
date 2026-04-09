# stoney_verify/bot_actions_api.py
from __future__ import annotations

import os
import asyncio
from typing import Any, Dict, Optional, Tuple

from aiohttp import web
import discord

try:
    from .globals import (
        VERIFIED_ROLE_ID,
        RESIDENT_ROLE_ID,
        STAFF_ROLE_ID,
        VC_ACCESS_TASKS,
        log,
        supabase,
    )
except Exception:
    VERIFIED_ROLE_ID = None  # type: ignore
    RESIDENT_ROLE_ID = None  # type: ignore
    STAFF_ROLE_ID = None  # type: ignore
    VC_ACCESS_TASKS = {}  # type: ignore
    log = None  # type: ignore
    supabase = None  # type: ignore

try:
    from .tickets_new.sync_service import sync_active_ticket_channels_for_guild
except Exception:
    sync_active_ticket_channels_for_guild = None  # type: ignore


def _log(msg: str) -> None:
    if callable(log):
        try:
            log(msg)
            return
        except Exception:
            pass
    print(msg)


def _env(name: str, default: str = "") -> str:
    return str(os.getenv(name, default) or "").strip()


def _truthy(v: str) -> bool:
    return str(v or "").strip().lower() in ("1", "true", "yes", "y", "on")


def _auth_ok(request: web.Request) -> bool:
    """
    If BOT_ACTIONS_SECRET is set, require Authorization: Bearer <secret>.
    If not set, allow requests so the dashboard can still work during setup.
    """
    secret = _env("BOT_ACTIONS_SECRET")
    if not secret:
        return True

    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return False

    token = auth.removeprefix("Bearer ").strip()
    return token == secret


async def _read_json(request: web.Request) -> Dict[str, Any]:
    try:
        body = await request.json()
        return body if isinstance(body, dict) else {}
    except Exception:
        return {}


async def _fetch_token_row(token: str) -> Optional[Dict[str, Any]]:
    if not supabase:
        return None

    try:
        res = (
            supabase.table("verification_tokens")
            .select("*")
            .eq("token", token)
            .limit(1)
            .execute()
        )
        data = getattr(res, "data", None)
        if isinstance(data, list) and data:
            return data[0]
    except Exception as e:
        _log(f"⚠️ bot_actions_api: supabase fetch failed: {e}")

    return None


async def _update_token_decision(
    token: str,
    decision: str,
    staff_id: str,
    staff_name: str,
) -> bool:
    if not supabase:
        return False

    try:
        import datetime

        decided_at = datetime.datetime.now(datetime.timezone.utc).isoformat()

        payload = {
            "decision": decision,
            "decided_by": staff_id,
            "decided_at": decided_at,
            "used": decision in ("APPROVED", "DENIED"),
            "updated_at": decided_at,
        }

        supabase.table("verification_tokens").update(payload).eq("token", token).execute()
        return True
    except Exception as e:
        _log(f"⚠️ bot_actions_api: supabase update failed: {e}")
        return False


async def _insert_audit(
    action: str,
    token: str,
    staff_id: str,
    meta: Dict[str, Any],
) -> None:
    if not supabase:
        return

    try:
        supabase.table("audit_logs").insert(
            {
                "action": action,
                "token": token,
                "staff_id": staff_id,
                "meta": meta,
            }
        ).execute()
    except Exception as e:
        _log(f"⚠️ bot_actions_api: audit insert failed: {e}")


async def _delete_kick_timer(channel_id: str) -> None:
    if not supabase:
        return

    try:
        supabase.table("verification_kick_timers").delete().eq("channel_id", channel_id).execute()
    except Exception as e:
        _log(f"⚠️ bot_actions_api: kick timer delete failed: {e}")


async def _apply_roles(
    guild: discord.Guild,
    user_id: str,
    decision: str,
) -> Tuple[bool, str]:
    member = guild.get_member(int(user_id))
    if member is None:
        try:
            member = await guild.fetch_member(int(user_id))
        except Exception:
            member = None

    if not member:
        return False, "member_not_found"

    if decision != "APPROVED":
        return True, "no_role_change"

    verified_id = _env("VERIFIED_ROLE_ID", str(VERIFIED_ROLE_ID or "")).strip()
    resident_id = _env("RESIDENT_ROLE_ID", str(RESIDENT_ROLE_ID or "")).strip()

    if not verified_id:
        return False, "VERIFIED_ROLE_ID_missing"

    roles_to_add = []

    r_verified = guild.get_role(int(verified_id)) if verified_id.isdigit() else None
    if r_verified:
        roles_to_add.append(r_verified)

    if resident_id and resident_id.isdigit():
        r_res = guild.get_role(int(resident_id))
        if r_res:
            roles_to_add.append(r_res)

    if not roles_to_add:
        return False, "roles_not_found"

    try:
        await member.add_roles(*roles_to_add, reason="Stoney Verify: approved via dashboard")
        return True, "roles_added"
    except Exception as e:
        return False, f"add_roles_failed: {e}"


async def _maybe_kick_on_deny(
    guild: discord.Guild,
    user_id: str,
) -> Tuple[bool, str]:
    if not _truthy(_env("KICK_ON_DENY", "false")):
        return True, "kick_disabled"

    try:
        member = guild.get_member(int(user_id))
        if member is None:
            member = await guild.fetch_member(int(user_id))
        if not member:
            return False, "member_not_found"

        await member.kick(reason="Stoney Verify: denied via dashboard")
        return True, "kicked"
    except Exception as e:
        return False, f"kick_failed: {e}"


async def _close_ticket_channel(
    bot: discord.Client,
    guild: discord.Guild,
    channel_id: Optional[str],
    decision: str,
    user_id: Optional[str],
) -> Tuple[bool, str]:
    if not channel_id or not str(channel_id).isdigit():
        return True, "no_channel"

    ch = guild.get_channel(int(channel_id))
    if ch is None:
        try:
            ch = await bot.fetch_channel(int(channel_id))  # type: ignore[arg-type]
        except Exception:
            ch = None

    if not isinstance(ch, discord.TextChannel):
        return False, "channel_not_found"

    try:
        await ch.send(
            f"🧾 **Dashboard decision:** `{decision}`"
            + (f" for <@{user_id}>" if user_id else "")
            + "\n\nThis ticket will close automatically."
        )
    except Exception:
        pass

    delay_s = int(_env("TICKET_DELETE_DELAY_SECONDS", "2") or "2")
    if delay_s < 0:
        delay_s = 0

    await asyncio.sleep(delay_s)

    try:
        await ch.delete(reason=f"Stoney Verify: {decision} via dashboard")
        return True, "channel_deleted"
    except Exception as e:
        return False, f"channel_delete_failed: {e}"


async def _cancel_vc_access_task(token: str) -> None:
    try:
        task = VC_ACCESS_TASKS.get(str(token))
        if task and not task.done():
            task.cancel()
        VC_ACCESS_TASKS.pop(str(token), None)
    except Exception:
        pass


def _resolve_guild_from_row(
    bot: discord.Client,
    row: Dict[str, Any],
) -> Optional[discord.Guild]:
    gid = str(row.get("guild_id") or "").strip()
    if gid.isdigit():
        guild = bot.get_guild(int(gid))
        if guild:
            return guild

    try:
        return next(iter(bot.guilds), None)
    except Exception:
        return None


async def handle_decision(
    bot: discord.Client,
    token: str,
    decision: str,
    staff_id: str,
    staff_name: str,
) -> Dict[str, Any]:
    decision = (decision or "").strip().upper()
    if decision in ("APPROVE", "APPROVED"):
        decision = "APPROVED"
    elif decision in ("DENY", "DENIED"):
        decision = "DENIED"

    row = await _fetch_token_row(token)
    if not row:
        return {"ok": False, "error": "token_not_found_or_supabase_off"}

    guild = _resolve_guild_from_row(bot, row)
    if not guild:
        return {"ok": False, "error": "guild_not_found"}

    user_id = str(row.get("requester_id") or row.get("user_id") or "").strip() or None
    channel_id = str(row.get("channel_id") or "").strip() or None

    results: Dict[str, Any] = {
        "token": token,
        "decision": decision,
        "guild_id": str(guild.id),
        "user_id": user_id,
        "channel_id": channel_id,
        "role_action": None,
        "kick_action": None,
        "ticket_action": None,
        "timer_action": None,
        "vc_task_action": None,
        "supabase_update": None,
    }

    updated = await _update_token_decision(token, decision, staff_id, staff_name)
    results["supabase_update"] = bool(updated)

    if channel_id:
        await _delete_kick_timer(channel_id)
        results["timer_action"] = "timer_deleted"

    await _cancel_vc_access_task(token)
    results["vc_task_action"] = "vc_task_cancelled"

    if user_id:
        ok_roles, msg_roles = await _apply_roles(guild, user_id, decision)
        results["role_action"] = {"ok": ok_roles, "msg": msg_roles}

        ok_kick, msg_kick = await _maybe_kick_on_deny(guild, user_id)
        results["kick_action"] = {"ok": ok_kick, "msg": msg_kick}

    ok_ticket, msg_ticket = await _close_ticket_channel(bot, guild, channel_id, decision, user_id)
    results["ticket_action"] = {"ok": ok_ticket, "msg": msg_ticket}

    await _insert_audit(
        action="bot_decision_applied",
        token=token,
        staff_id=staff_id,
        meta={
            "staff_name": staff_name,
            "decision": decision,
            "guild_id": row.get("guild_id"),
            "channel_id": row.get("channel_id"),
            "requester_id": row.get("requester_id"),
            "user_id": row.get("user_id"),
            "results": results,
        },
    )

    return {"ok": True, "results": results}


async def _handle_sync_active_tickets(
    bot: discord.Client,
    body: Dict[str, Any],
) -> Dict[str, Any]:
    if sync_active_ticket_channels_for_guild is None:
        return {"ok": False, "error": "ticket_sync_unavailable"}

    guild_id = str(body.get("guild_id") or "").strip()
    include_closed_visible_channels = bool(
        body.get("include_closed_visible_channels", True)
    )
    dry_run = bool(body.get("dry_run", False))

    guild: Optional[discord.Guild] = None

    if guild_id.isdigit():
        guild = bot.get_guild(int(guild_id))
        if guild is None:
            try:
                await bot.fetch_guild(int(guild_id))
                guild = bot.get_guild(int(guild_id))
            except Exception:
                guild = None

    if guild is None:
        try:
            guild = next(iter(bot.guilds), None)
        except Exception:
            guild = None

    if guild is None:
        return {"ok": False, "error": "guild_not_found"}

    try:
        summary = await sync_active_ticket_channels_for_guild(
            guild,
            source="dashboard_ticket_sync",
            include_closed_visible_channels=include_closed_visible_channels,
            dry_run=dry_run,
        )
        return {"ok": True, "summary": summary}
    except Exception as e:
        _log(f"❌ bot_actions_api ticket sync exception: {e}")
        return {"ok": False, "error": f"ticket_sync_exception: {e}"}


def create_app(bot: discord.Client) -> web.Application:
    app = web.Application()

    async def health(_: web.Request):
        return web.json_response(
            {
                "ok": True,
                "service": "stoney-verify-bot-actions",
                "port": int(_env("BOT_ACTIONS_PORT", "8080") or "8080"),
            }
        )

    async def decision(request: web.Request):
        if not _auth_ok(request):
            return web.json_response({"ok": False, "error": "unauthorized"}, status=401)

        body = await _read_json(request)

        token = str(body.get("token") or "").strip()
        decision_value = str(body.get("decision") or "").strip()
        staff_id = str(body.get("staffId") or body.get("staff_id") or "").strip()
        staff_name = str(body.get("staffName") or body.get("staff_name") or "").strip()

        if not token or not decision_value or not staff_id:
            return web.json_response(
                {
                    "ok": False,
                    "error": "missing_fields",
                    "need": ["token", "decision", "staffId"],
                },
                status=400,
            )

        try:
            out = await handle_decision(
                bot,
                token,
                decision_value,
                staff_id,
                staff_name or staff_id,
            )
            return web.json_response(out, status=200 if out.get("ok") else 400)
        except Exception as e:
            _log(f"❌ bot_actions_api decision exception: {e}")
            return web.json_response(
                {"ok": False, "error": f"exception: {e}"},
                status=500,
            )

    async def sync_active_tickets(request: web.Request):
        if not _auth_ok(request):
            return web.json_response({"ok": False, "error": "unauthorized"}, status=401)

        body = await _read_json(request)
        out = await _handle_sync_active_tickets(bot, body)
        return web.json_response(out, status=200 if out.get("ok") else 400)

    # Health
    app.router.add_get("/health", health)
    app.router.add_get("/api/health", health)

    # Existing decision route
    app.router.add_post("/api/verify/decision", decision)

    # Ticket sync on public 8080 app
    app.router.add_post("/tickets/sync-active", sync_active_tickets)
    app.router.add_post("/api/tickets/sync-active", sync_active_tickets)

    return app


async def start_bot_actions_server(bot: discord.Client) -> None:
    """
    Call this once AFTER bot is ready.

    Env:
      BOT_ACTIONS_PORT (default 8080)
      BOT_ACTIONS_HOST (default 0.0.0.0)
      BOT_ACTIONS_SECRET (optional)
    """
    port = int(_env("BOT_ACTIONS_PORT", "8080") or "8080")
    host = _env("BOT_ACTIONS_HOST", "0.0.0.0")

    app = create_app(bot)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host=host, port=port)
    await site.start()

    _log(f"🌐 Bot Actions API online at http://{host}:{port}/api/verify/decision")
    _log(f"🌐 Bot Actions public health at http://{host}:{port}/health")
    _log(f"🌐 Bot Actions ticket sync at http://{host}:{port}/tickets/sync-active")