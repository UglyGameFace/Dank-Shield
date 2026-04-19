from __future__ import annotations

import asyncio
import os
import re
from typing import Any, Dict, Optional, Tuple, List

import discord
from discord import app_commands

from ..globals import *  # noqa: F401,F403
from ..tickets import find_ticket_owner_retry

from .common import (
    VC_ACCESS_TASKS,
    VC_REQUESTS,
    RUNTIME_STATS,
    _discord_channel_url,
    _staff_check,
    _staff_ping_text,
    _track_task,
    make_custom_id,
)

try:
    from ..store import (
        sb_get_token_info,
        sb_mark_decision,
        sb_set_used,
    )
except Exception:
    def sb_get_token_info(token: str) -> Optional[Dict[str, Any]]:  # type: ignore
        return None

    def sb_mark_decision(
        token: str,
        decision: str,
        staff_id: int,
        approved_user_id: Optional[int] = None,
    ) -> None:  # type: ignore
        return None

    def sb_set_used(token: str, used: bool = True) -> None:  # type: ignore
        return None


# ============================================================
# Session / VC compatibility wiring
# ============================================================
try:
    from .. import vc_verify as _vc_verify_mod  # type: ignore
except Exception:
    _vc_verify_mod = None  # type: ignore

try:
    from .. import vc_sessions as _vc_sessions_mod  # type: ignore
except Exception:
    _vc_sessions_mod = None  # type: ignore


# ============================================================
# Constants / action names
# ============================================================
VC_STAFF_ACTIONS = {
    "vc_start",
    "vc_complete",
    "vc_cancel",
    "vc_upload",
    "vc_end",
    "vc_reissue",
    "vc_approve",
    "vc_denyclose",
    "vc_accept",
}

VC_ACTIVE_STATUSES = {
    "PENDING",
    "ACCEPTED",
    "STAFF_ACCEPTED",
    "READY",
    "IN_VC",
    "STARTED",
    "TAKEN_OVER",
    "RESTARTED",
}

VC_TERMINAL_STATUSES = {
    "COMPLETED",
    "CANCELED",
    "DENIED",
    "UPLOAD_REQUESTED",
    "EXPIRED",
    "STALE",
}

DEFAULT_VC_VERIFY_REQUESTS_CHANNEL_ID = 1476977094729793710
_VC_REQUEST_LOCKS: Dict[str, asyncio.Lock] = {}


# ============================================================
# Helpers
# ============================================================
def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(str(value).strip())
    except Exception:
        return default


def _safe_str(value: Any) -> str:
    try:
        return str(value or "").strip()
    except Exception:
        return ""


def _request_lock(token: str) -> asyncio.Lock:
    key = _safe_str(token)
    lock = _VC_REQUEST_LOCKS.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _VC_REQUEST_LOCKS[key] = lock
    return lock


def _vc_requests_channel_id() -> int:
    for key in ("VC_VERIFY_REQUESTS_CHANNEL_ID", "VC_VERIFY_QUEUE_CHANNEL_ID"):
        v = os.getenv(key)
        if v and str(v).strip().isdigit():
            return int(str(v).strip())

    try:
        if VC_VERIFY_QUEUE_CHANNEL_ID and int(VC_VERIFY_QUEUE_CHANNEL_ID) != 0:
            return int(VC_VERIFY_QUEUE_CHANNEL_ID)
    except Exception:
        pass

    return int(DEFAULT_VC_VERIFY_REQUESTS_CHANNEL_ID)


def _get_vc_channel(guild: discord.Guild) -> Optional[discord.VoiceChannel]:
    try:
        vc_id = int(globals().get("VC_VERIFY_CHANNEL_ID", 0) or 0)
        if vc_id <= 0:
            vc_id = int(globals().get("VC_VERIFY_VC_ID", 0) or 0)

        if vc_id <= 0:
            print("⚠️ VC verify channel id is not configured.")
            return None

        ch = guild.get_channel(vc_id)
        if isinstance(ch, discord.VoiceChannel):
            return ch

        print(f"⚠️ Channel {vc_id} is not a voice channel (type={type(ch).__name__}).")
        return None
    except Exception as e:
        print(f"⚠️ Error resolving VC channel: {e}")
        return None


async def _resolve_text_channel(
    guild: discord.Guild,
    channel_id: int,
) -> Optional[discord.abc.Messageable]:
    if not channel_id:
        return None

    def _ok(ch: object) -> bool:
        try:
            if not hasattr(ch, "guild") or getattr(ch, "guild", None) != guild:
                return False
            if not hasattr(ch, "send") or not callable(getattr(ch, "send")):
                return False
            return True
        except Exception:
            return False

    try:
        ch = guild.get_channel(int(channel_id))
        if ch and _ok(ch):
            return ch  # type: ignore[return-value]
    except Exception:
        pass

    try:
        ch2 = await guild.fetch_channel(int(channel_id))
        if ch2 and _ok(ch2):
            return ch2  # type: ignore[return-value]
    except Exception:
        pass

    return None


async def _get_vc_queue_channel(guild: discord.Guild) -> Optional[discord.abc.Messageable]:
    try:
        cid = _vc_requests_channel_id()
        return await _resolve_text_channel(guild, int(cid or 0))
    except Exception:
        return None


async def _get_staff_alert_channel(guild: discord.Guild) -> Optional[discord.abc.Messageable]:
    q = await _get_vc_queue_channel(guild)
    if q:
        return q

    try:
        if MODLOG_CHANNEL_ID:
            ch = await _resolve_text_channel(guild, int(MODLOG_CHANNEL_ID))
            if ch:
                return ch
    except Exception:
        pass

    try:
        if TRANSCRIPTS_CHANNEL_ID:
            ch = await _resolve_text_channel(guild, int(TRANSCRIPTS_CHANNEL_ID))
            if ch:
                return ch
    except Exception:
        pass

    return None


def _can_manage_channel(
    me: discord.Member,
    channel: discord.abc.GuildChannel,
) -> Tuple[bool, str]:
    try:
        if not me:
            return False, "Bot member not found in guild."
        perms = channel.permissions_for(me)
        if perms.administrator:
            return True, ""
        if not perms.manage_channels:
            return False, f"Bot lacks 'Manage Channels' in {channel.mention} (or category)."
        return True, ""
    except Exception as e:
        return False, f"Error checking permissions: {e}"


async def _resolve_ticket_channel_from_token_info(
    guild: discord.Guild,
    token_info: Dict[str, Any],
) -> Optional[discord.TextChannel]:
    ch_id = _safe_int(token_info.get("channel_id"), 0)
    if ch_id <= 0:
        return None

    try:
        ch = guild.get_channel(ch_id)
        if isinstance(ch, discord.TextChannel):
            return ch
    except Exception:
        pass

    try:
        fetched = await guild.fetch_channel(ch_id)
        if isinstance(fetched, discord.TextChannel):
            return fetched
    except Exception:
        pass

    return None


def _build_vc_staff_embed(
    *,
    guild: discord.Guild,
    requester_id: int,
    requester_mention: str,
    ticket_channel_id: int,
    token: str,
) -> discord.Embed:
    member = guild.get_member(int(requester_id))

    if member:
        user_display = f"{member.mention} — **{member.display_name}**"
    else:
        user_display = requester_mention or f"<@{requester_id}>"

    emb = discord.Embed(
        title="🎙️ VC Verification Requested",
        description="Staff-only panel — choose how to handle this VC request.",
        color=discord.Color.dark_green(),
        timestamp=now_utc(),
    )

    emb.add_field(
        name="User",
        value=f"{user_display}\n`{requester_id}`",
        inline=False,
    )
    emb.add_field(
        name="Ticket",
        value=f"<#{int(ticket_channel_id)}>\n`{ticket_channel_id}`",
        inline=True,
    )

    vc_id = _safe_int(globals().get("VC_VERIFY_CHANNEL_ID", 0) or globals().get("VC_VERIFY_VC_ID", 0), 0)
    emb.add_field(
        name="VC Channel",
        value=(
            f"<#{vc_id}>\n`{vc_id}`"
            if vc_id > 0
            else "`Not configured`"
        ),
        inline=True,
    )
    emb.add_field(
        name="Token",
        value=f"`{token}`",
        inline=False,
    )

    vc_footer_ttl = int(
        globals().get(
            "VC_TOKEN_TTL_MINUTES",
            globals().get("VC_REQUEST_TTL_MINUTES", TOKEN_TTL_MINUTES or 20),
        ) or 20
    )
    emb.set_footer(text=f"Stoney Verify • VC staff panel | TTL {vc_footer_ttl}m")
    return emb


def _build_staff_vc_request_view(token: str) -> discord.ui.View:
    staff_view = discord.ui.View(timeout=None)
    staff_view.add_item(
        discord.ui.Button(
            label="✅ Accept VC Verify",
            style=discord.ButtonStyle.success,
            custom_id=make_custom_id("vc_accept", token),
        )
    )
    staff_view.add_item(
        discord.ui.Button(
            label="🔁 Ask for Upload Instead",
            style=discord.ButtonStyle.secondary,
            custom_id=make_custom_id("vc_upload", token),
        )
    )
    staff_view.add_item(
        discord.ui.Button(
            label="♻️ Reissue Token",
            style=discord.ButtonStyle.secondary,
            custom_id=make_custom_id("vc_reissue", token),
        )
    )
    return staff_view


async def _post_staff_vc_request_panel(
    *,
    guild: discord.Guild,
    token: str,
    requester_id: int,
    requester_mention: str,
    ticket_channel_id: int,
) -> Optional[int]:
    staff_view = _build_staff_vc_request_view(token)
    emb = _build_vc_staff_embed(
        guild=guild,
        requester_id=int(requester_id),
        requester_mention=requester_mention,
        ticket_channel_id=int(ticket_channel_id),
        token=token,
    )

    ping = _staff_ping_text()
    content = ping if ping else None

    candidate_ids: List[int] = []

    try:
        candidate_ids.append(int(_vc_requests_channel_id()))
    except Exception:
        pass

    try:
        if MODLOG_CHANNEL_ID:
            candidate_ids.append(int(MODLOG_CHANNEL_ID))
    except Exception:
        pass

    try:
        if TRANSCRIPTS_CHANNEL_ID:
            candidate_ids.append(int(TRANSCRIPTS_CHANNEL_ID))
    except Exception:
        pass

    try:
        candidate_ids.append(int(ticket_channel_id))
    except Exception:
        pass

    tried: set[int] = set()

    for cid in candidate_ids:
        if not cid or cid in tried:
            continue
        tried.add(cid)

        ch = await _resolve_text_channel(guild, int(cid))
        if not ch:
            continue

        try:
            me = guild.me
            if me and hasattr(ch, "permissions_for"):
                perms = ch.permissions_for(me)  # type: ignore[attr-defined]
                if not (
                    getattr(perms, "view_channel", True)
                    and getattr(perms, "send_messages", True)
                ):
                    print(f"⚠️ VC staff panel: bot lacks view/send perms in {getattr(ch, 'id', None)}")
                    continue
        except Exception:
            pass

        try:
            msg = await ch.send(content=content, embed=emb, view=staff_view)  # type: ignore[misc]
            try:
                VC_REQUESTS.setdefault(token, {})
                VC_REQUESTS[token].setdefault("staff_msg_refs", [])
                VC_REQUESTS[token]["staff_msg_refs"].append(
                    {
                        "channel_id": int(getattr(getattr(msg, "channel", None), "id", 0) or 0),
                        "message_id": int(getattr(msg, "id", 0) or 0),
                    }
                )
                VC_REQUESTS[token]["staff_msg_ids"] = [
                    int(ref["message_id"])
                    for ref in VC_REQUESTS[token].get("staff_msg_refs", [])
                    if isinstance(ref, dict) and int(ref.get("message_id", 0) or 0) > 0
                ]
                VC_REQUESTS[token]["staff_panel_msg_id"] = int(getattr(msg, "id", 0) or 0)
                VC_REQUESTS[token]["staff_panel_channel_id"] = int(getattr(getattr(msg, "channel", None), "id", 0) or 0)
            except Exception:
                pass
            return int(getattr(msg, "id", 0) or 0) or None
        except Exception as e:
            print(f"⚠️ Failed to post staff VC panel to {getattr(ch, 'id', None)}: {e}")
            continue

    print("⚠️ VC staff panel: no target channel resolved or all posts failed.")
    return None


def _set_request_status(token: str, status: str, **extra: Any) -> None:
    try:
        req = VC_REQUESTS.get(token) or {}
        req["status"] = str(status).upper().strip()
        for k, v in extra.items():
            req[k] = v
        VC_REQUESTS[token] = req
    except Exception:
        pass


def _preserve_member_ids(
    keep_member: Optional[discord.Member] = None,
    keep_members: Optional[List[discord.Member]] = None,
) -> set[int]:
    ids: set[int] = set()

    if isinstance(keep_member, discord.Member):
        ids.add(int(keep_member.id))

    for member in list(keep_members or []):
        try:
            if isinstance(member, discord.Member):
                ids.add(int(member.id))
        except Exception:
            continue

    return ids


async def _cleanup_vc_permissions(
    guild: discord.Guild,
    keep_member: Optional[discord.Member] = None,
    keep_members: Optional[List[discord.Member]] = None,
) -> None:
    vc = _get_vc_channel(guild)
    if not vc:
        return

    me = guild.me
    if not me:
        return

    ok, why = _can_manage_channel(me, vc)
    if not ok:
        try:
            print(f"⚠️ VC cleanup skipped: {why}")
        except Exception:
            pass
        return

    preserve_ids = _preserve_member_ids(
        keep_member=keep_member,
        keep_members=keep_members,
    )

    for target, _ow in list(vc.overwrites.items()):
        if not isinstance(target, discord.Member):
            continue

        if is_staff(target) or int(target.id) in preserve_ids:
            continue

        try:
            await vc.set_permissions(
                target,
                overwrite=None,
                reason="VC cleanup: remove stale access",
            )
            print(f"✅ Removed VC overwrite for {target}")
        except Exception as e:
            print(f"⚠️ Failed to remove overwrite for {target}: {e}")


async def _vc_revoke_access(
    guild: discord.Guild,
    member: discord.Member,
    token: str,
    reason: str = "revoke",
) -> None:
    vc = _get_vc_channel(guild)
    if not vc:
        return

    me = guild.me
    if not me:
        return

    ok, _ = _can_manage_channel(me, vc)
    if not ok:
        return

    try:
        await vc.set_permissions(
            member,
            overwrite=None,
            reason=f"VC verify revoke ({reason}) token={token}",
        )
        print(f"✅ Revoked VC access for {member} in {vc.name}")
    except Exception as e:
        print(f"⚠️ Failed to revoke VC access: {e}")

    try:
        t = VC_ACCESS_TASKS.get(token)
        if t and not t.done():
            t.cancel()
    except Exception:
        pass

    VC_ACCESS_TASKS.pop(token, None)


async def _vc_grant_access(
    guild: discord.Guild,
    member: discord.Member,
    token: str,
) -> Tuple[bool, str]:
    print(f"🔍 _vc_grant_access: guild={guild.id}, member={member.id}, token={token}")

    vc = _get_vc_channel(guild)
    if not vc:
        msg = "VC verification channel not found or not a voice channel."
        print(f"❌ {msg}")
        return False, msg

    me = guild.me
    if not me:
        msg = "Bot member missing in guild."
        print(f"❌ {msg}")
        return False, msg

    ok, reason = _can_manage_channel(me, vc)
    if not ok:
        full_msg = f"Bot lacks permission to manage {vc.mention}: {reason}"
        print(f"❌ {full_msg}")
        return False, full_msg

    try:
        ow = vc.overwrites_for(member)
        ow.view_channel = True
        ow.connect = True
        ow.speak = True
        await vc.set_permissions(
            member,
            overwrite=ow,
            reason=f"VC verify grant (token={token})",
        )
        print(f"✅ Granted VC access to {member} in {vc.name}")
    except discord.Forbidden as e:
        msg = f"Forbidden while setting VC permissions: {e}"
        print(f"❌ {msg}")
        return False, msg
    except discord.HTTPException as e:
        msg = f"Discord API error while setting permissions: {e}"
        print(f"❌ {msg}")
        return False, msg
    except Exception as e:
        msg = f"Unexpected error: {e}"
        print(f"❌ {msg}")
        return False, msg

    access_minutes = int(globals().get("VC_VERIFY_ACCESS_MINUTES", 30) or 30)

    async def _revoke_later():
        try:
            await asyncio.sleep(max(30, access_minutes * 60))
            await _vc_revoke_access(guild, member, token, reason="auto-expire")
        except asyncio.CancelledError:
            return
        except Exception as e:
            print(f"⚠️ Error in revoke_later: {e}")

    try:
        old = VC_ACCESS_TASKS.get(token)
        if old and not old.done():
            old.cancel()
        t = asyncio.create_task(_revoke_later())
        _track_task(t, label="vc_access_revoke")
        VC_ACCESS_TASKS[token] = t
    except Exception as e:
        print(f"⚠️ Failed to schedule revoke task: {e}")

    return True, "OK"


async def _vc_disable_panels_everywhere(
    guild: discord.Guild,
    token: str,
    status_text: str,
) -> None:
    try:
        req = VC_REQUESTS.get(token) or {}
        msg_refs = req.get("staff_msg_refs") or []
        if not isinstance(msg_refs, list):
            msg_refs = []

        if not msg_refs:
            msg_ids = req.get("staff_msg_ids") or []
            staff_ch = await _get_staff_alert_channel(guild)
            if staff_ch and isinstance(msg_ids, list):
                for mid in list(msg_ids):
                    try:
                        m = await staff_ch.fetch_message(int(mid))  # type: ignore[attr-defined]
                        try:
                            await m.edit(content=(m.content or ""), view=None)
                        except Exception:
                            pass
                    except Exception:
                        continue
                if status_text:
                    try:
                        await staff_ch.send(f"ℹ️ VC request `{token}`: {status_text}")  # type: ignore[misc]
                    except Exception:
                        pass
            return

        notified_channels: set[int] = set()

        for ref in list(msg_refs):
            try:
                channel_id = int(ref.get("channel_id", 0) or 0)
                message_id = int(ref.get("message_id", 0) or 0)
            except Exception:
                continue

            if channel_id <= 0 or message_id <= 0:
                continue

            ch = await _resolve_text_channel(guild, channel_id)
            if not ch or not hasattr(ch, "fetch_message"):
                continue

            try:
                m = await ch.fetch_message(message_id)  # type: ignore[attr-defined]
                try:
                    await m.edit(content=(m.content or ""), view=None)
                except Exception:
                    pass
                notified_channels.add(channel_id)
            except Exception:
                continue

        if status_text:
            for channel_id in notified_channels:
                ch = await _resolve_text_channel(guild, channel_id)
                if not ch:
                    continue
                try:
                    await ch.send(f"ℹ️ VC request `{token}`: {status_text}")  # type: ignore[misc]
                except Exception:
                    continue
    except Exception as e:
        print(f"⚠️ _vc_disable_panels_everywhere failed: {e}")


async def _cleanup_stale_vc_request(
    guild: discord.Guild,
    token: str,
    reason: str,
) -> bool:
    try:
        req = VC_REQUESTS.get(token)
        if not req:
            return False

        ticket_ch_id = req.get("ticket_channel_id")
        if not ticket_ch_id:
            VC_REQUESTS.pop(token, None)
            return True

        ticket_ch = guild.get_channel(ticket_ch_id)
        if ticket_ch is None:
            try:
                ticket_ch = await guild.fetch_channel(int(ticket_ch_id))
            except Exception:
                ticket_ch = None

        if isinstance(ticket_ch, discord.TextChannel):
            return False

        _set_request_status(token, "STALE", stale_reason=reason, cleaned_at=now_utc().isoformat())

        try:
            await _vc_disable_panels_everywhere(
                guild,
                token,
                status_text=f"Request cancelled — ticket channel no longer exists. Reason: {reason}",
            )
        except Exception:
            pass

        VC_REQUESTS.pop(token, None)
        return True
    except Exception as e:
        print(f"⚠️ Error in _cleanup_stale_vc_request: {e}")
        return False


def _find_active_vc_token_for_channel(channel_id: int) -> Optional[str]:
    try:
        cid = int(channel_id)
    except Exception:
        return None

    for tok, req in list((VC_REQUESTS or {}).items()):
        try:
            if int(req.get("ticket_channel_id") or 0) != cid:
                continue

            status = str(req.get("status") or "").upper()
            if status in VC_ACTIVE_STATUSES:
                return str(tok)
        except Exception:
            continue

    return None


async def _resolve_vc_ticket_and_owner(
    guild: discord.Guild,
    token: str,
) -> Tuple[Optional[discord.TextChannel], Optional[discord.Member], Optional[Dict[str, Any]]]:
    tok = str(token or "").strip()
    if not tok:
        return None, None, None

    token_info = sb_get_token_info(tok)
    if not token_info:
        return None, None, None

    ticket_ch = await _resolve_ticket_channel_from_token_info(guild, token_info)
    owner = None

    try:
        rid = int(str(token_info.get("requester_id") or token_info.get("user_id") or "0") or 0)
        if rid:
            owner = guild.get_member(rid) or await guild.fetch_member(rid)
    except Exception:
        owner = None

    if owner is None and isinstance(ticket_ch, discord.TextChannel):
        try:
            owner = await find_ticket_owner_retry(ticket_ch)
        except Exception:
            owner = None

    return ticket_ch if isinstance(ticket_ch, discord.TextChannel) else None, owner, token_info


# ============================================================
# Shared VC request creation
# ============================================================
async def create_vc_request_for_ticket(
    *,
    guild: discord.Guild,
    ticket_channel: discord.TextChannel,
    requester_id: int,
    requested_by_id: int,
    token: str,
    owner_member: Optional[discord.Member] = None,
) -> Dict[str, Any]:
    """
    Single source of truth for creating a VC request from a ticket.

    Returns:
      {
        "ok": bool,
        "token": str,
        "staff_posted": bool,
        "duplicate": bool,
        "message": str,
        "staff_panel_message_id": Optional[int],
      }
    """
    if not isinstance(ticket_channel, discord.TextChannel):
        return {
            "ok": False,
            "token": str(token or ""),
            "staff_posted": False,
            "duplicate": False,
            "message": "Ticket channel is invalid.",
            "staff_panel_message_id": None,
        }

    if not guild:
        return {
            "ok": False,
            "token": str(token or ""),
            "staff_posted": False,
            "duplicate": False,
            "message": "Guild context missing.",
            "staff_panel_message_id": None,
        }

    requester_id = int(requester_id or 0)
    requested_by_id = int(requested_by_id or 0)
    token = str(token or "").strip()

    if requester_id <= 0:
        return {
            "ok": False,
            "token": token,
            "staff_posted": False,
            "duplicate": False,
            "message": "Ticket owner could not be resolved.",
            "staff_panel_message_id": None,
        }

    if not token:
        return {
            "ok": False,
            "token": token,
            "staff_posted": False,
            "duplicate": False,
            "message": "VC token missing.",
            "staff_panel_message_id": None,
        }

    try:
        vc_id = int(globals().get("VC_VERIFY_CHANNEL_ID", 0) or globals().get("VC_VERIFY_VC_ID", 0) or 0)
    except Exception:
        vc_id = 0

    if vc_id <= 0:
        return {
            "ok": False,
            "token": token,
            "staff_posted": False,
            "duplicate": False,
            "message": "VC verification channel is not configured.",
            "staff_panel_message_id": None,
        }

    async with _request_lock(token):
        existing_same_owner = None
        try:
            for existing_token, req in list((VC_REQUESTS or {}).items()):
                if str(existing_token or "").strip() == token:
                    continue
                if not isinstance(req, dict):
                    continue
                if int(req.get("ticket_channel_id") or 0) != int(ticket_channel.id):
                    continue
                req_owner_id = int(req.get("owner_id") or req.get("requester_id") or req.get("requested_by") or 0)
                req_status = str(req.get("status") or "").upper().strip()
                if req_owner_id == requester_id and req_status in VC_ACTIVE_STATUSES:
                    existing_same_owner = str(existing_token)
                    break
        except Exception:
            existing_same_owner = None

        if existing_same_owner:
            return {
                "ok": True,
                "token": existing_same_owner,
                "staff_posted": True,
                "duplicate": True,
                "message": "VC request already queued for this ticket owner.",
                "staff_panel_message_id": None,
            }

        requester_mention = (
            owner_member.mention
            if isinstance(owner_member, discord.Member)
            else f"<@{int(requester_id)}>"
        )

        VC_REQUESTS[token] = {
            "status": "PENDING",
            "requested_at": now_utc().isoformat(),
            "requested_by": int(requested_by_id or requester_id),
            "requester_id": int(requester_id),
            "owner_id": int(requester_id),
            "ticket_channel_id": int(ticket_channel.id),
            "guild_id": int(guild.id),
            "vc_channel_id": int(vc_id),
            "staff_msg_ids": [],
            "staff_msg_refs": [],
        }

        try:
            RUNTIME_STATS["vc_requests"] = int(RUNTIME_STATS.get("vc_requests", 0) or 0) + 1
        except Exception:
            pass

        staff_panel_mid = await _post_staff_vc_request_panel(
            guild=guild,
            token=token,
            requester_id=int(requester_id),
            requester_mention=requester_mention,
            ticket_channel_id=int(ticket_channel.id),
        )

        if staff_panel_mid:
            try:
                VC_REQUESTS[token]["staff_panel_msg_id"] = int(staff_panel_mid)
            except Exception:
                pass
            return {
                "ok": True,
                "token": token,
                "staff_posted": True,
                "duplicate": False,
                "message": "VC request queued and staff panel posted.",
                "staff_panel_message_id": int(staff_panel_mid),
            }

        try:
            RUNTIME_STATS["vc_staff_panel_posts_failed"] = int(
                RUNTIME_STATS.get("vc_staff_panel_posts_failed", 0) or 0
            ) + 1
        except Exception:
            pass

        return {
            "ok": True,
            "token": token,
            "staff_posted": False,
            "duplicate": False,
            "message": "VC request created but staff panel could not be posted.",
            "staff_panel_message_id": None,
        }


async def _vc_lock_channel_for_session(
    guild: discord.Guild,
    owner: Optional[discord.Member],
    staff_member: Optional[discord.Member],
    token: str,
) -> Tuple[bool, str]:
    if not isinstance(owner, discord.Member):
        return False, "Ticket owner could not be resolved."
    if not isinstance(staff_member, discord.Member):
        return False, "Assigned staff member could not be resolved."

    async with _request_lock(token):
        try:
            if _vc_sessions_mod and hasattr(_vc_sessions_mod, "ensure_session"):
                _vc_sessions_mod.ensure_session(
                    token=str(token),
                    guild_id=int(guild.id),
                    ticket_channel_id=int((VC_REQUESTS.get(token) or {}).get("ticket_channel_id") or 0),
                    requester_id=int(owner.id),
                    owner_id=int(owner.id),
                    vc_channel_id=int(globals().get("VC_VERIFY_CHANNEL_ID", 0) or globals().get("VC_VERIFY_VC_ID", 0) or 0),
                    queue_channel_id=int(globals().get("VC_VERIFY_QUEUE_CHANNEL_ID", 0) or 0),
                    access_minutes=int(globals().get("VC_VERIFY_ACCESS_MINUTES", 30) or 30),
                    meta={
                        "assigned_staff_id": int(staff_member.id),
                        "assigned_staff_name": str(staff_member.display_name),
                        "staff_confirmed": True,
                    },
                )

            if _vc_sessions_mod and hasattr(_vc_sessions_mod, "set_staff_accepted"):
                _vc_sessions_mod.set_staff_accepted(
                    token=str(token),
                    staff_id=int(staff_member.id),
                    staff_name=str(staff_member.display_name),
                )
        except Exception:
            pass

        _set_request_status(
            token,
            "STAFF_ACCEPTED",
            accepted_staff_id=int(staff_member.id),
            assigned_staff_id=int(staff_member.id),
            accepted_by=int(staff_member.id),
            vc_channel_id=int(globals().get("VC_VERIFY_CHANNEL_ID", 0) or globals().get("VC_VERIFY_VC_ID", 0) or 0),
            accepted_at=now_utc().isoformat(),
            owner_id=int(owner.id),
            requester_id=int(owner.id),
            guild_id=int(guild.id),
        )

        if _vc_verify_mod and hasattr(_vc_verify_mod, "vc_unlock_session_participants"):
            ok, msg = await _vc_verify_mod.vc_unlock_session_participants(
                guild=guild,
                token=str(token),
                owner=owner,
                staff_member=staff_member,
            )
            if not ok:
                return False, msg
        else:
            ok, msg = await _vc_grant_access(guild, owner, token)
            if not ok:
                return False, msg

            ok2, msg2 = await _vc_grant_access(guild, staff_member, token)
            if not ok2:
                return False, msg2

            try:
                await _cleanup_vc_permissions(
                    guild,
                    keep_members=[owner, staff_member],
                )
            except Exception:
                pass

        vc = None
        try:
            if _vc_verify_mod and hasattr(_vc_verify_mod, "_resolve_vc_channel"):
                vc = await _vc_verify_mod._resolve_vc_channel(guild)
        except Exception:
            vc = None

        if isinstance(vc, (discord.VoiceChannel, discord.StageChannel)):
            try:
                me = guild.me
                can_manage = False
                try:
                    perms_result = _can_manage_channel(me, vc) if me else (False, "")
                    can_manage = bool(perms_result[0])
                except Exception:
                    can_manage = False

                if me and can_manage:
                    preserve_ids = {int(owner.id), int(staff_member.id)}

                    for target, _ow in list(vc.overwrites.items()):
                        if not isinstance(target, discord.Member):
                            continue
                        if target.id in preserve_ids or is_staff(target):
                            continue
                        try:
                            await vc.set_permissions(
                                target,
                                overwrite=None,
                                reason=f"VC session lock cleanup token={token}",
                            )
                        except Exception:
                            pass

                    for m in list(getattr(vc, "members", []) or []):
                        if int(m.id) in preserve_ids:
                            continue
                        try:
                            await m.move_to(
                                None,
                                reason=f"VC session private lock token={token}",
                            )
                        except Exception:
                            pass
            except Exception:
                pass

        return True, "VC locked to ticket owner + assigned staff."


async def _vc_unlock_channel_for_next_session(
    guild: discord.Guild,
    token: str,
) -> None:
    try:
        if _vc_verify_mod and hasattr(_vc_verify_mod, "vc_relock_session"):
            await _vc_verify_mod.vc_relock_session(
                guild=guild,
                token=str(token),
                reason="ready for next person",
            )
    except Exception:
        pass

    try:
        if _vc_sessions_mod and hasattr(_vc_sessions_mod, "clear_unlock"):
            _vc_sessions_mod.clear_unlock(token=str(token), action_name="ready_next")
    except Exception:
        pass

    try:
        req = VC_REQUESTS.get(token) or {}
        req["status"] = "COMPLETED"
        req["completed_at"] = now_utc().isoformat()
        req.pop("accepted_staff_id", None)
        req.pop("assigned_staff_id", None)
        VC_REQUESTS[token] = req
    except Exception:
        pass


# ============================================================
# Slash command handlers
# ============================================================
async def _vc_reissue_command(
    interaction: discord.Interaction,
    token: Optional[str] = None,
    ticket: Optional[discord.TextChannel] = None,
):
    if not _staff_check(interaction):
        return await interaction.response.send_message("❌ Staff only.", ephemeral=True)

    await interaction.response.defer(ephemeral=True)

    guild = interaction.guild
    if not guild:
        return await interaction.followup.send("❌ Invalid context (no guild).", ephemeral=True)

    resolved_token: str = (token or "").strip()
    resolved_ticket_id: int = int(getattr(ticket, "id", 0) or 0)

    if not resolved_token:
        ch = interaction.channel
        if isinstance(ch, discord.TextChannel):
            try:
                me_id = int(getattr(getattr(bot, "user", None), "id", 0) or 0)
                async for msg in ch.history(limit=50):
                    if int(getattr(getattr(msg, "author", None), "id", 0) or 0) != me_id:
                        continue
                    if not msg.embeds:
                        continue

                    e = msg.embeds[0]
                    ft = str(getattr(getattr(e, "footer", None), "text", "") or "")
                    if "VC staff panel" not in ft:
                        continue

                    tok = ""
                    tid = 0
                    try:
                        for f in (e.fields or []):
                            n = (f.name or "").strip().lower()
                            v = (f.value or "").strip()
                            if n == "token":
                                tok = v.split()[0].strip("`").strip()
                            if n == "ticket":
                                mm = re.search(r"\b(\d{15,22})\b", v)
                                if mm:
                                    tid = int(mm.group(1))
                    except Exception:
                        pass

                    if tok:
                        resolved_token = tok
                    if tid:
                        resolved_ticket_id = tid
                    if resolved_token:
                        break
            except Exception:
                pass

    if not resolved_token:
        return await interaction.followup.send(
            "❌ Missing token.\nUse `/vc_reissue token:<token>` or run it inside the VC queue channel so I can read the latest panel.",
            ephemeral=True,
        )

    old_info = sb_get_token_info(resolved_token)
    if not old_info:
        return await interaction.followup.send("❌ Token not found in storage.", ephemeral=True)

    if not resolved_ticket_id:
        try:
            resolved_ticket_id = int(str(old_info.get("channel_id") or "0") or 0)
        except Exception:
            resolved_ticket_id = 0

    ticket_ch: Optional[discord.TextChannel] = None
    if resolved_ticket_id:
        ch2 = guild.get_channel(int(resolved_ticket_id))
        if isinstance(ch2, discord.TextChannel):
            ticket_ch = ch2
        elif resolved_ticket_id:
            try:
                fetched = await guild.fetch_channel(int(resolved_ticket_id))
                if isinstance(fetched, discord.TextChannel):
                    ticket_ch = fetched
            except Exception:
                ticket_ch = None

    if not ticket_ch:
        await _cleanup_stale_vc_request(
            guild,
            resolved_token,
            reason="channel not found during reissue",
        )
        return await interaction.followup.send(
            "❌ I couldn’t resolve the ticket channel for that token.\n"
            "The ticket channel may have been deleted. The request has been cleaned up.",
            ephemeral=True,
        )

    try:
        vc_ttl = int(globals().get("VC_REQUEST_TTL_MINUTES", 0) or 0)
    except Exception:
        vc_ttl = 0
    if vc_ttl <= 0:
        vc_ttl = max(20, int(TOKEN_TTL_MINUTES or 20))

    try:
        rid = int(str(old_info.get("requester_id") or old_info.get("user_id") or "0") or 0)
    except Exception:
        rid = 0

    try:
        from ..verify_ui import post_or_replace_verify_ui  # lazy import to avoid circular imports
        await post_or_replace_verify_ui(
            ticket_ch,
            requester_id=rid or None,
            reason=f"vc_reissue_by_staff:{interaction.user.id}",
            site_url=VERIFY_SITE_URL,
            ttl_minutes=TOKEN_TTL_MINUTES,
            allow_regen=ALLOW_USER_VERIFYLINK,
        )
    except Exception:
        pass

    try:
        from ..verify_ui import _issue_token_url  # lazy import to avoid circular imports
        new_token, _ = await _issue_token_url(
            site_url=VERIFY_SITE_URL,
            guild=guild,
            channel=ticket_ch,
            requester_id=int(rid or interaction.user.id),
            ttl_minutes=vc_ttl,
        )
        print(f"✅ Created new token {new_token} with TTL {vc_ttl}m")
    except Exception as e:
        return await interaction.followup.send(
            f"❌ Failed to create new token: {e}",
            ephemeral=True,
        )

    result = await create_vc_request_for_ticket(
        guild=guild,
        ticket_channel=ticket_ch,
        requester_id=int(rid or 0),
        requested_by_id=int(interaction.user.id),
        token=new_token,
        owner_member=(guild.get_member(int(rid)) if rid else None),
    )

    try:
        await _vc_disable_panels_everywhere(
            guild,
            resolved_token,
            status_text=f"Reissued → `{new_token}` by {interaction.user.mention}",
        )
    except Exception:
        pass

    old_entry = VC_REQUESTS.get(resolved_token)
    if old_entry and old_entry.get("ticket_panel_msg_id"):
        ticket_panel_msg_id = old_entry["ticket_panel_msg_id"]
        if ticket_ch:
            try:
                ticket_msg = await ticket_ch.fetch_message(ticket_panel_msg_id)
                new_view = discord.ui.View(timeout=None)
                new_view.add_item(
                    discord.ui.Button(
                        label="✅ Approve (VC)",
                        style=discord.ButtonStyle.success,
                        custom_id=make_custom_id("vc_approve", new_token),
                    )
                )
                new_view.add_item(
                    discord.ui.Button(
                        label="⛔ Deny & Close (VC)",
                        style=discord.ButtonStyle.danger,
                        custom_id=make_custom_id("vc_denyclose", new_token),
                    )
                )
                new_view.add_item(
                    discord.ui.Button(
                        label="🧹 End VC Session",
                        style=discord.ButtonStyle.secondary,
                        custom_id=make_custom_id("vc_end", new_token),
                    )
                )
                await ticket_msg.edit(view=new_view)

                VC_REQUESTS.setdefault(new_token, {})
                VC_REQUESTS[new_token]["ticket_panel_msg_id"] = ticket_panel_msg_id
            except Exception as e:
                print(f"⚠️ Failed to update ticket panel in /vc_reissue: {e}")

    status_note = "staff panel posted" if result.get("staff_posted") else "staff panel routing failed"
    return await interaction.followup.send(
        f"✅ Reissued VC token.\nOld: `{resolved_token}`\nNew: `{new_token}`\nTicket: {ticket_ch.mention}\nStatus: {status_note}",
        ephemeral=True,
    )


async def _vc_status_command(
    interaction: discord.Interaction,
    token: Optional[str] = None,
):
    if not _staff_check(interaction):
        return await interaction.response.send_message("❌ Staff only.", ephemeral=True)

    guild = interaction.guild
    if guild is None:
        return await interaction.response.send_message("❌ Invalid context.", ephemeral=True)

    tok = (token or "").strip()
    if not tok and isinstance(interaction.channel, discord.TextChannel):
        tok = _find_active_vc_token_for_channel(interaction.channel.id) or ""

    if not tok:
        return await interaction.response.send_message(
            "❌ No active VC token found for this ticket.",
            ephemeral=True,
        )

    ticket_ch, owner, token_info = await _resolve_vc_ticket_and_owner(guild, tok)
    if token_info is None:
        return await interaction.response.send_message("❌ Token not found.", ephemeral=True)

    req = VC_REQUESTS.get(tok) or {}
    accepted_by = int(req.get("accepted_staff_id") or req.get("accepted_by") or 0)

    msg = [f"🎙️ VC token: `{tok}`", f"Status: `{req.get('status') or 'UNKNOWN'}`"]
    if ticket_ch:
        msg.append(f"Ticket: {ticket_ch.mention}")
    if owner:
        msg.append(f"Owner: {owner.mention} (`{owner.id}`)")
    if accepted_by:
        msg.append(f"Accepted by: <@{accepted_by}> (`{accepted_by}`)")

    await interaction.response.send_message("\n".join(msg), ephemeral=True)


async def _vc_takeover_command(
    interaction: discord.Interaction,
    token: Optional[str] = None,
    ticket: Optional[discord.TextChannel] = None,
):
    if not _staff_check(interaction):
        return await interaction.response.send_message("❌ Staff only.", ephemeral=True)

    guild = interaction.guild
    if guild is None:
        return await interaction.response.send_message("❌ Invalid context.", ephemeral=True)

    await interaction.response.defer(ephemeral=True)

    tok = (token or "").strip()
    ticket_ch = ticket if isinstance(ticket, discord.TextChannel) else (
        interaction.channel if isinstance(interaction.channel, discord.TextChannel) else None
    )

    if not tok and ticket_ch is not None:
        tok = _find_active_vc_token_for_channel(ticket_ch.id) or ""

    if not tok:
        return await interaction.followup.send(
            "❌ No active VC token found. Pass a token or run this in the ticket channel.",
            ephemeral=True,
        )

    ticket_ch2, owner, token_info = await _resolve_vc_ticket_and_owner(guild, tok)
    if token_info is None:
        return await interaction.followup.send("❌ Token not found.", ephemeral=True)

    ticket_ch = ticket_ch2 or ticket_ch
    if not isinstance(ticket_ch, discord.TextChannel):
        return await interaction.followup.send(
            "❌ Could not resolve the ticket channel.",
            ephemeral=True,
        )

    req = VC_REQUESTS.setdefault(tok, {})
    prev_staff = int(req.get("accepted_staff_id") or req.get("accepted_by") or 0)

    _set_request_status(
        tok,
        "TAKEN_OVER",
        accepted_staff_id=int(interaction.user.id),
        accepted_by=int(interaction.user.id),
        assigned_staff_id=int(interaction.user.id),
        takeover_at=now_utc().isoformat(),
        takeover_by=int(interaction.user.id),
    )

    try:
        if _vc_sessions_mod and hasattr(_vc_sessions_mod, "takeover_session"):
            _vc_sessions_mod.takeover_session(
                token=str(tok),
                new_staff_id=int(interaction.user.id),
                new_staff_name=str(getattr(interaction.user, "display_name", interaction.user)),
                reason="manual takeover by staff",
            )
    except Exception:
        pass

    ok, msg = await _vc_lock_channel_for_session(
        guild,
        owner,
        interaction.user if isinstance(interaction.user, discord.Member) else None,
        tok,
    )
    if not ok:
        return await interaction.followup.send(
            f"❌ Failed to lock VC channel: {msg}",
            ephemeral=True,
        )

    try:
        await ticket_ch.send(
            f"🔁 **VC verify takeover:** {interaction.user.mention} has taken over this VC session"
            + (
                f" from <@{prev_staff}>."
                if prev_staff and prev_staff != int(interaction.user.id)
                else "."
            )
        )
    except Exception:
        pass

    await interaction.followup.send(
        f"✅ VC session now belongs to you. Only you and the ticket owner should have VC access for `{tok}`.",
        ephemeral=True,
    )


async def _vc_unlock_command(
    interaction: discord.Interaction,
    token: Optional[str] = None,
):
    if not _staff_check(interaction):
        return await interaction.response.send_message("❌ Staff only.", ephemeral=True)

    guild = interaction.guild
    if guild is None:
        return await interaction.response.send_message("❌ Invalid context.", ephemeral=True)

    await interaction.response.defer(ephemeral=True)

    tok = (token or "").strip()
    if not tok and isinstance(interaction.channel, discord.TextChannel):
        tok = _find_active_vc_token_for_channel(interaction.channel.id) or ""

    if not tok:
        return await interaction.followup.send("❌ No active VC token found.", ephemeral=True)

    try:
        ticket_ch, owner, _ = await _resolve_vc_ticket_and_owner(guild, tok)
    except Exception:
        ticket_ch, owner = None, None

    try:
        if isinstance(owner, discord.Member):
            await _vc_revoke_access(guild, owner, tok, reason="manual-unlock")
    except Exception:
        pass

    await _vc_unlock_channel_for_next_session(guild, tok)

    try:
        if isinstance(ticket_ch, discord.TextChannel):
            await ticket_ch.send(
                f"🧹 VC verify channel reset by {interaction.user.mention}. Ready for the next person."
            )
    except Exception:
        pass

    await interaction.followup.send(
        f"✅ VC channel reset for token `{tok}`.",
        ephemeral=True,
    )


async def _vc_cleanup_command(interaction: discord.Interaction):
    if not _staff_check(interaction):
        return await interaction.response.send_message("❌ Staff only.", ephemeral=True)

    guild = interaction.guild
    if guild is None:
        return await interaction.response.send_message("❌ Invalid context.", ephemeral=True)

    await interaction.response.defer(ephemeral=True)

    try:
        await _cleanup_vc_permissions(guild, keep_members=None)
        await interaction.followup.send("✅ VC verify channel cleanup complete.", ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"❌ VC cleanup failed: {e}", ephemeral=True)


# ============================================================
# Explicit registration
# ============================================================
_REGISTERED = False


def register_vc_flow_commands(_bot: Any = None, tree: Any = None) -> None:
    global _REGISTERED

    if _REGISTERED:
        return

    command_tree = tree or getattr(_bot, "tree", None) or bot.tree

    @command_tree.command(
        name="vc_reissue",
        description="(Staff) Reissue a VC request token (use in the VC queue channel or ticket).",
    )
    @app_commands.describe(
        token="(Optional) The token shown in the VC queue panel. If omitted, I will try to read it from the latest VC panel in this channel.",
        ticket="(Optional) The ticket channel. If omitted, I will resolve it from the token/panel.",
    )
    async def vc_reissue_slash(
        interaction: discord.Interaction,
        token: Optional[str] = None,
        ticket: Optional[discord.TextChannel] = None,
    ):
        return await _vc_reissue_command(interaction, token=token, ticket=ticket)

    @command_tree.command(
        name="vc_status",
        description="(Staff) Show active VC verify session details for this ticket or token.",
    )
    @app_commands.describe(token="Optional VC token (leave empty to resolve from the current ticket)")
    async def vc_status_slash(
        interaction: discord.Interaction,
        token: Optional[str] = None,
    ):
        return await _vc_status_command(interaction, token=token)

    @command_tree.command(
        name="vc_takeover",
        description="(Staff) Take over a stuck VC verify ticket and lock the VC to you + the ticket owner.",
    )
    @app_commands.describe(token="Optional VC token", ticket="Optional ticket channel")
    async def vc_takeover_slash(
        interaction: discord.Interaction,
        token: Optional[str] = None,
        ticket: Optional[discord.TextChannel] = None,
    ):
        return await _vc_takeover_command(interaction, token=token, ticket=ticket)

    @command_tree.command(
        name="vc_unlock",
        description="(Staff) Force-unlock/reset the VC verify channel so it is ready for the next person.",
    )
    @app_commands.describe(token="Optional VC token")
    async def vc_unlock_slash(
        interaction: discord.Interaction,
        token: Optional[str] = None,
    ):
        return await _vc_unlock_command(interaction, token=token)

    @command_tree.command(
        name="vc_cleanup",
        description="(Staff) Remove stale non-staff permission overwrites from the VC verify channel.",
    )
    async def vc_cleanup_slash(interaction: discord.Interaction):
        return await _vc_cleanup_command(interaction)

    _REGISTERED = True
    print("✅ commands_ext.vc_flow: registered VC flow commands")


def register_extra_commands(tree) -> None:
    try:
        register_vc_flow_commands(bot, tree)
    except Exception:
        pass


__all__ = [
    "VC_STAFF_ACTIONS",
    "VC_ACTIVE_STATUSES",
    "VC_TERMINAL_STATUSES",
    "DEFAULT_VC_VERIFY_REQUESTS_CHANNEL_ID",
    "_vc_requests_channel_id",
    "_get_vc_channel",
    "_resolve_text_channel",
    "_get_vc_queue_channel",
    "_get_staff_alert_channel",
    "_can_manage_channel",
    "_resolve_ticket_channel_from_token_info",
    "_build_vc_staff_embed",
    "_post_staff_vc_request_panel",
    "create_vc_request_for_ticket",
    "_cleanup_vc_permissions",
    "_vc_grant_access",
    "_vc_revoke_access",
    "_vc_disable_panels_everywhere",
    "_cleanup_stale_vc_request",
    "_find_active_vc_token_for_channel",
    "_resolve_vc_ticket_and_owner",
    "_vc_lock_channel_for_session",
    "_vc_unlock_channel_for_next_session",
    "register_vc_flow_commands",
    "register_extra_commands",
]
