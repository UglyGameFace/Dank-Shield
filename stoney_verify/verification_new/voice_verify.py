# stoney_verify/verification_new/voice_verify.py
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

import discord

from ..globals import *  # noqa: F401,F403

from ..tickets import (
    find_ticket_owner_retry,
    is_verification_ticket_channel,
)

from .service import (
    approve_vc_verification,
    deny_vc_verification,
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


try:
    from ..verify_ui import (
        post_or_replace_verify_ui,
        _issue_token_url,  # type: ignore
    )
except Exception:
    async def post_or_replace_verify_ui(*args, **kwargs) -> Optional[str]:  # type: ignore
        return None

    async def _issue_token_url(*args, **kwargs):  # type: ignore
        raise RuntimeError("verify_ui._issue_token_url unavailable")


try:
    from ..tickets_new.repository import safe_optional_update_by_channel_id
except Exception:
    async def safe_optional_update_by_channel_id(*args, **kwargs) -> bool:  # type: ignore
        return False


def _parse_iso_datetime(value: str) -> Optional[datetime]:
    try:
        text = str(value or "").strip()
        if not text:
            return None

        if text.endswith("Z"):
            text = text[:-1] + "+00:00"

        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except Exception:
        return None


def _utc_now() -> datetime:
    try:
        return now_utc()
    except Exception:
        return datetime.now(timezone.utc)


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


try:
    from ..commands_ext.common import (
        VC_REQUESTS,
        VC_REQUEST_COOLDOWNS,
        RUNTIME_STATS,
        _discord_channel_url,
        make_custom_id,
        token_is_expired,
    )
except Exception:
    VC_REQUESTS = {}
    VC_REQUEST_COOLDOWNS = {}
    RUNTIME_STATS = {}

    def _discord_channel_url(guild_id: int, channel_id: int) -> str:
        return f"https://discord.com/channels/{guild_id}/{channel_id}"

    def make_custom_id(action: str, token: str) -> str:
        return f"{action}:{token}"

    def token_is_expired(token_info: Optional[Dict[str, Any]]) -> bool:
        try:
            if not token_info:
                return True

            raw = token_info.get("expires_at")
            if not raw:
                return False

            parsed = _parse_iso_datetime(str(raw or ""))
            if parsed is None:
                return False

            return parsed <= _utc_now()
        except Exception:
            return False


try:
    from ..commands_ext.vc_flow import (
        _get_vc_channel,
        _can_manage_channel,
        _post_staff_vc_request_panel,
        _vc_grant_access,
        _vc_revoke_access,
        _vc_disable_panels_everywhere,
        _cleanup_stale_vc_request,
        _find_active_vc_token_for_channel,
        _resolve_vc_ticket_and_owner,
        _vc_lock_channel_for_session,
        _vc_unlock_channel_for_next_session,
    )
except Exception:
    def _get_vc_channel(guild: discord.Guild) -> Optional[discord.VoiceChannel]:
        try:
            if not VC_VERIFY_CHANNEL_ID:
                return None
            ch = guild.get_channel(int(VC_VERIFY_CHANNEL_ID))
            if isinstance(ch, discord.VoiceChannel):
                return ch
        except Exception:
            pass
        return None

    def _can_manage_channel(
        me: discord.Member,
        channel: discord.abc.GuildChannel,
    ) -> Tuple[bool, str]:
        try:
            perms = channel.permissions_for(me)
            if perms.administrator or perms.manage_channels:
                return True, ""
            return False, f"Bot lacks Manage Channels in {channel.mention}"
        except Exception as e:
            return False, str(e)

    async def _post_staff_vc_request_panel(
        *,
        guild: discord.Guild,
        token: str,
        requester_id: int,
        requester_mention: str,
        ticket_channel_id: int,
    ) -> Optional[int]:
        return None

    async def _vc_grant_access(
        guild: discord.Guild,
        member: discord.Member,
        token: str,
    ) -> Tuple[bool, str]:
        return False, "vc_flow import missing"

    async def _vc_revoke_access(
        guild: discord.Guild,
        member: discord.Member,
        token: str,
        reason: str = "revoke",
    ) -> None:
        return None

    async def _vc_disable_panels_everywhere(
        guild: discord.Guild,
        token: str,
        status_text: str,
    ) -> None:
        return None

    async def _cleanup_stale_vc_request(
        guild: discord.Guild,
        token: str,
        reason: str,
    ) -> bool:
        try:
            VC_REQUESTS.pop(str(token), None)
        except Exception:
            pass
        return True

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
                if status in {
                    "PENDING",
                    "ACCEPTED",
                    "STAFF_ACCEPTED",
                    "READY",
                    "IN_VC",
                    "STARTED",
                    "TAKEN_OVER",
                    "RESTARTED",
                    "UPLOAD_REQUESTED",
                }:
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

        ticket_ch: Optional[discord.TextChannel] = None
        try:
            ch_id = int(str(token_info.get("channel_id") or "0") or 0)
            if ch_id:
                ch = guild.get_channel(ch_id)
                if isinstance(ch, discord.TextChannel):
                    ticket_ch = ch
                else:
                    try:
                        fetched = await guild.fetch_channel(ch_id)
                        if isinstance(fetched, discord.TextChannel):
                            ticket_ch = fetched
                    except Exception:
                        pass
        except Exception:
            ticket_ch = None

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

        return ticket_ch, owner, token_info

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

        ok, msg = await _vc_grant_access(guild, owner, token)
        if not ok:
            return False, msg

        ok2, msg2 = await _vc_grant_access(guild, staff_member, token)
        if not ok2:
            return False, msg2

        return True, "VC unlocked for owner + assigned staff."

    async def _vc_unlock_channel_for_next_session(
        guild: discord.Guild,
        token: str,
    ) -> None:
        return None


try:
    from .. import vc_sessions
except Exception:
    vc_sessions = None  # type: ignore


# ============================================================
# Internal helpers
# ============================================================

def _result(ok: bool, message: str, **extra: Any) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "ok": bool(ok),
        "message": str(message),
    }
    payload.update(extra)
    return payload


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

        if member.guild_permissions.administrator:
            return True
        if member.guild_permissions.manage_guild:
            return True
        if member.guild_permissions.manage_channels:
            return True

        staff_role_id = _safe_int(globals().get("STAFF_ROLE_ID"), 0)
        if staff_role_id > 0:
            return any(int(getattr(role, "id", 0)) == staff_role_id for role in (member.roles or []))

        return False
    except Exception:
        return False


def _build_ticket_vc_link_view(guild_id: int, vc_channel_id: int) -> Optional[discord.ui.View]:
    try:
        view = discord.ui.View(timeout=1800)
        view.add_item(
            discord.ui.Button(
                label="🎙️ Join ID-Verify VC",
                style=discord.ButtonStyle.link,
                url=_discord_channel_url(int(guild_id), int(vc_channel_id)),
            )
        )
        return view
    except Exception:
        return None


def _build_ticket_vc_staff_controls(token: str) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    view.add_item(
        discord.ui.Button(
            label="✅ Approve (VC)",
            style=discord.ButtonStyle.success,
            custom_id=make_custom_id("vc_approve", token),
        )
    )
    view.add_item(
        discord.ui.Button(
            label="⛔ Deny & Close (VC)",
            style=discord.ButtonStyle.danger,
            custom_id=make_custom_id("vc_denyclose", token),
        )
    )
    view.add_item(
        discord.ui.Button(
            label="🧹 End VC Session",
            style=discord.ButtonStyle.secondary,
            custom_id=make_custom_id("vc_end", token),
        )
    )
    return view


async def _safe_send(channel: Optional[discord.TextChannel], *args, **kwargs) -> None:
    try:
        if isinstance(channel, discord.TextChannel):
            await channel.send(*args, **kwargs)
    except Exception:
        pass


async def _safe_edit_message(message: Optional[discord.Message], **kwargs) -> None:
    try:
        if isinstance(message, discord.Message):
            await message.edit(**kwargs)
    except Exception:
        pass


async def _mark_decision_safe(
    token: str,
    decision: str,
    staff_id: int,
    approved_user_id: Optional[int] = None,
) -> None:
    try:
        sb_mark_decision(
            token,
            decision,
            int(staff_id),
            approved_user_id=int(approved_user_id) if approved_user_id is not None else None,
        )
    except Exception:
        pass


async def _set_used_safe(token: str, used: bool = True) -> None:
    try:
        sb_set_used(token, used)
    except Exception:
        pass


def _ensure_staff_member(staff_member: discord.Member) -> Tuple[bool, str]:
    try:
        if not isinstance(staff_member, discord.Member):
            return False, "Staff member context is invalid."
        if not _is_staff_member(staff_member):
            return False, "Staff only."
        return True, ""
    except Exception:
        return False, "Staff validation failed."


def _requester_id_from_token_info(token_info: Optional[Dict[str, Any]]) -> int:
    try:
        if not token_info:
            return 0
        return int(
            str(
                token_info.get("requester_id")
                or token_info.get("user_id")
                or "0"
            ) or 0
        )
    except Exception:
        return 0


def _vc_request_status_active(status: str) -> bool:
    return str(status or "").upper() in {
        "PENDING",
        "ACCEPTED",
        "STAFF_ACCEPTED",
        "READY",
        "IN_VC",
        "STARTED",
        "TAKEN_OVER",
        "RESTARTED",
        "UPLOAD_REQUESTED",
    }


async def _update_ticket_vc_status(
    *,
    ticket_channel: Optional[discord.TextChannel],
    status_text: str,
    owner: Optional[discord.Member] = None,
    staff_member: Optional[discord.Member] = None,
) -> None:
    if not isinstance(ticket_channel, discord.TextChannel):
        return

    try:
        payload: Dict[str, Any] = {
            "decision": status_text,
            "closed_reason": status_text,
        }
        if isinstance(owner, discord.Member):
            payload["user_id"] = str(owner.id)
            payload["username"] = str(owner)
        if isinstance(staff_member, discord.Member):
            payload["assigned_to"] = str(staff_member.id)
            payload["claimed_by"] = str(staff_member.id)
        await safe_optional_update_by_channel_id(ticket_channel.id, payload)
    except Exception:
        pass


async def _upsert_ticket_vc_accept_message(
    *,
    guild: discord.Guild,
    ticket_channel: discord.TextChannel,
    owner: Optional[discord.Member],
    staff_member: discord.Member,
    vc_channel: discord.VoiceChannel,
    token: str,
) -> None:
    access_min = 30
    try:
        access_min = int(globals().get("VC_VERIFY_ACCESS_MINUTES", 30) or 30)
    except Exception:
        access_min = 30

    user_mention = owner.mention if isinstance(owner, discord.Member) else "the user"
    content = (
        f"✅ **VC Verify accepted** by {staff_member.mention}\n\n"
        f"{user_mention} tap below to join <#{vc_channel.id}> now.\n"
        f"⏳ Temporary access expires in ~{access_min} minutes."
    )
    link_view = _build_ticket_vc_link_view(guild.id, vc_channel.id)

    edited = False
    try:
        me_id = int(getattr(getattr(bot, "user", None), "id", 0) or 0)
        async for msg in ticket_channel.history(limit=50):
            if int(getattr(getattr(msg, "author", None), "id", 0) or 0) != me_id:
                continue
            text = str(msg.content or "")
            if (
                "VC verification request sent" in text
                or "VC request sent" in text
                or "Staff will respond here" in text
                or "VC Verify accepted" in text
            ):
                await msg.edit(content=content, view=link_view)
                edited = True
                break
    except Exception:
        edited = False

    if not edited:
        await _safe_send(ticket_channel, content, view=link_view)

    try:
        controls = _build_ticket_vc_staff_controls(token)
        existing_controls: Optional[discord.Message] = None

        me_id = int(getattr(getattr(bot, "user", None), "id", 0) or 0)
        async for msg in ticket_channel.history(limit=30):
            if int(getattr(getattr(msg, "author", None), "id", 0) or 0) != me_id:
                continue
            if "VC staff controls" in str(msg.content or ""):
                existing_controls = msg
                break

        if existing_controls is not None:
            await existing_controls.edit(
                content=f"🧾 **VC staff controls** for {(owner.mention if owner else 'this ticket')}:",
                view=controls,
            )
            try:
                VC_REQUESTS.setdefault(token, {})
                VC_REQUESTS[token]["ticket_panel_msg_id"] = int(existing_controls.id)
            except Exception:
                pass
        else:
            msg = await ticket_channel.send(
                content=f"🧾 **VC staff controls** for {(owner.mention if owner else 'this ticket')}:",
                view=controls,
            )
            try:
                VC_REQUESTS.setdefault(token, {})
                VC_REQUESTS[token]["ticket_panel_msg_id"] = int(msg.id)
            except Exception:
                pass
    except Exception:
        pass


async def _end_vc_session_record(
    *,
    guild: discord.Guild,
    token: str,
    status: str,
    staff_id: int,
) -> None:
    try:
        if vc_sessions and hasattr(vc_sessions, "end_session"):
            await vc_sessions.end_session(
                guild_id=int(guild.id),
                token=str(token),
                status=str(status),
                staff_id=int(staff_id),
            )
            return
    except Exception:
        pass

    try:
        if vc_sessions and hasattr(vc_sessions, "transition"):
            vc_sessions.transition(
                token=str(token),
                new_status=str(status),
                staff_id=int(staff_id),
            )
    except Exception:
        pass


async def _mark_request_status(
    token: str,
    *,
    status: str,
    staff_member: Optional[discord.Member] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> None:
    try:
        req = dict(VC_REQUESTS.get(token) or {})
        req["status"] = str(status)
        if isinstance(staff_member, discord.Member):
            req["handled_by"] = int(staff_member.id)
            req["handled_at"] = _utc_now().isoformat()
        if isinstance(extra, dict):
            req.update(extra)
        VC_REQUESTS[token] = req
    except Exception:
        pass


# ============================================================
# Context
# ============================================================

async def resolve_vc_context(
    *,
    guild: discord.Guild,
    token: Optional[str] = None,
    channel: Optional[discord.TextChannel] = None,
    allow_expired: bool = False,
) -> Dict[str, Any]:
    tok = str(token or "").strip()

    if not tok and isinstance(channel, discord.TextChannel):
        try:
            tok = _find_active_vc_token_for_channel(channel.id) or ""
        except Exception:
            tok = ""

    if not tok:
        return _result(
            False,
            "Missing VC token.",
            token="",
            channel=channel,
            owner=None,
            token_info=None,
            request=None,
        )

    ticket_channel, owner, token_info = await _resolve_vc_ticket_and_owner(guild, tok)
    request = dict(VC_REQUESTS.get(tok) or {})

    if token_info is None:
        return _result(
            False,
            "Invalid or expired token.",
            token=tok,
            channel=ticket_channel,
            owner=owner,
            token_info=None,
            request=request,
        )

    if token_is_expired(token_info) and not allow_expired:
        return _result(
            False,
            "This VC token expired.",
            token=tok,
            channel=ticket_channel,
            owner=owner,
            token_info=token_info,
            request=request,
        )

    if isinstance(channel, discord.TextChannel) and isinstance(ticket_channel, discord.TextChannel):
        try:
            if int(channel.id) != int(ticket_channel.id):
                return _result(
                    False,
                    "That VC token doesn’t belong to this ticket.",
                    token=tok,
                    channel=ticket_channel,
                    owner=owner,
                    token_info=token_info,
                    request=request,
                )
        except Exception:
            pass

    if ticket_channel is None:
        try:
            await _cleanup_stale_vc_request(guild, tok, reason="ticket channel not found")
        except Exception:
            pass
        return _result(
            False,
            "Could not resolve the ticket channel for this VC request.",
            token=tok,
            channel=None,
            owner=owner,
            token_info=token_info,
            request=request,
        )

    if not is_verification_ticket_channel(ticket_channel):
        return _result(
            False,
            "Resolved channel is not a verification ticket.",
            token=tok,
            channel=ticket_channel,
            owner=owner,
            token_info=token_info,
            request=request,
        )

    return _result(
        True,
        "OK",
        token=tok,
        channel=ticket_channel,
        owner=owner,
        token_info=token_info,
        request=request,
    )


# ============================================================
# Ticket-owner flow
# ============================================================

async def queue_vc_request(
    *,
    guild: discord.Guild,
    channel: discord.TextChannel,
    requester: discord.Member,
    token: str,
) -> Dict[str, Any]:
    ctx = await resolve_vc_context(
        guild=guild,
        token=token,
        channel=channel,
        allow_expired=False,
    )
    if not ctx.get("ok"):
        return ctx

    token_info = ctx.get("token_info")
    owner = ctx.get("owner")
    ticket_channel = ctx.get("channel")
    tok = str(ctx.get("token") or token or "").strip()

    if not isinstance(ticket_channel, discord.TextChannel):
        return _result(False, "Could not resolve the verification ticket channel.")

    if not isinstance(requester, discord.Member):
        return _result(False, "Invalid requester context.", channel=ticket_channel, owner=owner)

    expected_uid = _requester_id_from_token_info(token_info)
    if expected_uid and int(requester.id) != int(expected_uid):
        return _result(False, "Only the ticket owner can request VC verify.", channel=ticket_channel, owner=owner)

    if isinstance(owner, discord.Member) and int(owner.id) != int(requester.id):
        return _result(False, "Only the ticket owner can request VC verify.", channel=ticket_channel, owner=owner)

    try:
        if token_info and bool(token_info.get("used", False)):
            return _result(False, "This token has already been used.", channel=ticket_channel, owner=owner)
    except Exception:
        pass

    try:
        cooldown_seconds = int(globals().get("VC_REQUEST_COOLDOWN_SECONDS", 60) or 60)
    except Exception:
        cooldown_seconds = 60

    try:
        last = VC_REQUEST_COOLDOWNS.get(int(requester.id))
        if last and (_utc_now() - last).total_seconds() < cooldown_seconds:
            left = int(cooldown_seconds - (_utc_now() - last).total_seconds())
            return _result(
                False,
                f"Please wait {left}s before requesting VC verify again.",
                cooldown_left_seconds=left,
                channel=ticket_channel,
                owner=owner,
            )
    except Exception:
        pass

    vc_channel = _get_vc_channel(guild)
    if not vc_channel:
        return _result(False, "VC verification channel isn’t configured correctly.", channel=ticket_channel, owner=owner)

    existing = dict(VC_REQUESTS.get(tok) or {})
    if _vc_request_status_active(str(existing.get("status") or "")):
        return _result(
            True,
            "VC request is already queued.",
            token=tok,
            channel=ticket_channel,
            owner=owner,
            request=existing,
            already_exists=True,
        )

    try:
        VC_REQUEST_COOLDOWNS[int(requester.id)] = _utc_now()
    except Exception:
        pass

    VC_REQUESTS[tok] = {
        "status": "PENDING",
        "requested_at": _utc_now().isoformat(),
        "requested_by": int(requester.id),
        "ticket_channel_id": int(ticket_channel.id),
        "guild_id": int(guild.id),
        "staff_msg_ids": [],
    }

    try:
        RUNTIME_STATS["vc_requests"] = int(RUNTIME_STATS.get("vc_requests", 0) or 0) + 1
    except Exception:
        pass

    requester_mention = owner.mention if isinstance(owner, discord.Member) else requester.mention
    staff_mid = await _post_staff_vc_request_panel(
        guild=guild,
        token=tok,
        requester_id=int(requester.id),
        requester_mention=requester_mention,
        ticket_channel_id=int(ticket_channel.id),
    )

    if staff_mid:
        try:
            VC_REQUESTS[tok]["staff_msg_ids"] = [int(staff_mid)]
        except Exception:
            pass

    await _update_ticket_vc_status(
        ticket_channel=ticket_channel,
        status_text="VC REQUESTED",
        owner=owner if isinstance(owner, discord.Member) else None,
        staff_member=None,
    )

    await _safe_send(
        ticket_channel,
        (
            f"🎙️ {(owner.mention if isinstance(owner, discord.Member) else requester.mention)} "
            "**VC verification request sent.**\n"
            "Staff will respond here when they’re ready. Please wait."
        ),
    )

    return _result(
        True,
        "VC verification request queued.",
        token=tok,
        channel=ticket_channel,
        owner=owner,
        vc_channel=vc_channel,
        staff_panel_msg_id=staff_mid,
    )


# ============================================================
# Staff flow
# ============================================================

async def accept_vc_request(
    *,
    guild: discord.Guild,
    token: str,
    staff_member: discord.Member,
    queue_message: Optional[discord.Message] = None,
) -> Dict[str, Any]:
    ok_staff, staff_err = _ensure_staff_member(staff_member)
    if not ok_staff:
        return _result(False, staff_err)

    ctx = await resolve_vc_context(
        guild=guild,
        token=token,
        allow_expired=False,
    )
    if not ctx.get("ok"):
        return ctx

    tok = str(ctx.get("token") or token or "").strip()
    ticket_channel = ctx.get("channel")
    owner = ctx.get("owner")
    token_info = ctx.get("token_info")
    req = dict(ctx.get("request") or {})

    if not isinstance(ticket_channel, discord.TextChannel):
        return _result(False, "Could not resolve the verification ticket channel.")

    if token_info and bool(token_info.get("used", False)):
        return _result(False, "This token has already been used.", channel=ticket_channel, owner=owner)

    if not isinstance(owner, discord.Member):
        return _result(False, "Could not detect ticket owner for VC verification.", channel=ticket_channel, owner=owner)

    accepted_by = int(req.get("accepted_by") or 0) if req else 0
    current_status = str(req.get("status") or "").upper()

    if current_status == "ACCEPTED" and accepted_by == int(staff_member.id):
        return _result(
            True,
            "You already accepted this VC request.",
            token=tok,
            channel=ticket_channel,
            owner=owner,
            already_accepted=True,
        )

    if current_status == "ACCEPTED" and accepted_by and accepted_by != int(staff_member.id):
        return _result(False, "Another staff member already accepted this VC request.", channel=ticket_channel, owner=owner)

    vc_channel = _get_vc_channel(guild)
    if not vc_channel:
        return _result(False, "VC verification channel not found.", channel=ticket_channel, owner=owner)

    me = guild.me
    if not me:
        return _result(False, "Bot member missing.", channel=ticket_channel, owner=owner)

    ok_manage, perm_msg = _can_manage_channel(me, vc_channel)
    if not ok_manage:
        return _result(False, f"Bot lacks required permissions: {perm_msg}", channel=ticket_channel, owner=owner)

    locked = False
    lock_msg = ""
    try:
        locked, lock_msg = await _vc_lock_channel_for_session(
            guild,
            owner,
            staff_member,
            tok,
        )
    except Exception as e:
        locked = False
        lock_msg = str(e)

    if not locked:
        ok_access, access_msg = await _vc_grant_access(guild, owner, tok)
        if not ok_access:
            return _result(False, access_msg or lock_msg or "Failed to grant VC access.", channel=ticket_channel, owner=owner)

        try:
            await _vc_grant_access(guild, staff_member, tok)
        except Exception:
            pass

    try:
        VC_REQUESTS[tok] = {
            **req,
            "status": "ACCEPTED",
            "accepted_by": int(staff_member.id),
            "accepted_at": _utc_now().isoformat(),
            "accepted_staff_id": int(staff_member.id),
            "assigned_staff_id": int(staff_member.id),
            "ticket_channel_id": int(ticket_channel.id),
            "guild_id": int(guild.id),
        }
    except Exception:
        pass

    try:
        RUNTIME_STATS["vc_accepted"] = int(RUNTIME_STATS.get("vc_accepted", 0) or 0) + 1
    except Exception:
        pass

    try:
        await _vc_disable_panels_everywhere(
            guild,
            tok,
            status_text=f"Accepted by {staff_member.mention}",
        )
    except Exception:
        pass

    await _safe_edit_message(
        queue_message,
        content=f"✅ Claimed by {staff_member.mention}.",
        embed=None,
        view=_build_ticket_vc_staff_controls(tok),
    )

    await _update_ticket_vc_status(
        ticket_channel=ticket_channel,
        status_text="VC ACCEPTED",
        owner=owner,
        staff_member=staff_member,
    )

    await _upsert_ticket_vc_accept_message(
        guild=guild,
        ticket_channel=ticket_channel,
        owner=owner,
        staff_member=staff_member,
        vc_channel=vc_channel,
        token=tok,
    )

    return _result(
        True,
        "Accepted VC verify and granted temporary access.",
        token=tok,
        channel=ticket_channel,
        owner=owner,
        vc_channel=vc_channel,
    )


async def request_upload_instead(
    *,
    guild: discord.Guild,
    token: str,
    staff_member: discord.Member,
    queue_message: Optional[discord.Message] = None,
) -> Dict[str, Any]:
    ok_staff, staff_err = _ensure_staff_member(staff_member)
    if not ok_staff:
        return _result(False, staff_err)

    ctx = await resolve_vc_context(
        guild=guild,
        token=token,
        allow_expired=True,
    )
    if not ctx.get("ok"):
        return ctx

    tok = str(ctx.get("token") or token or "").strip()
    ticket_channel = ctx.get("channel")
    owner = ctx.get("owner")

    if not isinstance(ticket_channel, discord.TextChannel):
        return _result(False, "Could not resolve the verification ticket channel.")

    if isinstance(owner, discord.Member):
        try:
            await _vc_revoke_access(guild, owner, tok, reason="upload-requested")
        except Exception:
            pass

    try:
        await _vc_revoke_access(guild, staff_member, tok, reason="upload-requested")
    except Exception:
        pass

    try:
        await _vc_unlock_channel_for_next_session(guild, tok)
    except Exception:
        pass

    try:
        await post_or_replace_verify_ui(
            ticket_channel,
            requester_id=int(owner.id) if isinstance(owner, discord.Member) else None,
            reason=f"vc_upload_requested:{staff_member.id}",
            site_url=VERIFY_SITE_URL,
            ttl_minutes=int(TOKEN_TTL_MINUTES or 20),
            allow_regen=bool(ALLOW_USER_VERIFYLINK),
        )
    except Exception:
        pass

    try:
        await _vc_disable_panels_everywhere(
            guild,
            tok,
            status_text=f"Upload requested by {staff_member.mention}",
        )
    except Exception:
        pass

    await _safe_edit_message(
        queue_message,
        content="✅ VC request handled: staff requested upload instead.",
        view=None,
    )

    await _mark_request_status(
        tok,
        status="UPLOAD_REQUESTED",
        staff_member=staff_member,
    )

    await _update_ticket_vc_status(
        ticket_channel=ticket_channel,
        status_text="VC UPLOAD REQUESTED",
        owner=owner if isinstance(owner, discord.Member) else None,
        staff_member=staff_member,
    )

    await _end_vc_session_record(
        guild=guild,
        token=tok,
        status="CANCELED",
        staff_id=int(staff_member.id),
    )

    try:
        RUNTIME_STATS["vc_upload_requested"] = int(RUNTIME_STATS.get("vc_upload_requested", 0) or 0) + 1
    except Exception:
        pass

    if isinstance(owner, discord.Member):
        await _safe_send(
            ticket_channel,
            f"🔁 {owner.mention} Staff requested **secure upload** instead. Use the **Get Secure Upload** button above.",
        )
    else:
        await _safe_send(
            ticket_channel,
            "🔁 Staff requested **secure upload** instead. Use the **Get Secure Upload** button above.",
        )

    return _result(
        True,
        "Requested upload instead.",
        token=tok,
        channel=ticket_channel,
        owner=owner,
    )


async def reissue_vc_token(
    *,
    guild: discord.Guild,
    token: str,
    staff_member: discord.Member,
    queue_message: Optional[discord.Message] = None,
) -> Dict[str, Any]:
    ok_staff, staff_err = _ensure_staff_member(staff_member)
    if not ok_staff:
        return _result(False, staff_err)

    ctx = await resolve_vc_context(
        guild=guild,
        token=token,
        allow_expired=True,
    )
    if not ctx.get("ok"):
        return ctx

    old_token = str(ctx.get("token") or token or "").strip()
    ticket_channel = ctx.get("channel")
    owner = ctx.get("owner")
    token_info = ctx.get("token_info")
    old_req = dict(ctx.get("request") or {})

    if not isinstance(ticket_channel, discord.TextChannel):
        try:
            await _cleanup_stale_vc_request(guild, old_token, reason="ticket channel not found during reissue")
        except Exception:
            pass
        return _result(False, "I couldn't resolve the ticket channel for this VC request.")

    requester_id = int(owner.id) if isinstance(owner, discord.Member) else _requester_id_from_token_info(token_info)
    if requester_id <= 0:
        requester_id = int(staff_member.id)

    try:
        vc_ttl = int(globals().get("VC_REQUEST_TTL_MINUTES", 0) or 0)
    except Exception:
        vc_ttl = 0
    if vc_ttl <= 0:
        vc_ttl = max(20, int(TOKEN_TTL_MINUTES or 20))

    try:
        await post_or_replace_verify_ui(
            ticket_channel,
            requester_id=int(requester_id),
            reason=f"vc_reissue_upload_ui:{staff_member.id}",
            site_url=VERIFY_SITE_URL,
            ttl_minutes=int(TOKEN_TTL_MINUTES or 20),
            allow_regen=bool(ALLOW_USER_VERIFYLINK),
        )
    except Exception:
        pass

    try:
        new_token, _ = await _issue_token_url(
            site_url=VERIFY_SITE_URL,
            guild=guild,
            channel=ticket_channel,
            requester_id=int(requester_id),
            ttl_minutes=int(vc_ttl),
        )
    except Exception as e:
        return _result(False, f"Failed to reissue VC token: {e}", old_token=old_token)

    VC_REQUESTS[new_token] = {
        "status": "PENDING",
        "requested_at": _utc_now().isoformat(),
        "requested_by": int(requester_id),
        "ticket_channel_id": int(ticket_channel.id),
        "guild_id": int(guild.id),
        "reissued_from": old_token,
        "reissued_by": int(staff_member.id),
        "staff_msg_ids": [],
    }

    requester_mention = owner.mention if isinstance(owner, discord.Member) else f"<@{int(requester_id)}>"
    staff_mid = await _post_staff_vc_request_panel(
        guild=guild,
        token=new_token,
        requester_id=int(requester_id),
        requester_mention=requester_mention,
        ticket_channel_id=int(ticket_channel.id),
    )

    if staff_mid:
        try:
            VC_REQUESTS[new_token]["staff_msg_ids"] = [int(staff_mid)]
        except Exception:
            pass

    try:
        await _vc_disable_panels_everywhere(
            guild,
            old_token,
            status_text=f"Reissued by {staff_member.mention} → new token `{new_token}`",
        )
    except Exception:
        pass

    await _mark_request_status(
        old_token,
        status="REISSUED",
        staff_member=staff_member,
        extra={"reissued_to": new_token},
    )

    old_ticket_panel_id = int(old_req.get("ticket_panel_msg_id") or 0) if old_req else 0
    if old_ticket_panel_id > 0:
        try:
            msg = await ticket_channel.fetch_message(old_ticket_panel_id)
            await msg.edit(view=_build_ticket_vc_staff_controls(new_token))
            VC_REQUESTS[new_token]["ticket_panel_msg_id"] = int(old_ticket_panel_id)
        except Exception:
            pass

    await _safe_edit_message(
        queue_message,
        content=f"♻️ Reissued by {staff_member.mention}.\nNew token: `{new_token}`",
        view=None,
    )

    await _update_ticket_vc_status(
        ticket_channel=ticket_channel,
        status_text="VC REISSUED",
        owner=owner if isinstance(owner, discord.Member) else None,
        staff_member=staff_member,
    )

    return _result(
        True,
        "VC token reissued.",
        old_token=old_token,
        token=new_token,
        channel=ticket_channel,
        owner=owner,
        staff_panel_msg_id=staff_mid,
    )


async def approve_vc_request(
    *,
    guild: discord.Guild,
    token: str,
    staff_member: discord.Member,
) -> Dict[str, Any]:
    ok_staff, staff_err = _ensure_staff_member(staff_member)
    if not ok_staff:
        return _result(False, staff_err)

    ctx = await resolve_vc_context(
        guild=guild,
        token=token,
        allow_expired=True,
    )
    if not ctx.get("ok"):
        return ctx

    tok = str(ctx.get("token") or token or "").strip()
    ticket_channel = ctx.get("channel")
    owner = ctx.get("owner")

    if isinstance(owner, discord.Member):
        try:
            await _vc_revoke_access(guild, owner, tok, reason="decision-made")
        except Exception:
            pass

    try:
        await _vc_revoke_access(guild, staff_member, tok, reason="decision-made")
    except Exception:
        pass

    result = await approve_vc_verification(
        guild=guild,
        channel=ticket_channel if isinstance(ticket_channel, discord.TextChannel) else None,
        token=tok,
        staff_member=staff_member,
        owner=owner if isinstance(owner, discord.Member) else None,
        close_after=True,
    )

    if result.get("ok"):
        try:
            await _vc_disable_panels_everywhere(
                guild,
                tok,
                status_text=f"Approved by {staff_member.mention}",
            )
        except Exception:
            pass

        try:
            await _vc_unlock_channel_for_next_session(guild, tok)
        except Exception:
            pass

        await _mark_request_status(
            tok,
            status="APPROVED",
            staff_member=staff_member,
            extra={
                "approved_by": int(staff_member.id),
                "approved_at": _utc_now().isoformat(),
            },
        )

        await _update_ticket_vc_status(
            ticket_channel=ticket_channel if isinstance(ticket_channel, discord.TextChannel) else None,
            status_text="APPROVED (VC)",
            owner=owner if isinstance(owner, discord.Member) else None,
            staff_member=staff_member,
        )

        await _end_vc_session_record(
            guild=guild,
            token=tok,
            status="COMPLETED",
            staff_id=int(staff_member.id),
        )

        try:
            RUNTIME_STATS["vc_approved"] = int(RUNTIME_STATS.get("vc_approved", 0) or 0) + 1
        except Exception:
            pass

    return result


async def deny_vc_request(
    *,
    guild: discord.Guild,
    token: str,
    staff_member: discord.Member,
) -> Dict[str, Any]:
    ok_staff, staff_err = _ensure_staff_member(staff_member)
    if not ok_staff:
        return _result(False, staff_err)

    ctx = await resolve_vc_context(
        guild=guild,
        token=token,
        allow_expired=True,
    )
    if not ctx.get("ok"):
        return ctx

    tok = str(ctx.get("token") or token or "").strip()
    ticket_channel = ctx.get("channel")
    owner = ctx.get("owner")

    if isinstance(owner, discord.Member):
        try:
            await _vc_revoke_access(guild, owner, tok, reason="denied")
        except Exception:
            pass

    try:
        await _vc_revoke_access(guild, staff_member, tok, reason="denied")
    except Exception:
        pass

    result = await deny_vc_verification(
        guild=guild,
        channel=ticket_channel if isinstance(ticket_channel, discord.TextChannel) else None,
        token=tok,
        staff_member=staff_member,
        close_after=True,
    )

    if result.get("ok"):
        try:
            await _vc_disable_panels_everywhere(
                guild,
                tok,
                status_text=f"Denied by {staff_member.mention}",
            )
        except Exception:
            pass

        try:
            await _vc_unlock_channel_for_next_session(guild, tok)
        except Exception:
            pass

        await _mark_request_status(
            tok,
            status="DENIED",
            staff_member=staff_member,
            extra={
                "denied_by": int(staff_member.id),
                "denied_at": _utc_now().isoformat(),
            },
        )

        await _update_ticket_vc_status(
            ticket_channel=ticket_channel if isinstance(ticket_channel, discord.TextChannel) else None,
            status_text="DENIED (VC)",
            owner=owner if isinstance(owner, discord.Member) else None,
            staff_member=staff_member,
        )

        await _end_vc_session_record(
            guild=guild,
            token=tok,
            status="DENIED",
            staff_id=int(staff_member.id),
        )

        try:
            RUNTIME_STATS["vc_denied"] = int(RUNTIME_STATS.get("vc_denied", 0) or 0) + 1
        except Exception:
            pass

    return result


async def end_vc_session(
    *,
    guild: discord.Guild,
    token: str,
    staff_member: discord.Member,
    reason: str = "ended-by-staff",
) -> Dict[str, Any]:
    ok_staff, staff_err = _ensure_staff_member(staff_member)
    if not ok_staff:
        return _result(False, staff_err)

    ctx = await resolve_vc_context(
        guild=guild,
        token=token,
        allow_expired=True,
    )
    if not ctx.get("ok"):
        return ctx

    tok = str(ctx.get("token") or token or "").strip()
    ticket_channel = ctx.get("channel")
    owner = ctx.get("owner")

    if isinstance(owner, discord.Member):
        try:
            await _vc_revoke_access(guild, owner, tok, reason=reason)
        except Exception:
            pass

    try:
        await _vc_revoke_access(guild, staff_member, tok, reason=reason)
    except Exception:
        pass

    try:
        await _vc_disable_panels_everywhere(
            guild,
            tok,
            status_text=f"Ended by {staff_member.mention}",
        )
    except Exception:
        pass

    try:
        await _vc_unlock_channel_for_next_session(guild, tok)
    except Exception:
        pass

    await _mark_request_status(
        tok,
        status="ENDED",
        staff_member=staff_member,
        extra={
            "ended_reason": str(reason),
        },
    )

    await _update_ticket_vc_status(
        ticket_channel=ticket_channel if isinstance(ticket_channel, discord.TextChannel) else None,
        status_text="VC ENDED",
        owner=owner if isinstance(owner, discord.Member) else None,
        staff_member=staff_member,
    )

    await _end_vc_session_record(
        guild=guild,
        token=tok,
        status="CANCELED",
        staff_id=int(staff_member.id),
    )

    try:
        RUNTIME_STATS["vc_ended"] = int(RUNTIME_STATS.get("vc_ended", 0) or 0) + 1
    except Exception:
        pass

    if isinstance(ticket_channel, discord.TextChannel):
        await _safe_send(
            ticket_channel,
            f"🧹 VC session ended by {staff_member.mention}.",
        )

    return _result(
        True,
        "VC session ended.",
        token=tok,
        channel=ticket_channel,
        owner=owner,
    )


async def takeover_vc_request(
    *,
    guild: discord.Guild,
    staff_member: discord.Member,
    token: Optional[str] = None,
    channel: Optional[discord.TextChannel] = None,
) -> Dict[str, Any]:
    ok_staff, staff_err = _ensure_staff_member(staff_member)
    if not ok_staff:
        return _result(False, staff_err)

    ctx = await resolve_vc_context(
        guild=guild,
        token=token,
        channel=channel,
        allow_expired=True,
    )
    if not ctx.get("ok"):
        return ctx

    tok = str(ctx.get("token") or token or "").strip()
    ticket_channel = ctx.get("channel")
    owner = ctx.get("owner")
    req = dict(ctx.get("request") or {})

    if not isinstance(ticket_channel, discord.TextChannel):
        return _result(False, "Could not resolve the verification ticket channel.")

    prev_staff = int(req.get("accepted_staff_id") or req.get("accepted_by") or 0) if req else 0

    ok_lock, msg_lock = await _vc_lock_channel_for_session(
        guild,
        owner if isinstance(owner, discord.Member) else None,
        staff_member,
        tok,
    )
    if not ok_lock:
        return _result(False, f"Failed to lock VC channel: {msg_lock}", token=tok, channel=ticket_channel, owner=owner)

    try:
        if vc_sessions and hasattr(vc_sessions, "takeover_session"):
            vc_sessions.takeover_session(
                token=str(tok),
                new_staff_id=int(staff_member.id),
                new_staff_name=str(getattr(staff_member, "display_name", staff_member)),
                reason="manual takeover by staff",
            )
    except Exception:
        pass

    await _mark_request_status(
        tok,
        status="TAKEN_OVER",
        staff_member=staff_member,
        extra={
            "accepted_staff_id": int(staff_member.id),
            "accepted_by": int(staff_member.id),
            "takeover_at": _utc_now().isoformat(),
            "takeover_by": int(staff_member.id),
        },
    )

    await _update_ticket_vc_status(
        ticket_channel=ticket_channel,
        status_text="VC TAKEN OVER",
        owner=owner if isinstance(owner, discord.Member) else None,
        staff_member=staff_member,
    )

    await _safe_send(
        ticket_channel,
        (
            f"🔁 **VC verify takeover:** {staff_member.mention} has taken over this VC session"
            + (
                f" from <@{prev_staff}>."
                if prev_staff and prev_staff != int(staff_member.id)
                else "."
            )
        ),
    )

    return _result(
        True,
        "VC session now belongs to the requesting staff member.",
        token=tok,
        channel=ticket_channel,
        owner=owner,
    )


async def unlock_vc_request(
    *,
    guild: discord.Guild,
    staff_member: discord.Member,
    token: Optional[str] = None,
    channel: Optional[discord.TextChannel] = None,
) -> Dict[str, Any]:
    ok_staff, staff_err = _ensure_staff_member(staff_member)
    if not ok_staff:
        return _result(False, staff_err)

    ctx = await resolve_vc_context(
        guild=guild,
        token=token,
        channel=channel,
        allow_expired=True,
    )
    if not ctx.get("ok"):
        return ctx

    tok = str(ctx.get("token") or token or "").strip()
    ticket_channel = ctx.get("channel")
    owner = ctx.get("owner")

    if isinstance(owner, discord.Member):
        try:
            await _vc_revoke_access(guild, owner, tok, reason="manual-unlock")
        except Exception:
            pass

    try:
        await _vc_revoke_access(guild, staff_member, tok, reason="manual-unlock")
    except Exception:
        pass

    try:
        await _vc_disable_panels_everywhere(
            guild,
            tok,
            status_text=f"Unlocked by {staff_member.mention}",
        )
    except Exception:
        pass

    try:
        await _vc_unlock_channel_for_next_session(guild, tok)
    except Exception:
        pass

    await _mark_request_status(
        tok,
        status="COMPLETED",
        staff_member=staff_member,
    )

    await _update_ticket_vc_status(
        ticket_channel=ticket_channel if isinstance(ticket_channel, discord.TextChannel) else None,
        status_text="VC RESET",
        owner=owner if isinstance(owner, discord.Member) else None,
        staff_member=staff_member,
    )

    if isinstance(ticket_channel, discord.TextChannel):
        await _safe_send(
            ticket_channel,
            f"🧹 VC verify channel reset by {staff_member.mention}. Ready for the next person.",
        )

    return _result(
        True,
        "VC channel reset for the next session.",
        token=tok,
        channel=ticket_channel,
        owner=owner,
    )


async def vc_status(
    *,
    guild: discord.Guild,
    token: Optional[str] = None,
    channel: Optional[discord.TextChannel] = None,
) -> Dict[str, Any]:
    ctx = await resolve_vc_context(
        guild=guild,
        token=token,
        channel=channel,
        allow_expired=True,
    )
    if not ctx.get("ok"):
        return ctx

    tok = str(ctx.get("token") or token or "").strip()
    ticket_channel = ctx.get("channel")
    owner = ctx.get("owner")
    request = dict(ctx.get("request") or {})
    token_info = ctx.get("token_info")

    accepted_by = int(request.get("accepted_staff_id") or request.get("accepted_by") or 0)
    requester_id = _requester_id_from_token_info(token_info)

    return _result(
        True,
        "VC status resolved.",
        token=tok,
        channel=ticket_channel,
        owner=owner,
        request=request,
        status=str(request.get("status") or "UNKNOWN"),
        requester_id=requester_id,
        accepted_by=accepted_by,
        used=bool(token_info.get("used", False)) if isinstance(token_info, dict) else False,
        expired=bool(token_is_expired(token_info)),
    )


__all__ = [
    "resolve_vc_context",
    "queue_vc_request",
    "accept_vc_request",
    "request_upload_instead",
    "reissue_vc_token",
    "approve_vc_request",
    "deny_vc_request",
    "end_vc_session",
    "takeover_vc_request",
    "unlock_vc_request",
    "vc_status",
]
