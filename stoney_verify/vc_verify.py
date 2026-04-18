from __future__ import annotations

import asyncio
from datetime import datetime, timezone, timedelta
from typing import Optional, Tuple, Dict, Any, List

import discord

from .globals import *

try:
    from . import vc_sessions
except Exception:
    vc_sessions = None  # type: ignore


def _safe_track_task(task: "asyncio.Task", *, label: str = "") -> None:
    try:
        fn = globals().get("_track_task")
        if callable(fn):
            fn(task, label=label)  # type: ignore[misc]
    except Exception:
        pass


def mark_ticket_activity(channel_id: int) -> None:
    try:
        TICKET_LAST_ACTIVITY[int(channel_id)] = now_utc()
    except Exception:
        pass


def _staff_ping_text() -> str:
    try:
        vc_rid = int(globals().get("VC_STAFF_ROLE_ID") or 0)
        if vc_rid:
            return f"<@&{vc_rid}>"
    except Exception:
        pass
    try:
        rid = int(STAFF_ROLE_ID or 0)
        if rid:
            return f"<@&{rid}>"
    except Exception:
        pass
    return ""


def _as_int(v: object, default: int = 0) -> int:
    try:
        if v is None:
            return default
        if isinstance(v, bool):
            return default
        return int(str(v).strip())
    except Exception:
        return default


def _configured_vc_channel_id() -> int:
    return _as_int(globals().get("VC_VERIFY_CHANNEL_ID", 0) or globals().get("VC_VERIFY_VC_ID", 0), 0)


def _get_vc_channel(guild: discord.Guild) -> Optional[discord.abc.GuildChannel]:
    vc_id = _configured_vc_channel_id()
    if not vc_id:
        return None

    ch = guild.get_channel(vc_id)
    if isinstance(ch, (discord.VoiceChannel, discord.StageChannel)):
        return ch

    try:
        asyncio.get_event_loop().create_task(guild.fetch_channel(vc_id))
    except Exception:
        pass
    return None


async def _resolve_vc_channel(guild: discord.Guild) -> Optional[discord.abc.GuildChannel]:
    vc_id = _configured_vc_channel_id()
    if not vc_id:
        return None

    ch = guild.get_channel(vc_id)
    if isinstance(ch, (discord.VoiceChannel, discord.StageChannel)):
        return ch
    try:
        ch2 = await guild.fetch_channel(vc_id)
        if isinstance(ch2, (discord.VoiceChannel, discord.StageChannel)):
            return ch2
    except Exception:
        return None
    return None


def _get_vc_queue_channel(guild: discord.Guild) -> Optional[discord.TextChannel]:
    qid = _as_int(globals().get("VC_VERIFY_QUEUE_CHANNEL_ID", 0), 0)
    if not qid:
        return None
    ch = guild.get_channel(qid)
    return ch if isinstance(ch, discord.TextChannel) else None


async def _resolve_vc_queue_channel(guild: discord.Guild) -> Optional[discord.TextChannel]:
    qid = _as_int(globals().get("VC_VERIFY_QUEUE_CHANNEL_ID", 0), 0)
    if not qid:
        return None
    ch = guild.get_channel(qid)
    if isinstance(ch, discord.TextChannel):
        return ch
    try:
        ch2 = await guild.fetch_channel(qid)
        if isinstance(ch2, discord.TextChannel):
            return ch2
    except Exception:
        return None
    return None


def _can_manage_channel(me: Optional[discord.Member], ch: discord.abc.GuildChannel) -> bool:
    try:
        if not me:
            return False
        perms = ch.permissions_for(me)
        return bool(perms.view_channel and (perms.manage_channels or perms.administrator))
    except Exception:
        return False


def _member_in_target_vc(member: Optional[discord.Member], vc_channel_id: int) -> bool:
    try:
        if not member:
            return False
        state = getattr(member, "voice", None)
        ch = getattr(state, "channel", None)
        return bool(ch and int(getattr(ch, "id", 0) or 0) == int(vc_channel_id))
    except Exception:
        return False


def _get_session_row(token: str) -> Optional[Dict[str, Any]]:
    try:
        if vc_sessions and hasattr(vc_sessions, "get_session"):
            return vc_sessions.get_session(str(token))
    except Exception:
        pass
    return None


def _get_assigned_staff_id(token: str) -> int:
    try:
        row = _get_session_row(token) or {}
        meta = row.get("meta") or {}
        sid = int(meta.get("assigned_staff_id") or 0)
        if sid:
            return sid
    except Exception:
        pass

    try:
        req = VC_REQUESTS.get(token) or {}
        return int(req.get("assigned_staff_id") or 0)
    except Exception:
        return 0


def _get_owner_id(token: str) -> int:
    try:
        row = _get_session_row(token) or {}
        return int(row.get("owner_id") or row.get("requester_id") or 0)
    except Exception:
        return 0


def _get_vc_channel_id_from_session(token: str) -> int:
    try:
        row = _get_session_row(token) or {}
        return int(row.get("vc_channel_id") or 0)
    except Exception:
        pass
    try:
        req = VC_REQUESTS.get(token) or {}
        return int(req.get("vc_channel_id") or 0)
    except Exception:
        return 0


def _get_ticket_channel_id_from_session(token: str) -> int:
    try:
        row = _get_session_row(token) or {}
        return int(row.get("ticket_channel_id") or 0)
    except Exception:
        return 0


def _get_session_status(token: str) -> str:
    try:
        row = _get_session_row(token) or {}
        return str(row.get("status") or "").upper().strip()
    except Exception:
        return ""


async def _resolve_session_vc_channel(
    guild: discord.Guild,
    *,
    token: str,
    session_row: Optional[Dict[str, Any]] = None,
) -> Optional[discord.abc.GuildChannel]:
    vc_channel_id = _as_int(
        (session_row or {}).get("vc_channel_id") or _get_vc_channel_id_from_session(token),
        0,
    )
    if vc_channel_id <= 0:
        return await _resolve_vc_channel(guild)

    ch = guild.get_channel(vc_channel_id)
    if isinstance(ch, (discord.VoiceChannel, discord.StageChannel)):
        return ch

    try:
        fetched = await guild.fetch_channel(vc_channel_id)
        if isinstance(fetched, (discord.VoiceChannel, discord.StageChannel)):
            return fetched
    except Exception:
        return None

    return None


async def _vc_channel_has_active_users(
    guild: discord.Guild,
    *,
    token: str,
    session_row: Optional[Dict[str, Any]] = None,
) -> bool:
    ch = await _resolve_session_vc_channel(guild, token=token, session_row=session_row)
    if not isinstance(ch, (discord.VoiceChannel, discord.StageChannel)):
        return False

    try:
        return any(not getattr(member, "bot", False) for member in (ch.members or []))
    except Exception:
        return False


async def _extend_live_session_expiry(
    *,
    token: str,
    session_row: Optional[Dict[str, Any]] = None,
    reason: str,
) -> None:
    try:
        if vc_sessions and hasattr(vc_sessions, "extend_expiry"):
            vc_sessions.extend_expiry(
                token=str(token),
                minutes=_as_int((session_row or {}).get("access_minutes"), 0),
                reason=reason,
                by_staff_id=_get_assigned_staff_id(token),
            )
    except Exception:
        pass

    try:
        if vc_sessions and hasattr(vc_sessions, "touch_watchdog"):
            vc_sessions.touch_watchdog(str(token))
    except Exception:
        pass


async def _session_notify_ticket_channel(
    guild: discord.Guild,
    *,
    token: str,
    text: str,
) -> None:
    ticket_channel_id = _get_ticket_channel_id_from_session(token)
    if ticket_channel_id <= 0:
        return

    try:
        ticket_ch = guild.get_channel(ticket_channel_id)
        if ticket_ch is None:
            ticket_ch = await guild.fetch_channel(ticket_channel_id)
        if isinstance(ticket_ch, discord.TextChannel):
            await ticket_ch.send(text)
    except Exception:
        pass


def _session_unlock_guard(
    *,
    guild: discord.Guild,
    token: str,
    owner: discord.Member,
    staff_member: discord.Member,
) -> Tuple[bool, str]:
    if not token:
        return False, "Missing VC session token."

    if vc_sessions is None or not hasattr(vc_sessions, "session_is_unlockable"):
        return False, "VC session guard is unavailable."

    try:
        ok, reason = vc_sessions.session_is_unlockable(
            token=str(token),
            expected_guild_id=int(guild.id),
            expected_staff_id=int(staff_member.id),
        )
    except Exception as e:
        return False, f"Failed session guard lookup: {e}"

    if not ok:
        return False, reason

    owner_id = _get_owner_id(token)
    if int(owner.id) != int(owner_id):
        return False, "Owner does not match this VC session."

    ticket_channel_id = _get_ticket_channel_id_from_session(token)
    if ticket_channel_id <= 0:
        return False, "VC session is not ticket-backed."

    vc_channel_id = _get_vc_channel_id_from_session(token)
    configured_vc_id = _configured_vc_channel_id()
    if configured_vc_id <= 0:
        return False, "Configured ID Verify VC is missing."
    if vc_channel_id != configured_vc_id:
        return False, "Session VC does not match configured ID Verify VC."

    status = _get_session_status(token)
    if status not in {"STAFF_ACCEPTED", "OWNER_CONFIRMED", "READY", "TAKEN_OVER", "RESTARTED"}:
        return False, f"Session status `{status or 'UNKNOWN'}` is not allowed to unlock VC."

    return True, "Guard passed."


async def _vc_revoke_access(
    guild: discord.Guild,
    member: discord.Member,
    token: str,
    reason: str = "manual",
) -> None:
    vc = await _resolve_vc_channel(guild)
    if not vc:
        return

    me = guild.me
    if not _can_manage_channel(me, vc):
        return

    prev_vals: Dict[str, Any] = {}
    try:
        prev_vals = (VC_REQUESTS.get(token) or {}).get(f"prev_overwrite_values:{member.id}") or {}
        if not isinstance(prev_vals, dict):
            prev_vals = {}
    except Exception:
        prev_vals = {}

    try:
        if prev_vals:
            ow = discord.PermissionOverwrite(**prev_vals)
            await vc.set_permissions(
                member,
                overwrite=ow,
                reason=f"Restore VC overwrite ({reason}, token={token})",
            )
        else:
            await vc.set_permissions(
                member,
                overwrite=None,
                reason=f"Remove VC overwrite ({reason}, token={token})",
            )
    except Exception:
        pass

    try:
        if token in VC_REQUESTS:
            VC_REQUESTS[token].pop(f"prev_overwrite_values:{member.id}", None)
    except Exception:
        pass


async def _vc_grant_access(
    guild: discord.Guild,
    member: discord.Member,
    token: str,
) -> Tuple[bool, str]:
    vc = await _resolve_vc_channel(guild)
    if not vc:
        return False, "VC verify channel not found (check VC_VERIFY_CHANNEL_ID)."

    me = guild.me
    if not _can_manage_channel(me, vc):
        return False, "I need **Manage Channels** and **View Channel** on the VC verify channel."

    try:
        prev = vc.overwrites_for(member)
        prev_vals = dict(getattr(prev, "_values", {}) or {})
    except Exception:
        prev_vals = {}

    try:
        VC_REQUESTS.setdefault(token, {})
        VC_REQUESTS[token][f"prev_overwrite_values:{member.id}"] = prev_vals
        VC_REQUESTS[token]["vc_channel_id"] = int(vc.id)
    except Exception:
        pass

    try:
        ow = vc.overwrites_for(member)
        ow.view_channel = True
        ow.connect = True
        ow.speak = True
        ow.use_voice_activation = True
        await vc.set_permissions(member, overwrite=ow, reason=f"VC verify access (token={token})")
    except discord.Forbidden:
        return False, "Forbidden while setting VC permissions."
    except Exception as e:
        return False, f"Failed to set VC permissions: {e}"

    return True, "Temporary access granted."


async def vc_unlock_session_participants(
    *,
    guild: discord.Guild,
    token: str,
    owner: discord.Member,
    staff_member: discord.Member,
) -> Tuple[bool, str]:
    ok_guard, guard_msg = _session_unlock_guard(
        guild=guild,
        token=token,
        owner=owner,
        staff_member=staff_member,
    )
    if not ok_guard:
        return False, guard_msg

    ok1, msg1 = await _vc_grant_access(guild, owner, token)
    if not ok1:
        return False, msg1

    ok2, msg2 = await _vc_grant_access(guild, staff_member, token)
    if not ok2:
        try:
            await _vc_revoke_access(guild, owner, token, reason="staff grant failed rollback")
        except Exception:
            pass
        return False, msg2

    try:
        if vc_sessions and hasattr(vc_sessions, "mark_unlocked"):
            vc_sessions.mark_unlocked(
                token=str(token),
                by_staff_id=int(staff_member.id),
                guard_reason=guard_msg,
            )
    except Exception:
        pass

    return True, "Owner and assigned staff now have private VC access."


async def vc_relock_session(
    *,
    guild: discord.Guild,
    token: str,
    reason: str = "session ended",
) -> None:
    owner_id = _get_owner_id(token)
    staff_id = _get_assigned_staff_id(token)

    owner = guild.get_member(owner_id) if owner_id else None
    staff = guild.get_member(staff_id) if staff_id else None

    if owner:
        try:
            await _vc_revoke_access(guild, owner, token, reason=reason)
        except Exception:
            pass
    if staff:
        try:
            await _vc_revoke_access(guild, staff, token, reason=reason)
        except Exception:
            pass

    try:
        if vc_sessions and hasattr(vc_sessions, "clear_unlock"):
            vc_sessions.clear_unlock(token=str(token), action_name="relock")
    except Exception:
        pass


async def vc_session_everyone_left(
    *,
    guild: discord.Guild,
    token: str,
) -> bool:
    if await _vc_channel_has_active_users(guild, token=token):
        return False

    vc_id = _get_vc_channel_id_from_session(token)
    if not vc_id:
        return False

    owner = guild.get_member(_get_owner_id(token))
    staff = guild.get_member(_get_assigned_staff_id(token))

    owner_in = _member_in_target_vc(owner, vc_id)
    staff_in = _member_in_target_vc(staff, vc_id)

    return not owner_in and not staff_in


def _has_perm(member: Optional[discord.Member], *, perm: str) -> bool:
    try:
        if not member:
            return False
        p = member.guild_permissions
        return bool(getattr(p, perm, False))
    except Exception:
        return False


async def vc_move_member_into_verify_vc(
    *,
    guild: discord.Guild,
    member: discord.Member,
) -> Tuple[bool, str]:
    vc = await _resolve_vc_channel(guild)
    if not vc or not isinstance(vc, (discord.VoiceChannel, discord.StageChannel)):
        return False, "VC verify channel not found."

    try:
        src_state = member.voice
        if not src_state or not src_state.channel:
            return False, "User is not currently in a voice channel."
    except Exception:
        return False, "Couldn't read user's voice state."

    me = guild.me
    if not _has_perm(me, perm="move_members"):
        return False, "I need **Move Members** permission."

    try:
        perms = vc.permissions_for(me)  # type: ignore[arg-type]
        if not perms.view_channel or not perms.connect:
            return False, "I need **View Channel** + **Connect** on the verify VC."
    except Exception:
        pass

    try:
        await member.move_to(vc, reason="Start VC verify session")
        return True, f"Moved user into <#{int(vc.id)}>"
    except discord.Forbidden:
        return False, "Forbidden while trying to move user."
    except Exception as e:
        return False, f"Failed to move user: {e}"


try:
    from .vc_sessions import transition as _vc_session_transition  # type: ignore
    from .vc_sessions import sb_enabled as _vc_sb_enabled  # type: ignore
except Exception:
    _vc_session_transition = None  # type: ignore
    _vc_sb_enabled = lambda: False  # type: ignore


def _sb_client():
    for k in ("sb", "supabase", "SUPABASE"):
        try:
            v = globals().get(k)
            if v:
                return v
        except Exception:
            pass
    return None


async def vc_sweeper_loop(bot_client: discord.Client, *, interval_seconds: int = 120) -> None:
    if not callable(_vc_sb_enabled) or not _vc_sb_enabled():
        return
    sb = _sb_client()
    if not sb:
        return

    try:
        if getattr(bot_client, "_vc_sweeper_started", False):
            return
        setattr(bot_client, "_vc_sweeper_started", True)
    except Exception:
        pass

    while True:
        try:
            await asyncio.sleep(max(30, int(interval_seconds)))
        except asyncio.CancelledError:
            return
        except Exception:
            continue

        try:
            now_iso = now_utc().isoformat()  # type: ignore[name-defined]
        except Exception:
            now_iso = datetime.now(timezone.utc).isoformat()

        try:
            res = (
                sb.table("vc_verify_sessions")
                .select("token,guild_id,owner_id,status,vc_channel_id,revoke_at,access_minutes,meta")
                .in_("status", ["STAFF_ACCEPTED", "OWNER_CONFIRMED", "READY", "IN_VC", "STARTED", "TAKEN_OVER", "RESTARTED"])
                .lte("revoke_at", now_iso)
                .limit(100)
                .execute()
            )
            rows = getattr(res, "data", None) or []
        except Exception:
            rows = []

        for row in rows:
            try:
                token = str(row.get("token") or "")
                gid = int(row.get("guild_id") or 0)
                if not token or not gid:
                    continue

                guild = bot_client.get_guild(gid)
                if not guild:
                    if callable(_vc_session_transition):
                        _vc_session_transition(token=token, new_status="EXPIRED", staff_id=0)
                    continue

                live_users_present = await _vc_channel_has_active_users(
                    guild,
                    token=token,
                    session_row=row if isinstance(row, dict) else None,
                )

                if live_users_present:
                    await _extend_live_session_expiry(
                        token=token,
                        session_row=row if isinstance(row, dict) else None,
                        reason="verify vc still has active users",
                    )
                    await _session_notify_ticket_channel(
                        guild,
                        token=token,
                        text="♻️ VC verify session timer was automatically extended because the verify VC still has active users.",
                    )
                    continue

                await vc_relock_session(guild=guild, token=token, reason="sweeper-expire")

                if callable(_vc_session_transition):
                    _vc_session_transition(token=token, new_status="EXPIRED", staff_id=0)

                await _session_notify_ticket_channel(
                    guild,
                    token=token,
                    text="⌛ VC verify session expired after the verify VC became empty.",
                )
            except Exception:
                continue