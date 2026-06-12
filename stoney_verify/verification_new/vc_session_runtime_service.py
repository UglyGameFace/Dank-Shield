from __future__ import annotations

"""Runtime service for VC verification session lifecycle.

The event listener owns Discord voice-change triggers. This service owns the
session lifecycle decisions:
- relock the verify VC when the session ends
- mark a VC session completed
- extend/touch session activity
- mark owner/staff presence milestones
- mirror live state into the runtime VC_REQUESTS map
"""

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Set

import discord


@dataclass
class VcRuntimeDeps:
    vc_sessions: Any
    vc_requests: Dict[str, Dict[str, Any]]
    resolve_vc_verify_channel: Callable[[discord.Guild], Any]
    fetch_active_session_rows: Callable[[discord.Guild, int], Any]
    can_manage_channel: Callable[[discord.Member, discord.abc.GuildChannel], Any]
    as_int: Callable[[Any, int], int]
    vc_row_token: Callable[[Dict[str, Any]], str]
    vc_row_status: Callable[[Dict[str, Any]], str]
    vc_owner_id_from_row: Callable[[Dict[str, Any]], int]
    vc_staff_ids_from_row: Callable[[Dict[str, Any]], List[int]]
    vc_meta_dict: Callable[[Dict[str, Any]], Dict[str, Any]]
    member_in_target_voice: Callable[[Optional[discord.Member], int], bool]


def _log(message: str) -> None:
    try:
        print(f"🎙️ vc_runtime {message}")
    except Exception:
        pass


def _warn(message: str) -> None:
    try:
        print(f"⚠️ vc_runtime {message}")
    except Exception:
        pass


async def vc_channel_is_empty(channel: discord.abc.GuildChannel) -> bool:
    try:
        if isinstance(channel, (discord.VoiceChannel, discord.StageChannel)):
            return len(channel.members) == 0
    except Exception:
        pass
    return False


async def relock_session_channel(
    guild: discord.Guild,
    row: Dict[str, Any],
    *,
    reason: str = "vc session ended",
    deps: VcRuntimeDeps,
) -> bool:
    try:
        ch = await deps.resolve_vc_verify_channel(guild)
        if not isinstance(ch, (discord.VoiceChannel, discord.StageChannel)):
            return False

        me = guild.me
        if me is None:
            _warn("relock skipped: bot member missing")
            return False

        try:
            can_manage = deps.can_manage_channel(me, ch)
            if isinstance(can_manage, tuple):
                ok, why = bool(can_manage[0]), str(can_manage[1] if len(can_manage) > 1 else "")
            else:
                ok, why = bool(can_manage), ""
        except Exception as e:
            ok, why = False, repr(e)

        if not ok:
            _warn(f"relock skipped: bot cannot manage VC verify channel. reason={why}")
            return False

        owner_id = deps.vc_owner_id_from_row(row)
        staff_ids = deps.vc_staff_ids_from_row(row)
        touched = False

        if owner_id > 0:
            try:
                owner = guild.get_member(owner_id) or await guild.fetch_member(owner_id)
            except Exception:
                owner = None
            if owner:
                try:
                    await ch.set_permissions(owner, overwrite=None, reason=reason)
                    touched = True
                except Exception as e:
                    _warn(f"failed clearing VC overwrite for owner {owner_id}: {e!r}")

        for sid in staff_ids:
            try:
                staff_member = guild.get_member(sid) or await guild.fetch_member(sid)
            except Exception:
                staff_member = None
            if staff_member is None:
                continue
            try:
                await ch.set_permissions(staff_member, overwrite=None, reason=reason)
                touched = True
            except Exception as e:
                _warn(f"failed clearing VC overwrite for staff {sid}: {e!r}")

        return touched
    except Exception as e:
        _warn(f"relock_session_channel failed: {e!r}")
        return False


async def mark_session_completed(
    guild: discord.Guild,
    row: Dict[str, Any],
    *,
    deps: VcRuntimeDeps,
) -> None:
    token = str(row.get("token") or "").strip()
    if not token or deps.vc_sessions is None:
        return

    try:
        await deps.vc_sessions.end_session(
            guild_id=int(guild.id),
            token=token,
            status="COMPLETED",
            staff_id=0,
        )
        return
    except Exception:
        pass

    try:
        deps.vc_sessions.transition(
            token=token,
            new_status="COMPLETED",
            staff_id=0,
        )
    except Exception:
        pass


async def touch_session_activity(
    guild: discord.Guild,
    row: Dict[str, Any],
    *,
    reason: str,
    deps: VcRuntimeDeps,
) -> None:
    token = deps.vc_row_token(row)
    if not token or deps.vc_sessions is None:
        return

    try:
        primary_staff_id = 0
        staff_ids = deps.vc_staff_ids_from_row(row)
        if staff_ids:
            primary_staff_id = int(staff_ids[0])

        if hasattr(deps.vc_sessions, "extend_expiry"):
            deps.vc_sessions.extend_expiry(
                token=token,
                minutes=deps.as_int(row.get("access_minutes"), 0),
                reason=reason,
                by_staff_id=primary_staff_id,
            )
    except Exception:
        pass

    try:
        if hasattr(deps.vc_sessions, "touch_watchdog"):
            deps.vc_sessions.touch_watchdog(token)
    except Exception:
        pass


async def mark_owner_confirmed_if_needed(
    row: Dict[str, Any],
    owner: Optional[discord.Member],
    verify_vc_id: int,
    *,
    deps: VcRuntimeDeps,
) -> None:
    try:
        if owner is None or deps.vc_sessions is None or not hasattr(deps.vc_sessions, "set_owner_confirmed"):
            return
        if not deps.member_in_target_voice(owner, verify_vc_id):
            return
        token = deps.vc_row_token(row)
        if not token:
            return
        meta = deps.vc_meta_dict(row)
        if bool(meta.get("owner_confirmed")):
            return
        deps.vc_sessions.set_owner_confirmed(
            token=token,
            owner_id=int(owner.id),
        )
    except Exception:
        pass


async def mark_started_if_needed(
    row: Dict[str, Any],
    owner: Optional[discord.Member],
    staff_members: List[discord.Member],
    verify_vc_id: int,
    *,
    deps: VcRuntimeDeps,
) -> None:
    try:
        if deps.vc_sessions is None or not hasattr(deps.vc_sessions, "mark_started"):
            return

        token = deps.vc_row_token(row)
        if not token:
            return

        status = deps.vc_row_status(row)
        if status in {"STARTED", "IN_VC", "COMPLETED", "CANCELED", "EXPIRED"}:
            return

        owner_in = deps.member_in_target_voice(owner, verify_vc_id)
        staff_in_members = [m for m in staff_members if deps.member_in_target_voice(m, verify_vc_id)]
        if not owner_in or not staff_in_members:
            return

        deps.vc_sessions.mark_started(
            token=token,
            by_staff_id=int(staff_in_members[0].id),
        )
    except Exception:
        pass


async def sync_runtime_request_state(
    row: Dict[str, Any],
    owner: Optional[discord.Member],
    staff_members: List[discord.Member],
    verify_vc_id: int,
    *,
    deps: VcRuntimeDeps,
) -> None:
    try:
        token = deps.vc_row_token(row)
        if not token:
            return

        req = deps.vc_requests.get(token) or {}
        owner_in = deps.member_in_target_voice(owner, verify_vc_id)
        staff_in = any(deps.member_in_target_voice(m, verify_vc_id) for m in staff_members)

        if owner_in and staff_in:
            req["status"] = "IN_VC"
        elif staff_in:
            req["status"] = "STARTED"
        elif owner_in:
            req["status"] = "READY"
        else:
            req.setdefault("status", deps.vc_row_status(row) or "PENDING")

        req["owner_id"] = int(owner.id) if isinstance(owner, discord.Member) else deps.vc_owner_id_from_row(row)
        req["ticket_channel_id"] = deps.as_int(row.get("ticket_channel_id"), 0)
        req["vc_channel_id"] = int(verify_vc_id)
        req["guild_id"] = int(row.get("guild_id") or 0)
        if staff_members:
            req["assigned_staff_id"] = int(staff_members[0].id)
            req["accepted_staff_id"] = int(staff_members[0].id)
        deps.vc_requests[token] = req
    except Exception:
        pass


async def maybe_finish_vc_sessions_after_voice_change(
    guild: discord.Guild,
    changed_channel_ids: Set[int],
    *,
    deps: VcRuntimeDeps,
) -> None:
    try:
        verify_ch = await deps.resolve_vc_verify_channel(guild)
        if not isinstance(verify_ch, (discord.VoiceChannel, discord.StageChannel)):
            return

        verify_vc_id = int(verify_ch.id)
        if verify_vc_id not in changed_channel_ids:
            return

        rows = await deps.fetch_active_session_rows(guild, verify_vc_id)
        if not rows:
            return

        if await vc_channel_is_empty(verify_ch):
            for row in rows:
                try:
                    await relock_session_channel(
                        guild,
                        row,
                        reason="VC verify session ended and channel emptied",
                        deps=deps,
                    )
                    await mark_session_completed(guild, row, deps=deps)

                    ticket_channel_id = deps.as_int(row.get("ticket_channel_id"), 0)
                    if ticket_channel_id > 0:
                        try:
                            ticket_ch = guild.get_channel(ticket_channel_id)
                            if ticket_ch is None:
                                ticket_ch = await guild.fetch_channel(ticket_channel_id)
                            if isinstance(ticket_ch, discord.TextChannel):
                                await ticket_ch.send(
                                    "🔒 VC verify session ended. The ID Verify VC has been locked again."
                                )
                        except Exception:
                            pass
                except Exception as e:
                    _warn(f"session finalize loop error: {e!r}")
            return

        for row in rows:
            try:
                owner_id = deps.vc_owner_id_from_row(row)
                owner = None
                if owner_id > 0:
                    try:
                        owner = guild.get_member(owner_id) or await guild.fetch_member(owner_id)
                    except Exception:
                        owner = None

                staff_members: List[discord.Member] = []
                for sid in deps.vc_staff_ids_from_row(row):
                    try:
                        member = guild.get_member(sid) or await guild.fetch_member(sid)
                        if isinstance(member, discord.Member):
                            staff_members.append(member)
                    except Exception:
                        continue

                await touch_session_activity(
                    guild,
                    row,
                    reason="verify vc still has active users",
                    deps=deps,
                )
                await mark_owner_confirmed_if_needed(row, owner, verify_vc_id, deps=deps)
                await mark_started_if_needed(row, owner, staff_members, verify_vc_id, deps=deps)
                await sync_runtime_request_state(row, owner, staff_members, verify_vc_id, deps=deps)
            except Exception as e:
                _warn(f"session live-state reconcile error: {e!r}")
    except Exception as e:
        _warn(f"maybe_finish_vc_sessions_after_voice_change error: {e!r}")


__all__ = [
    "VcRuntimeDeps",
    "mark_owner_confirmed_if_needed",
    "mark_session_completed",
    "mark_started_if_needed",
    "maybe_finish_vc_sessions_after_voice_change",
    "relock_session_channel",
    "sync_runtime_request_state",
    "touch_session_activity",
    "vc_channel_is_empty",
]
