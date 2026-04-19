# stoney_verify/vc_verify.py
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Optional, Tuple, Dict, Any, List

import discord

from .globals import *  # noqa

try:
    from . import vc_sessions
except Exception:
    vc_sessions = None  # type: ignore


# ============================================================
# Fallback globals / safety
# ============================================================

try:
    VC_REQUESTS  # type: ignore[name-defined]
except Exception:
    VC_REQUESTS: Dict[str, Dict[str, Any]] = {}

try:
    TICKET_LAST_ACTIVITY  # type: ignore[name-defined]
except Exception:
    TICKET_LAST_ACTIVITY: Dict[int, datetime] = {}

try:
    VC_VERIFY_ACCESS_MINUTES  # type: ignore[name-defined]
except Exception:
    VC_VERIFY_ACCESS_MINUTES = 30

try:
    STAFF_ROLE_ID  # type: ignore[name-defined]
except Exception:
    STAFF_ROLE_ID = 0


# ============================================================
# Small helpers
# ============================================================

def _utcnow() -> datetime:
    try:
        return now_utc()  # type: ignore[name-defined]
    except Exception:
        return datetime.now(timezone.utc)


def _safe_track_task(task: "asyncio.Task", *, label: str = "") -> None:
    try:
        fn = globals().get("_track_task")
        if callable(fn):
            fn(task, label=label)  # type: ignore[misc]
    except Exception:
        pass


def mark_ticket_activity(channel_id: int) -> None:
    try:
        TICKET_LAST_ACTIVITY[int(channel_id)] = _utcnow()
    except Exception:
        pass


def _safe_str(value: Any) -> str:
    try:
        return str(value or "").strip()
    except Exception:
        return ""


def _as_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or isinstance(value, bool):
            return default
        return int(str(value).strip())
    except Exception:
        return default


def _configured_vc_channel_id() -> int:
    return _as_int(
        globals().get("VC_VERIFY_CHANNEL_ID", 0)
        or globals().get("VC_VERIFY_VC_ID", 0),
        0,
    )


def _configured_vc_queue_channel_id() -> int:
    return _as_int(globals().get("VC_VERIFY_QUEUE_CHANNEL_ID", 0), 0)


def _staff_ping_text() -> str:
    try:
        vc_rid = int(globals().get("VC_STAFF_ROLE_ID") or 0)
        if vc_rid > 0:
            return f"<@&{vc_rid}>"
    except Exception:
        pass

    try:
        rid = int(STAFF_ROLE_ID or 0)
        if rid > 0:
            return f"<@&{rid}>"
    except Exception:
        pass

    return ""


def _is_staff_member(member: Optional[discord.Member]) -> bool:
    try:
        if not isinstance(member, discord.Member):
            return False
        checker = globals().get("is_staff")
        if callable(checker):
            try:
                return bool(checker(member))
            except Exception:
                pass
        return bool(
            member.guild_permissions.administrator
            or member.guild_permissions.manage_guild
            or member.guild_permissions.manage_channels
            or member.guild_permissions.manage_messages
        )
    except Exception:
        return False


def _has_perm(member: Optional[discord.Member], *, perm: str) -> bool:
    try:
        if not member:
            return False
        p = member.guild_permissions
        return bool(getattr(p, perm, False))
    except Exception:
        return False


def _get_vc_channel(guild: discord.Guild) -> Optional[discord.abc.GuildChannel]:
    vc_id = _configured_vc_channel_id()
    if vc_id <= 0:
        return None

    try:
        ch = guild.get_channel(vc_id)
        if isinstance(ch, (discord.VoiceChannel, discord.StageChannel)):
            return ch
    except Exception:
        pass

    return None


async def _resolve_vc_channel(guild: discord.Guild) -> Optional[discord.abc.GuildChannel]:
    vc_id = _configured_vc_channel_id()
    if vc_id <= 0:
        return None

    try:
        ch = guild.get_channel(vc_id)
        if isinstance(ch, (discord.VoiceChannel, discord.StageChannel)):
            return ch
    except Exception:
        pass

    try:
        fetched = await guild.fetch_channel(vc_id)
        if isinstance(fetched, (discord.VoiceChannel, discord.StageChannel)):
            return fetched
    except Exception:
        pass

    return None


def _get_vc_queue_channel(guild: discord.Guild) -> Optional[discord.TextChannel]:
    qid = _configured_vc_queue_channel_id()
    if qid <= 0:
        return None

    try:
        ch = guild.get_channel(qid)
        if isinstance(ch, discord.TextChannel):
            return ch
    except Exception:
        pass

    return None


async def _resolve_vc_queue_channel(guild: discord.Guild) -> Optional[discord.TextChannel]:
    qid = _configured_vc_queue_channel_id()
    if qid <= 0:
        return None

    try:
        ch = guild.get_channel(qid)
        if isinstance(ch, discord.TextChannel):
            return ch
    except Exception:
        pass

    try:
        fetched = await guild.fetch_channel(qid)
        if isinstance(fetched, discord.TextChannel):
            return fetched
    except Exception:
        pass

    return None


def _can_manage_channel(me: Optional[discord.Member], ch: discord.abc.GuildChannel) -> bool:
    try:
        if not me:
            return False
        perms = ch.permissions_for(me)
        return bool(perms.view_channel and (perms.manage_channels or perms.administrator))
    except Exception:
        return False


async def _resolve_text_channel_by_id(
    guild: discord.Guild,
    channel_id: int,
) -> Optional[discord.TextChannel]:
    if channel_id <= 0:
        return None

    try:
        ch = guild.get_channel(channel_id)
        if isinstance(ch, discord.TextChannel):
            return ch
    except Exception:
        pass

    try:
        fetched = await guild.fetch_channel(channel_id)
        if isinstance(fetched, discord.TextChannel):
            return fetched
    except Exception:
        pass

    return None


# ============================================================
# Session helpers
# ============================================================

def _get_session_row(token: str) -> Optional[Dict[str, Any]]:
    try:
        if vc_sessions and hasattr(vc_sessions, "get_session"):
            return vc_sessions.get_session(str(token))
    except Exception:
        pass
    return None


def _session_meta(token: str) -> Dict[str, Any]:
    try:
        row = _get_session_row(token) or {}
        meta = row.get("meta") or {}
        if isinstance(meta, dict):
            return dict(meta)
    except Exception:
        pass
    return {}


def _get_assigned_staff_id(token: str) -> int:
    try:
        meta = _session_meta(token)
        sid = int(meta.get("assigned_staff_id") or 0)
        if sid > 0:
            return sid
    except Exception:
        pass

    try:
        req = VC_REQUESTS.get(token) or {}
        return int(
            req.get("assigned_staff_id")
            or req.get("accepted_staff_id")
            or req.get("accepted_by")
            or 0
        )
    except Exception:
        return 0


def _get_owner_id(token: str) -> int:
    try:
        row = _get_session_row(token) or {}
        return int(row.get("owner_id") or row.get("requester_id") or 0)
    except Exception:
        pass

    try:
        req = VC_REQUESTS.get(token) or {}
        return int(req.get("owner_id") or req.get("requester_id") or 0)
    except Exception:
        return 0


def _get_vc_channel_id_from_session(token: str) -> int:
    try:
        row = _get_session_row(token) or {}
        cid = int(row.get("vc_channel_id") or 0)
        if cid > 0:
            return cid
    except Exception:
        pass

    try:
        req = VC_REQUESTS.get(token) or {}
        cid = int(req.get("vc_channel_id") or 0)
        if cid > 0:
            return cid
    except Exception:
        pass

    return _configured_vc_channel_id()


def _get_ticket_channel_id_from_session(token: str) -> int:
    try:
        row = _get_session_row(token) or {}
        cid = int(row.get("ticket_channel_id") or 0)
        if cid > 0:
            return cid
    except Exception:
        pass

    try:
        req = VC_REQUESTS.get(token) or {}
        cid = int(req.get("ticket_channel_id") or 0)
        if cid > 0:
            return cid
    except Exception:
        pass

    return 0


def _get_session_status(token: str) -> str:
    try:
        row = _get_session_row(token) or {}
        return str(row.get("status") or "").upper().strip()
    except Exception:
        pass

    try:
        req = VC_REQUESTS.get(token) or {}
        return str(req.get("status") or "").upper().strip()
    except Exception:
        return ""


def _vc_session_is_active_status(status: str) -> bool:
    return status in {
        "PENDING",
        "STAFF_ACCEPTED",
        "OWNER_CONFIRMED",
        "READY",
        "IN_VC",
        "STARTED",
        "TAKEN_OVER",
        "RESTARTED",
    }


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

    try:
        ch = guild.get_channel(vc_channel_id)
        if isinstance(ch, (discord.VoiceChannel, discord.StageChannel)):
            return ch
    except Exception:
        pass

    try:
        fetched = await guild.fetch_channel(vc_channel_id)
        if isinstance(fetched, (discord.VoiceChannel, discord.StageChannel)):
            return fetched
    except Exception:
        pass

    return None


def _member_in_target_vc(member: Optional[discord.Member], vc_channel_id: int) -> bool:
    try:
        if not member:
            return False
        state = getattr(member, "voice", None)
        ch = getattr(state, "channel", None)
        return bool(ch and int(getattr(ch, "id", 0) or 0) == int(vc_channel_id))
    except Exception:
        return False


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

    ch = await _resolve_text_channel_by_id(guild, ticket_channel_id)
    if not isinstance(ch, discord.TextChannel):
        return

    try:
        await ch.send(text)
    except Exception:
        pass


# ============================================================
# Overwrite memory / restore helpers
# ============================================================

def _request_store(token: str) -> Dict[str, Any]:
    try:
        VC_REQUESTS.setdefault(str(token), {})
        req = VC_REQUESTS[str(token)]
        if isinstance(req, dict):
            return req
    except Exception:
        pass
    VC_REQUESTS[str(token)] = {}
    return VC_REQUESTS[str(token)]


def _overwrite_store_key(member_id: int) -> str:
    return f"prev_overwrite_values:{int(member_id)}"


def _remember_previous_overwrite(
    *,
    token: str,
    member: discord.Member,
    overwrite: discord.PermissionOverwrite,
) -> None:
    try:
        store = _request_store(token)
        key = _overwrite_store_key(int(member.id))
        if key not in store:
            store[key] = dict(getattr(overwrite, "_values", {}) or {})
    except Exception:
        pass


def _read_previous_overwrite(
    *,
    token: str,
    member: discord.Member,
) -> Dict[str, Any]:
    try:
        store = _request_store(token)
        data = store.get(_overwrite_store_key(int(member.id))) or {}
        if isinstance(data, dict):
            return dict(data)
    except Exception:
        pass
    return {}


def _clear_previous_overwrite(
    *,
    token: str,
    member: discord.Member,
) -> None:
    try:
        store = _request_store(token)
        store.pop(_overwrite_store_key(int(member.id)), None)
    except Exception:
        pass


async def _cleanup_nonstaff_overwrites(
    guild: discord.Guild,
    *,
    token: str,
    keep_members: Optional[List[discord.Member]] = None,
    reason: str,
) -> None:
    vc = await _resolve_session_vc_channel(guild, token=token)
    if not isinstance(vc, (discord.VoiceChannel, discord.StageChannel)):
        return

    me = guild.me
    if not _can_manage_channel(me, vc):
        return

    preserve_ids = {
        int(m.id)
        for m in list(keep_members or [])
        if isinstance(m, discord.Member)
    }

    for target, _overwrite in list(vc.overwrites.items()):
        if not isinstance(target, discord.Member):
            continue
        if _is_staff_member(target):
            continue
        if int(target.id) in preserve_ids:
            continue

        try:
            await vc.set_permissions(
                target,
                overwrite=None,
                reason=reason,
            )
        except Exception:
            continue


# ============================================================
# Session guard
# ============================================================

def _session_unlock_guard(
    *,
    guild: discord.Guild,
    token: str,
    owner: discord.Member,
    staff_member: discord.Member,
) -> Tuple[bool, str]:
    if not token:
        return False, "Missing VC session token."

    if vc_sessions is not None and hasattr(vc_sessions, "session_is_unlockable"):
        try:
            ok, reason = vc_sessions.session_is_unlockable(
                token=str(token),
                expected_guild_id=int(guild.id),
                expected_staff_id=int(staff_member.id),
            )
            if not ok:
                return False, reason
        except Exception as e:
            return False, f"Failed session guard lookup: {e}"

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

    assigned_staff_id = _get_assigned_staff_id(token)
    if assigned_staff_id > 0 and int(staff_member.id) != int(assigned_staff_id):
        return False, "Only the assigned staff member can unlock this VC session."

    return True, "Guard passed."


# ============================================================
# Access lifecycle
# ============================================================

async def _vc_revoke_access(
    guild: discord.Guild,
    member: discord.Member,
    token: str,
    reason: str = "manual",
) -> None:
    vc = await _resolve_session_vc_channel(guild, token=token)
    if not isinstance(vc, (discord.VoiceChannel, discord.StageChannel)):
        return

    me = guild.me
    if not _can_manage_channel(me, vc):
        return

    prev_vals = _read_previous_overwrite(token=token, member=member)

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

    _clear_previous_overwrite(token=token, member=member)


async def _vc_grant_access(
    guild: discord.Guild,
    member: discord.Member,
    token: str,
) -> Tuple[bool, str]:
    vc = await _resolve_session_vc_channel(guild, token=token)
    if not isinstance(vc, (discord.VoiceChannel, discord.StageChannel)):
        return False, "VC verify channel not found (check VC_VERIFY_CHANNEL_ID)."

    me = guild.me
    if not _can_manage_channel(me, vc):
        return False, "I need **Manage Channels** and **View Channel** on the VC verify channel."

    try:
        current = vc.overwrites_for(member)
        _remember_previous_overwrite(token=token, member=member, overwrite=current)
    except Exception:
        pass

    try:
        ow = vc.overwrites_for(member)
        ow.view_channel = True
        ow.connect = True
        ow.speak = True
        ow.use_voice_activation = True
        await vc.set_permissions(
            member,
            overwrite=ow,
            reason=f"VC verify access (token={token})",
        )
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
        await _cleanup_nonstaff_overwrites(
            guild,
            token=token,
            keep_members=[owner, staff_member],
            reason=f"VC session private lock token={token}",
        )
    except Exception:
        pass

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

    if isinstance(owner, discord.Member):
        try:
            await _vc_revoke_access(guild, owner, token, reason=reason)
        except Exception:
            pass

    if isinstance(staff, discord.Member):
        try:
            await _vc_revoke_access(guild, staff, token, reason=reason)
        except Exception:
            pass

    try:
        await _cleanup_nonstaff_overwrites(
            guild,
            token=token,
            keep_members=[],
            reason=f"VC session cleanup token={token}",
        )
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
    if vc_id <= 0:
        return False

    owner = guild.get_member(_get_owner_id(token))
    staff = guild.get_member(_get_assigned_staff_id(token))

    owner_in = _member_in_target_vc(owner, vc_id)
    staff_in = _member_in_target_vc(staff, vc_id)

    return not owner_in and not staff_in


# ============================================================
# VC movement helper
# ============================================================

async def vc_move_member_into_verify_vc(
    *,
    guild: discord.Guild,
    member: discord.Member,
) -> Tuple[bool, str]:
    vc = await _resolve_vc_channel(guild)
    if not isinstance(vc, (discord.VoiceChannel, discord.StageChannel)):
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


# ============================================================
# Sweeper
# ============================================================

async def vc_sweeper_loop(bot_client: discord.Client, *, interval_seconds: int = 120) -> None:
    if not vc_sessions or not hasattr(vc_sessions, "sb_enabled") or not vc_sessions.sb_enabled():
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
            sb = None
            for k in ("sb", "supabase", "SUPABASE"):
                try:
                    v = globals().get(k)
                    if v:
                        sb = v
                        break
                except Exception:
                    continue
            if not sb:
                continue

            now_iso = _utcnow().isoformat()

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
                token = _safe_str((row or {}).get("token"))
                gid = _as_int((row or {}).get("guild_id"), 0)
                if not token or gid <= 0:
                    continue

                guild = bot_client.get_guild(gid)
                if not guild:
                    try:
                        if hasattr(vc_sessions, "transition"):
                            vc_sessions.transition(token=token, new_status="EXPIRED", staff_id=0)
                    except Exception:
                        pass
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

                try:
                    if hasattr(vc_sessions, "transition"):
                        vc_sessions.transition(token=token, new_status="EXPIRED", staff_id=0)
                except Exception:
                    pass

                await _session_notify_ticket_channel(
                    guild,
                    token=token,
                    text="⌛ VC verify session expired after the verify VC became empty.",
                )
            except Exception:
                continue


__all__ = [
    "mark_ticket_activity",
    "_staff_ping_text",
    "_get_vc_channel",
    "_resolve_vc_channel",
    "_get_vc_queue_channel",
    "_resolve_vc_queue_channel",
    "_can_manage_channel",
    "_vc_grant_access",
    "_vc_revoke_access",
    "vc_unlock_session_participants",
    "vc_relock_session",
    "vc_session_everyone_left",
    "vc_move_member_into_verify_vc",
    "vc_sweeper_loop",
]
