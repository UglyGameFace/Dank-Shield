from __future__ import annotations

import asyncio
import inspect
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Set, Tuple

import discord

from .globals import *  # noqa: F401,F403

from .tickets import (
    find_ticket_owner_retry,
    is_verification_ticket_channel,
    parse_mod_id,
    _parse_webhook_id_from_url,  # type: ignore
)

from .commands_ext.common import (
    VC_REQUESTS,
    VC_REQUEST_COOLDOWNS,
    RUNTIME_STATS,
    TICKET_LAST_ACTIVITY,
    VC_ACCESS_TASKS,
    ACTIVE_DECISION_PANEL_MSG_ID,
    RECENT_SUBMISSION_TOKENS,
    RECENT_SUBMISSION_MSG_IDS,
    KICK_TIMER_TASKS,
    KICK_TIMER_STARTS,
    KICK_TIMER_STARTED_BY,
    SITE_URL,
    ALLOW_USER_VERIFYLINK,
    VC_VERIFY_ACCESS_MINUTES,
    _get_lock,
    _discord_channel_url,
    extract_token_from_message,
    make_custom_id,
    mark_ticket_activity,
    parse_custom_id,
    token_is_expired,
)

try:
    from .store import (
        sb_get_token_info,
        sb_mark_decision,
        sb_set_submitted,
        sb_set_submitted_at,
        sb_set_used,
    )
except Exception:
    def sb_get_token_info(token: str) -> Optional[Dict[str, Any]]:  # type: ignore
        return None

    def sb_mark_decision(  # type: ignore
        token: str,
        decision: str,
        staff_id: int,
        approved_user_id: Optional[int] = None,
    ) -> None:
        return None

    def sb_set_submitted(token: str) -> None:  # type: ignore
        return None

    def sb_set_submitted_at(token: str, submitted_at=None) -> None:  # type: ignore
        return None

    def sb_set_used(token: str, used: bool = True) -> None:  # type: ignore
        return None


try:
    from .verify_ui import (
        maybe_handle_verify_ui_interaction,
        post_or_replace_verify_ui,
    )
except Exception:
    async def maybe_handle_verify_ui_interaction(interaction: discord.Interaction, *, site_url: str) -> bool:  # type: ignore
        return False

    async def post_or_replace_verify_ui(*args, **kwargs) -> Optional[str]:  # type: ignore
        return None


try:
    from .transcripts import (
        auto_close_after_decision,
        check_bot_can_assign_roles,
    )
except Exception:
    async def auto_close_after_decision(*args, **kwargs) -> None:  # type: ignore
        return None

    async def check_bot_can_assign_roles(*args, **kwargs) -> Tuple[bool, str, List[discord.Role]]:  # type: ignore
        return (False, "transcripts.py missing", [])


try:
    from .commands_ext.kick_timers import (
        _cancel_kick_timer,
        kick_timer_persist_delete,
    )
except Exception:
    def _cancel_kick_timer(channel_id: int) -> bool:
        try:
            task = KICK_TIMER_TASKS.get(int(channel_id))
            if task and not task.done():
                task.cancel()
                return True
        except Exception:
            pass
        return False

    async def kick_timer_persist_delete(channel_id: int) -> None:
        return None


try:
    from .commands_ext.vc_flow import (
        VC_STAFF_ACTIONS,
        VC_ACTIVE_STATUSES,
        VC_TERMINAL_STATUSES,
        _can_manage_channel,
        _find_active_vc_token_for_channel,
        _get_vc_channel,
        _resolve_ticket_channel_from_token_info,
        _vc_lock_channel_for_session,
    )
except Exception:
    VC_STAFF_ACTIONS = {
        "vc_accept",
        "vc_start",
        "vc_complete",
        "vc_cancel",
        "vc_upload",
        "vc_reissue",
        "vc_end",
        "vc_approve",
        "vc_denyclose",
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
        "APPROVED",
        "COMPLETED",
        "DONE",
        "CANCELED",
        "DENIED",
        "UPLOAD_REQUESTED",
        "EXPIRED",
        "STALE",
        "PANEL_FAILED",
        "ENDED",
        "REISSUED",
    }

    def _get_vc_channel(guild: discord.Guild) -> Optional[discord.VoiceChannel]:
        try:
            vc_id = int(globals().get("VC_VERIFY_CHANNEL_ID", 0) or globals().get("VC_VERIFY_VC_ID", 0) or 0)
            if not vc_id:
                return None
            ch = guild.get_channel(vc_id)
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

    def _resolve_ticket_channel_from_token_info(
        guild: discord.Guild,
        token_info: Dict[str, Any],
    ) -> Optional[discord.TextChannel]:
        try:
            ch_id = int(str(token_info.get("channel_id") or "0") or 0)
            if not ch_id:
                return None
            ch = guild.get_channel(ch_id)
            if isinstance(ch, discord.TextChannel):
                return ch
        except Exception:
            pass
        return None

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

    async def _vc_lock_channel_for_session(
        guild: discord.Guild,
        owner: Optional[discord.Member],
        staff_member: Optional[discord.Member],
        token: str,
    ) -> Tuple[bool, str]:
        _ = guild
        _ = owner
        _ = staff_member
        _ = token
        return False, "vc_flow import missing"


try:
    from .verification_new.service import (
        approve_verification,
        deny_verification,
        request_resubmission,
    )
except Exception:
    async def approve_verification(*args, **kwargs) -> Dict[str, Any]:  # type: ignore
        return {"ok": False, "message": "verification_new.service missing"}

    async def deny_verification(*args, **kwargs) -> Dict[str, Any]:  # type: ignore
        return {"ok": False, "message": "verification_new.service missing"}

    async def request_resubmission(*args, **kwargs) -> Dict[str, Any]:  # type: ignore
        return {"ok": False, "message": "verification_new.service missing"}


try:
    from .vc_verify import vc_move_member_into_verify_vc
except Exception:
    async def vc_move_member_into_verify_vc(*args, **kwargs) -> Tuple[bool, str]:  # type: ignore
        return False, "vc_verify.py missing"


try:
    from .identity_proof_service import record_verified_identity_for_user
except Exception:
    def record_verified_identity_for_user(  # type: ignore
        *,
        guild_id: Any,
        user_id: Any,
        identity_fingerprint: str,
        source: str,
        created_by: Optional[str] = None,
        fingerprint_version: str = "v1",
        confidence: int = 100,
        notes: Optional[str] = None,
        evidence: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        return {}


_INTERACTION_HANDLERS_REGISTERED = False


# ============================================================
# Lazy voice_verify service wrappers
# ------------------------------------------------------------
# interaction_handlers should not own the canonical VC lifecycle.
# Accept / upload / reissue / approve / deny / end delegate to
# verification_new.voice_verify.
# ============================================================

async def _voice_verify_accept_vc_request(*args, **kwargs):
    from .verification_new.voice_verify import accept_vc_request
    return await accept_vc_request(*args, **kwargs)


async def _voice_verify_request_upload_instead(*args, **kwargs):
    from .verification_new.voice_verify import request_upload_instead
    return await request_upload_instead(*args, **kwargs)


async def _voice_verify_reissue_vc_token(*args, **kwargs):
    from .verification_new.voice_verify import reissue_vc_token
    return await reissue_vc_token(*args, **kwargs)


async def _voice_verify_approve_vc_request(*args, **kwargs):
    from .verification_new.voice_verify import approve_vc_request
    return await approve_vc_request(*args, **kwargs)


async def _voice_verify_deny_vc_request(*args, **kwargs):
    from .verification_new.voice_verify import deny_vc_request
    return await deny_vc_request(*args, **kwargs)


async def _voice_verify_end_vc_session(*args, **kwargs):
    from .verification_new.voice_verify import end_vc_session
    return await end_vc_session(*args, **kwargs)


# ============================================================
# Small helpers
# ============================================================

def _bump_runtime_stat(key: str, amount: int = 1) -> None:
    try:
        RUNTIME_STATS[key] = int(RUNTIME_STATS.get(key, 0) or 0) + int(amount)
    except Exception:
        try:
            RUNTIME_STATS[key] = int(amount)
        except Exception:
            pass


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


def _role_by_id(guild: discord.Guild, role_id: int) -> Optional[discord.Role]:
    try:
        if not guild or not role_id or int(role_id) <= 0:
            return None
        role = guild.get_role(int(role_id))
        return role if isinstance(role, discord.Role) else None
    except Exception:
        return None


def _configured_vc_channel_id() -> int:
    try:
        return int(globals().get("VC_VERIFY_CHANNEL_ID", 0) or globals().get("VC_VERIFY_VC_ID", 0) or 0)
    except Exception:
        return 0


def _configured_vc_access_minutes() -> int:
    try:
        return int(globals().get("VC_VERIFY_ACCESS_MINUTES", 30) or 30)
    except Exception:
        return 30


def _interaction_action_lock(action: str, token: Optional[str], channel_id: Optional[int | str] = None) -> asyncio.Lock:
    key = f"interaction:{_safe_str(action)}:{_safe_str(token) or _safe_str(channel_id) or 'default'}"
    return _get_lock(key)


def _member_is_elevated_staff(member: Optional[discord.Member]) -> bool:
    try:
        if not isinstance(member, discord.Member):
            return False
        if member.guild_permissions.administrator:
            return True
        if member.guild_permissions.manage_channels:
            return True
        if member.guild_permissions.manage_guild:
            return True
    except Exception:
        pass
    return False


def _vc_request_cache(token: str) -> Dict[str, Any]:
    try:
        req = VC_REQUESTS.get(str(token)) or {}
        return dict(req) if isinstance(req, dict) else {}
    except Exception:
        return {}


def _set_vc_request_cache(token: str, patch: Dict[str, Any]) -> Dict[str, Any]:
    current = _vc_request_cache(token)
    merged = {**current, **dict(patch or {})}
    try:
        VC_REQUESTS[str(token)] = merged
    except Exception:
        pass
    return merged


def _normalize_vc_status(status: Any) -> str:
    raw = _safe_str(status).upper()
    aliases = {
        "ACCEPTED": "STAFF_ACCEPTED",
        "STAFF_ACCEPTED": "STAFF_ACCEPTED",
        "REISSUE": "REISSUED",
        "UPLOAD": "UPLOAD_REQUESTED",
        "DONE": "COMPLETED",
        "CLOSED": "ENDED",
    }
    return aliases.get(raw, raw)


def _vc_request_status(token: str) -> str:
    try:
        return _normalize_vc_status((_vc_request_cache(token) or {}).get("status") or "")
    except Exception:
        return ""


def _vc_request_assigned_staff_id(token: str) -> int:
    req = _vc_request_cache(token)
    return int(
        req.get("accepted_staff_id")
        or req.get("accepted_by")
        or req.get("assigned_staff_id")
        or 0
    )


def _vc_current_panel_message_ids(token: str) -> Set[int]:
    ids: Set[int] = set()
    req = _vc_request_cache(token)
    for key in ("staff_panel_msg_id", "ticket_panel_msg_id", "queue_message_id"):
        try:
            mid = int(req.get(key) or 0)
            if mid > 0:
                ids.add(mid)
        except Exception:
            continue
    return ids


def _vc_interaction_message_is_current(interaction: discord.Interaction, token: str) -> bool:
    try:
        if not interaction.message:
            return True
        current_ids = _vc_current_panel_message_ids(token)
        if not current_ids:
            return True
        return int(interaction.message.id) in current_ids
    except Exception:
        return True


def _vc_action_allowed_for_status(action: str, status: str) -> bool:
    current = _normalize_vc_status(status)

    acceptable = {"", "PENDING", "UPLOAD_REQUESTED", "REISSUED"}
    startable = {"STAFF_ACCEPTED", "READY", "TAKEN_OVER", "RESTARTED"}
    decisionable = {"STAFF_ACCEPTED", "READY", "STARTED", "IN_VC", "TAKEN_OVER", "RESTARTED"}
    uploadable = {"PENDING", "UPLOAD_REQUESTED", "STAFF_ACCEPTED", "READY", "REISSUED"}
    cancelable = {"", "PENDING", "UPLOAD_REQUESTED", "STAFF_ACCEPTED", "READY", "STARTED", "IN_VC", "TAKEN_OVER", "RESTARTED"}
    reissueable = {"", "PENDING", "UPLOAD_REQUESTED", "STAFF_ACCEPTED", "READY", "STARTED", "IN_VC", "TAKEN_OVER", "RESTARTED", "EXPIRED", "CANCELED"}

    if action == "vc_accept":
        return current in acceptable
    if action == "vc_start":
        return current in startable
    if action in {"vc_approve", "vc_denyclose"}:
        return current in decisionable
    if action == "vc_upload":
        return current in uploadable
    if action in {"vc_cancel", "vc_end", "vc_complete"}:
        return current in cancelable
    if action == "vc_reissue":
        return current in reissueable
    return False


def _vc_action_status_error(action: str, status: str) -> str:
    human = _normalize_vc_status(status) or "UNKNOWN"
    if action == "vc_accept":
        return f"❌ This VC request is already in status `{human}`. Use the latest controls."
    if action == "vc_start":
        return f"❌ This VC request is not ready to start from status `{human}`."
    if action in {"vc_approve", "vc_denyclose"}:
        return f"❌ This VC request is not in an active VC session state. Current status: `{human}`."
    if action == "vc_upload":
        return f"❌ Upload reroute is not valid from status `{human}`."
    if action in {"vc_cancel", "vc_end", "vc_complete"}:
        return f"❌ This VC request is already finished. Current status: `{human}`."
    return f"❌ This VC request cannot perform `{action}` from status `{human}`."


async def _resolve_ticket_channel_from_token_info_safe(
    guild: discord.Guild,
    token_info: Dict[str, Any],
) -> Optional[discord.TextChannel]:
    try:
        result = _resolve_ticket_channel_from_token_info(guild, token_info)
        if inspect.isawaitable(result):
            result = await result
        if isinstance(result, discord.TextChannel):
            return result
    except Exception as e:
        print(f"⚠️ _resolve_ticket_channel_from_token_info_safe failed: {repr(e)}")
    return None


async def _resolve_owner_from_token_or_ticket(
    *,
    guild: discord.Guild,
    channel: discord.TextChannel,
    token_info: Optional[Dict[str, Any]],
) -> Optional[discord.Member]:
    owner = None

    try:
        owner = await find_ticket_owner_retry(channel)
    except Exception:
        owner = None

    if owner is not None:
        return owner

    try:
        expected_uid = token_info.get("requester_id") or token_info.get("user_id") if isinstance(token_info, dict) else None
        if expected_uid:
            uid = int(str(expected_uid))
            return guild.get_member(uid) or await guild.fetch_member(uid)
    except Exception:
        return None

    return None


async def _send_or_edit_ticket_vc_status(
    *,
    ticket_channel: discord.TextChannel,
    owner: Optional[discord.Member],
    content_msg: str,
    view: Optional[discord.ui.View] = None,
) -> None:
    try:
        me_id = int(getattr(getattr(bot, "user", None), "id", 0) or 0)
    except Exception:
        me_id = 0

    edited = False

    try:
        async for msg in ticket_channel.history(limit=60):
            try:
                if int(getattr(getattr(msg, "author", None), "id", 0) or 0) != me_id:
                    continue
                text = str(msg.content or "")
                if (
                    "VC verification request sent" in text
                    or "VC request sent" in text
                    or "Staff has been notified" in text
                    or "VC Verify accepted" in text
                    or "VC session started" in text
                ):
                    await msg.edit(content=content_msg, view=view)
                    edited = True
                    break
            except Exception:
                continue
    except Exception:
        edited = False

    if not edited:
        await ticket_channel.send(content_msg, view=view)


async def _defer_ephemeral(interaction: discord.Interaction) -> None:
    try:
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True)
    except Exception:
        pass


async def _disable_interaction_message_if_possible(
    interaction: discord.Interaction,
    *,
    content_suffix: str = "",
) -> None:
    try:
        if not interaction.message:
            return

        base = interaction.message.content or ""
        if content_suffix:
            if base:
                base = f"{base}\n{content_suffix}"
            else:
                base = content_suffix

        await interaction.message.edit(content=base, view=None)
    except Exception:
        pass


async def _send_followup_result(
    interaction: discord.Interaction,
    result: Dict[str, Any],
    *,
    success_prefix: str = "✅",
    failure_prefix: str = "❌",
) -> None:
    ok = bool(result.get("ok"))
    message = _safe_str(result.get("message")) or ("Success." if ok else "Operation failed.")
    prefix = success_prefix if ok else failure_prefix
    await interaction.followup.send(f"{prefix} {message}", ephemeral=True)


def _service_missing_result(result: Optional[Dict[str, Any]]) -> bool:
    try:
        if not isinstance(result, dict):
            return True
        if bool(result.get("ok")):
            return False
        text = str(result.get("message") or "").lower()
        return (
            "verification_new.service missing" in text
            or "service missing" in text
        )
    except Exception:
        return True


def _member_has_any_role(member: Optional[discord.Member], role_ids: List[int]) -> bool:
    try:
        if not isinstance(member, discord.Member):
            return False
        wanted = {int(r) for r in role_ids if int(r) > 0}
        if not wanted:
            return False
        return any(int(getattr(role, "id", 0) or 0) in wanted for role in member.roles)
    except Exception:
        return False


def _member_looks_already_verified(member: Optional[discord.Member]) -> bool:
    try:
        if not isinstance(member, discord.Member):
            return False

        verified_ids: List[int] = []
        for raw in [
            VERIFIED_ROLE_ID,
            RESIDENT_ROLE_ID,
            STONER_ROLE_ID,
            DRUNKEN_ROLE_ID,
        ]:
            try:
                rid = int(raw or 0)
                if rid > 0:
                    verified_ids.append(rid)
            except Exception:
                continue

        has_verified = _member_has_any_role(member, verified_ids)

        unverified_id = 0
        try:
            unverified_id = int(UNVERIFIED_ROLE_ID or 0)
        except Exception:
            unverified_id = 0

        has_unverified = _member_has_any_role(member, [unverified_id]) if unverified_id > 0 else False
        return bool(has_verified and not has_unverified)
    except Exception:
        return False


async def _remove_unverified_role_if_present(
    member: Optional[discord.Member],
    *,
    reason: str,
) -> Tuple[bool, Optional[str]]:
    try:
        if not isinstance(member, discord.Member):
            return False, None

        unverified_role = _role_by_id(member.guild, int(UNVERIFIED_ROLE_ID or 0))
        if not unverified_role or unverified_role not in member.roles:
            return False, None

        await member.remove_roles(unverified_role, reason=reason)
        return True, None
    except discord.Forbidden:
        return False, "I can't remove the Unverified role. Check role hierarchy and Manage Roles."
    except Exception as e:
        return False, str(e)


def _extract_identity_fingerprint(token_info: Optional[Dict[str, Any]]) -> Optional[str]:
    if not isinstance(token_info, dict):
        return None

    keys = (
        "identity_fingerprint",
        "verification_fingerprint",
        "verified_identity_fingerprint",
        "proof_fingerprint",
        "document_fingerprint",
        "face_fingerprint",
        "person_fingerprint",
        "id_hash",
        "identity_hash",
    )
    for key in keys:
        try:
            value = _safe_str(token_info.get(key))
            if value:
                return value
        except Exception:
            continue
    return None


def _extract_identity_source(token_info: Optional[Dict[str, Any]], *, default: str) -> str:
    if not isinstance(token_info, dict):
        return default

    candidates = (
        _safe_str(token_info.get("identity_source")),
        _safe_str(token_info.get("verification_source")),
        _safe_str(token_info.get("proof_source")),
        _safe_str(token_info.get("source")),
        default,
    )

    allowed = {
        "manual_review",
        "id_verification",
        "voice_verification",
        "document_verification",
        "selfie_match",
        "external_account_link",
        "trusted_admin_override",
    }

    for candidate in candidates:
        text = candidate.lower().strip()
        if text in allowed:
            return text

    text = _safe_str(token_info.get("verification_source") or token_info.get("source")).lower()
    if "voice" in text or "vc" in text:
        return "voice_verification"
    if "document" in text or "id" in text:
        return "document_verification"
    if "selfie" in text or "face" in text:
        return "selfie_match"
    return default


async def _persist_identity_proof_on_approval(
    *,
    guild: discord.Guild,
    owner: Optional[discord.Member],
    token: str,
    token_info: Optional[Dict[str, Any]],
    staff_member: discord.Member,
    channel: discord.TextChannel,
    approval_mode: str,
) -> Tuple[bool, Optional[str]]:
    try:
        if not isinstance(owner, discord.Member):
            return False, None

        fingerprint = _extract_identity_fingerprint(token_info)
        if not fingerprint:
            return False, None

        source = _extract_identity_source(
            token_info,
            default=("voice_verification" if approval_mode == "vc" else "manual_review"),
        )

        evidence = {
            "token": token,
            "channel_id": str(channel.id),
            "guild_id": str(guild.id),
            "approved_by": str(staff_member.id),
            "approved_by_name": getattr(staff_member, "display_name", None) or getattr(staff_member, "name", None),
            "approval_mode": approval_mode,
            "decision": "APPROVED",
            "token_info_keys": sorted([str(k) for k in token_info.keys()]) if isinstance(token_info, dict) else [],
        }

        row = record_verified_identity_for_user(
            guild_id=str(guild.id),
            user_id=str(owner.id),
            identity_fingerprint=fingerprint,
            source=source,
            created_by=str(staff_member.id),
            fingerprint_version=_safe_str((token_info or {}).get("fingerprint_version")) or "v1",
            confidence=100,
            notes=f"Verification approved via {approval_mode} by {staff_member} ({staff_member.id})",
            evidence=evidence,
        )

        proof_id = _safe_str((row or {}).get("id")) or None
        return True, proof_id
    except Exception as e:
        print("⚠️ Failed persisting identity proof on approval:", repr(e))
        return False, str(e)


# ============================================================
# Legacy approval/deny/resubmit fallbacks
# ------------------------------------------------------------
# Only used if verification_new.service import path fails.
# ============================================================

async def _legacy_request_resubmission(
    *,
    guild: discord.Guild,
    channel: discord.TextChannel,
    token: str,
    staff_member: discord.Member,
    owner: Optional[discord.Member],
) -> Dict[str, Any]:
    _ = guild
    try:
        sb_mark_decision(token, "RESUBMIT REQUESTED", int(staff_member.id))
    except Exception:
        pass

    try:
        await post_or_replace_verify_ui(
            channel,
            requester_id=int(owner.id) if isinstance(owner, discord.Member) else None,
            reason=f"resubmit_requested:{staff_member.id}",
            site_url=VERIFY_SITE_URL,
            ttl_minutes=TOKEN_TTL_MINUTES,
            allow_regen=ALLOW_USER_VERIFYLINK,
        )
    except Exception as e:
        return {"ok": False, "message": f"Failed to post resubmission UI: {e}"}

    try:
        # Keep old behavior only in fallback path.
        sb_set_used(token, True)
    except Exception:
        pass

    try:
        if isinstance(owner, discord.Member):
            await channel.send(
                f"🔁 {owner.mention} Please **resubmit** your ID using the new secure upload button above."
            )
        else:
            await channel.send(
                "🔁 Please **resubmit** your ID using the new secure upload button above."
            )
    except Exception:
        pass

    return {
        "ok": True,
        "message": "Resubmission requested.",
    }


async def _legacy_deny_verification(
    *,
    channel: discord.TextChannel,
    token: str,
    staff_member: discord.Member,
    decision_text: str,
) -> Dict[str, Any]:
    _ = channel
    try:
        sb_mark_decision(token, decision_text, int(staff_member.id))
    except Exception:
        pass

    try:
        sb_set_used(token, True)
    except Exception:
        pass

    return {
        "ok": True,
        "message": f"{decision_text} saved.",
    }


async def _legacy_approve_verification(
    *,
    guild: discord.Guild,
    channel: discord.TextChannel,
    token: str,
    staff_member: discord.Member,
    owner: Optional[discord.Member],
    decision_text: str,
) -> Dict[str, Any]:
    _ = channel

    if not isinstance(owner, discord.Member):
        return {
            "ok": False,
            "message": "Approval is blocked because I couldn't detect the ticket owner to grant roles.",
        }

    if _member_looks_already_verified(owner):
        try:
            sb_mark_decision(
                token,
                f"{decision_text} (already verified)",
                int(staff_member.id),
                approved_user_id=int(owner.id),
            )
        except Exception:
            pass
        try:
            sb_set_used(token, True)
        except Exception:
            pass
        return {
            "ok": False,
            "already_verified": True,
            "message": "Duplicate approval blocked: member already appears verified.",
        }

    can_assign, error_msg, roles_to_assign = await check_bot_can_assign_roles(guild)
    if not can_assign:
        try:
            sb_mark_decision(token, f"{decision_text} (roles unavailable)", int(staff_member.id))
        except Exception:
            pass
        return {
            "ok": False,
            "message": f"Cannot assign roles: {error_msg}",
        }

    try:
        grant_roles = [role for role in roles_to_assign if isinstance(role, discord.Role) and role not in owner.roles]
        if grant_roles:
            await owner.add_roles(
                *grant_roles,
                reason=f"Stoney Verify approved by {staff_member} ({staff_member.id})",
            )

        _, remove_error = await _remove_unverified_role_if_present(
            owner,
            reason=f"Stoney Verify approval cleanup by {staff_member} ({staff_member.id})",
        )
        if remove_error:
            try:
                sb_mark_decision(
                    token,
                    f"{decision_text} (unverified cleanup failed)",
                    int(staff_member.id),
                    approved_user_id=int(owner.id),
                )
            except Exception:
                pass
            try:
                sb_set_used(token, True)
            except Exception:
                pass
            return {
                "ok": False,
                "message": f"Roles were added, but removing Unverified failed: {remove_error}",
            }

        try:
            sb_mark_decision(token, decision_text, int(staff_member.id), approved_user_id=int(owner.id))
        except Exception:
            pass
        try:
            sb_set_used(token, True)
        except Exception:
            pass

        role_names = ", ".join(role.name for role in grant_roles) if grant_roles else "no new roles"
        return {
            "ok": True,
            "message": f"Approved. Granted {role_names} to {owner.display_name}.",
        }
    except discord.Forbidden:
        return {
            "ok": False,
            "message": "I can't add roles. Fix my role position + permissions (Manage Roles) and try again.",
        }
    except Exception as e:
        return {
            "ok": False,
            "message": f"Unexpected error: {e}",
        }


# ============================================================
# Persistent-view routing guard
# ------------------------------------------------------------
# transcripts.py now owns sv:ticket:* and sv:verify:staff:* via
# registered persistent Views. This file must not swallow them.
# ============================================================

def _is_persistent_view_managed_custom_id(custom_id: str) -> bool:
    cid = _safe_str(custom_id)
    if not cid:
        return False
    return (
        cid.startswith("sv:ticket:")
        or cid.startswith("sv:verify:staff:")
    )


# ============================================================
# Submission panel flow
# ============================================================

async def handle_possible_submission(message: discord.Message) -> None:
    if not isinstance(message.channel, discord.TextChannel):
        return
    if not is_verification_ticket_channel(message.channel):
        return
    if not message.guild:
        return

    me = message.guild.me
    if not me and bot.user:
        try:
            me = message.guild.get_member(bot.user.id) or await message.guild.fetch_member(bot.user.id)
        except Exception:
            me = None
    if not me:
        return

    perms = message.channel.permissions_for(me)
    if not (perms.view_channel and perms.send_messages):
        return

    if not getattr(message, "webhook_id", None):
        return

    token = extract_token_from_message(message)

    if not token:
        try:
            await asyncio.sleep(1.2)
            fresh = await message.channel.fetch_message(message.id)
            token = extract_token_from_message(fresh)
        except Exception:
            token = None

    if not token:
        return

    token_info = sb_get_token_info(token)
    if not token_info:
        return

    if token_is_expired(token_info):
        return

    ti_channel = str(token_info.get("channel_id") or "")
    if not ti_channel or ti_channel != str(message.channel.id):
        return

    ti_guild = str(token_info.get("guild_id") or "")
    if ti_guild and ti_guild != str(message.guild.id):
        return

    expected_wh_id = None
    webhook_url = token_info.get("webhook_url")
    if webhook_url:
        expected_wh_id = _parse_webhook_id_from_url(str(webhook_url))
        if expected_wh_id and int(getattr(message, "webhook_id", 0) or 0) != int(expected_wh_id):
            return
    else:
        print(f"⚠️ Token {token} has no webhook_url stored – proceeding without webhook check.")

    if token_info.get("used", False):
        return

    if token_info.get("submitted", False):
        return

    sb_set_submitted(token)
    try:
        sb_set_submitted_at(token, now_utc())
    except Exception:
        pass

    _bump_runtime_stat("submissions_seen")
    mark_ticket_activity(message.channel.id)

    try:
        now = now_utc()
        prune_after = max(10, SUBMISSION_DEDUPE_SECONDS * 3)

        for t, ts in list(RECENT_SUBMISSION_TOKENS.items()):
            if (now - ts).total_seconds() > prune_after:
                RECENT_SUBMISSION_TOKENS.pop(t, None)

        for mid, ts in list(RECENT_SUBMISSION_MSG_IDS.items()):
            if (now - ts).total_seconds() > prune_after:
                RECENT_SUBMISSION_MSG_IDS.pop(mid, None)

        if message.id in RECENT_SUBMISSION_MSG_IDS:
            return

        last = RECENT_SUBMISSION_TOKENS.get(token)
        if last and (now - last).total_seconds() < SUBMISSION_DEDUPE_SECONDS:
            return

        RECENT_SUBMISSION_TOKENS[token] = now
        RECENT_SUBMISSION_MSG_IDS[message.id] = now
    except Exception:
        pass

    try:
        _cancel_kick_timer(message.channel.id)
    except Exception:
        pass
    try:
        KICK_TIMER_TASKS.pop(message.channel.id, None)
        KICK_TIMER_STARTS.pop(message.channel.id, None)
        KICK_TIMER_STARTED_BY.pop(message.channel.id, None)
    except Exception:
        pass
    try:
        await kick_timer_persist_delete(int(message.channel.id))
    except Exception:
        pass

    if perms.read_message_history:
        try:
            async for m in message.channel.history(limit=50):
                if not m.author or not bot.user or m.author.id != bot.user.id:
                    continue
                if m.content and "🧾 **Staff Decision Panel**" in m.content:
                    try:
                        await m.delete(reason="Cleanup old staff decision panel (new submission)")
                    except Exception:
                        pass
        except Exception:
            pass

    owner = await find_ticket_owner_retry(message.channel)

    view = discord.ui.View(timeout=None)
    view.add_item(discord.ui.Button(
        label="✅ Approve",
        style=discord.ButtonStyle.success,
        custom_id=make_custom_id("approve", token),
    ))
    view.add_item(discord.ui.Button(
        label="⛔ Deny & Close",
        style=discord.ButtonStyle.danger,
        custom_id=make_custom_id("denyclose", token),
    ))
    view.add_item(discord.ui.Button(
        label="🔁 Request Resubmission",
        style=discord.ButtonStyle.secondary,
        custom_id=make_custom_id("resubmit", token),
    ))

    panel_msg = await message.channel.send(
        f"🧾 **Staff Decision Panel** for {(owner.mention if owner else 'this ticket')}:",
        view=view,
    )
    ACTIVE_DECISION_PANEL_MSG_ID[token] = int(panel_msg.id)
    _bump_runtime_stat("panels_posted")


# ============================================================
# Mod quick actions
# ============================================================

async def _handle_mod_quick_action(
    interaction: discord.Interaction,
    action: str,
    user_id: int,
    extra: str,
) -> bool:
    if not interaction.guild or not isinstance(interaction.user, discord.Member):
        return True

    if not is_staff(interaction.user):
        try:
            if not interaction.response.is_done():
                await interaction.response.send_message("❌ Staff only.", ephemeral=True)
            else:
                await interaction.followup.send("❌ Staff only.", ephemeral=True)
        except Exception:
            pass
        return True

    await _defer_ephemeral(interaction)

    guild = interaction.guild
    target = guild.get_member(int(user_id))
    if not target:
        await interaction.followup.send("❌ User not found (may have left).", ephemeral=True)
        return True

    me = guild.me
    if not me:
        await interaction.followup.send("❌ Bot member missing.", ephemeral=True)
        return True

    try:
        if me.top_role <= target.top_role and not me.guild_permissions.administrator:
            await interaction.followup.send("❌ I can’t act on that member (role hierarchy).", ephemeral=True)
            return True
    except Exception:
        pass

    try:
        if action == "ban":
            if not me.guild_permissions.ban_members:
                await interaction.followup.send("❌ Missing **Ban Members** permission.", ephemeral=True)
                return True
            await guild.ban(
                target,
                reason=f"QuickAction ban by {interaction.user} ({interaction.user.id})",
                delete_message_days=0,
            )
            _bump_runtime_stat("mod_actions")
            await interaction.followup.send(f"🔨 Banned {target.mention}.", ephemeral=True)
            return True

        if action == "kick":
            if not me.guild_permissions.kick_members:
                await interaction.followup.send("❌ Missing **Kick Members** permission.", ephemeral=True)
                return True
            await guild.kick(
                target,
                reason=f"QuickAction kick by {interaction.user} ({interaction.user.id})",
            )
            _bump_runtime_stat("mod_actions")
            await interaction.followup.send(f"👢 Kicked {target.mention}.", ephemeral=True)
            return True

        if action == "timeout":
            if not me.guild_permissions.moderate_members:
                await interaction.followup.send("❌ Missing **Moderate Members** permission.", ephemeral=True)
                return True
            mins = MOD_TIMEOUT_MINUTES
            try:
                if extra.startswith("m="):
                    mins = int(extra.replace("m=", "").strip())
            except Exception:
                mins = MOD_TIMEOUT_MINUTES
            until = now_utc() + timedelta(minutes=max(1, mins))
            await target.timeout(
                until,
                reason=f"QuickAction timeout by {interaction.user} ({interaction.user.id})",
            )
            _bump_runtime_stat("mod_actions")
            await interaction.followup.send(
                f"⏳ Timed out {target.mention} for {mins} minutes.",
                ephemeral=True,
            )
            return True

        await interaction.followup.send("❌ Unknown mod action.", ephemeral=True)
        return True
    except discord.Forbidden:
        await interaction.followup.send("❌ Forbidden (permissions/hierarchy).", ephemeral=True)
        return True
    except discord.HTTPException as e:
        await interaction.followup.send(f"❌ Discord API error: {e}", ephemeral=True)
        return True
    except Exception as e:
        await interaction.followup.send(f"❌ Unexpected error: {e}", ephemeral=True)
        return True


# ============================================================
# Standard ticket decision flow
# ============================================================

async def _handle_standard_staff_decision(
    interaction: discord.Interaction,
    *,
    action: str,
    token: str,
    guild: discord.Guild,
    channel: discord.TextChannel,
    owner: Optional[discord.Member],
) -> bool:
    if action not in {"approve", "denyclose", "resubmit"}:
        return False

    if not isinstance(interaction.user, discord.Member) or not is_staff(interaction.user):
        await interaction.followup.send("❌ Staff only.", ephemeral=True)
        return True

    if not _safe_str(token):
        await interaction.followup.send("❌ Missing decision token.", ephemeral=True)
        return True

    try:
        bound_id = ACTIVE_DECISION_PANEL_MSG_ID.get(token)
        if bound_id and interaction.message and int(interaction.message.id) != int(bound_id):
            await interaction.followup.send(
                "❌ That decision panel is stale. Use the latest panel.",
                ephemeral=True,
            )
            return True
    except Exception:
        pass

    token_info = sb_get_token_info(token)
    if not token_info:
        await interaction.followup.send("❌ Invalid or expired token.", ephemeral=True)
        return True

    if token_is_expired(token_info):
        await interaction.followup.send("❌ This token expired. Generate a new link.", ephemeral=True)
        return True

    if token_info.get("used", False):
        await interaction.followup.send("❌ This decision token was already handled. Use the latest panel.", ephemeral=True)
        return True

    if str(token_info.get("channel_id") or "") != str(channel.id):
        await interaction.followup.send(
            "❌ That decision token doesn’t belong to this ticket.",
            ephemeral=True,
        )
        return True

    ti_guild = str(token_info.get("guild_id") or "")
    if ti_guild and ti_guild != str(guild.id):
        await interaction.followup.send(
            "❌ That decision token doesn’t belong to this server.",
            ephemeral=True,
        )
        return True

    if owner is None:
        owner = await _resolve_owner_from_token_or_ticket(
            guild=guild,
            channel=channel,
            token_info=token_info,
        )

    async with _interaction_action_lock(action, token, channel.id):
        token_info_live = sb_get_token_info(token)
        if not token_info_live or token_info_live.get("used", False):
            await interaction.followup.send(
                "❌ This decision token was already handled. Use the latest panel.",
                ephemeral=True,
            )
            return True

        if action == "resubmit":
            result = await request_resubmission(
                guild=guild,
                channel=channel,
                token=token,
                staff_member=interaction.user,
                owner=owner if isinstance(owner, discord.Member) else None,
                prompt_in_channel=True,
            )

            if _service_missing_result(result):
                result = await _legacy_request_resubmission(
                    guild=guild,
                    channel=channel,
                    token=token,
                    staff_member=interaction.user,
                    owner=owner if isinstance(owner, discord.Member) else None,
                )

            if not result.get("ok"):
                await interaction.followup.send(
                    f"❌ {result.get('message') or 'Failed to request resubmission.'}",
                    ephemeral=True,
                )
                return True

            ACTIVE_DECISION_PANEL_MSG_ID.pop(token, None)

            await _disable_interaction_message_if_possible(
                interaction,
                content_suffix=f"🔁 Resubmission requested by {interaction.user.mention}.",
            )

            _bump_runtime_stat("resubmit")
            await interaction.followup.send(
                "✅ Resubmission requested. A new link was posted and the ticket stays open.",
                ephemeral=True,
            )
            return True

        if action == "denyclose":
            result = await deny_verification(
                guild=guild,
                channel=channel,
                token=token,
                staff_member=interaction.user,
                decision_text="DENIED",
                close_after=False,
            )

            if _service_missing_result(result):
                result = await _legacy_deny_verification(
                    channel=channel,
                    token=token,
                    staff_member=interaction.user,
                    decision_text="DENIED",
                )

            if not result.get("ok"):
                await interaction.followup.send(
                    f"❌ {result.get('message') or 'Denial failed.'}",
                    ephemeral=True,
                )
                return True

            ACTIVE_DECISION_PANEL_MSG_ID.pop(token, None)

            await _disable_interaction_message_if_possible(
                interaction,
                content_suffix=f"⛔ Denied by {interaction.user.mention}.",
            )

            _bump_runtime_stat("denied")
            await interaction.followup.send(
                "⛔ **Denied**.",
                ephemeral=True,
            )
            await auto_close_after_decision(channel, closer=interaction.user, decision="DENIED")
            return True

        result = await approve_verification(
            guild=guild,
            channel=channel,
            token=token,
            staff_member=interaction.user,
            decision_text="APPROVED",
            close_after=False,
            owner=owner if isinstance(owner, discord.Member) else None,
        )

        if _service_missing_result(result):
            result = await _legacy_approve_verification(
                guild=guild,
                channel=channel,
                token=token,
                staff_member=interaction.user,
                owner=owner if isinstance(owner, discord.Member) else None,
                decision_text="APPROVED",
            )

        if not result.get("ok"):
            if result.get("already_verified"):
                ACTIVE_DECISION_PANEL_MSG_ID.pop(token, None)
                await _disable_interaction_message_if_possible(
                    interaction,
                    content_suffix="✅ Member already appears verified. Panel closed.",
                )
                await interaction.followup.send(
                    "✅ This member already appears verified. Duplicate approval was blocked.",
                    ephemeral=True,
                )
                return True

            await interaction.followup.send(
                f"❌ {result.get('message') or 'Approval failed.'}",
                ephemeral=True,
            )
            return True

        token_info_live = sb_get_token_info(token)
        proof_saved = False
        proof_meta: Optional[str] = None
        if isinstance(owner, discord.Member):
            proof_saved, proof_meta = await _persist_identity_proof_on_approval(
                guild=guild,
                owner=owner,
                token=token,
                token_info=token_info_live,
                staff_member=interaction.user,
                channel=channel,
                approval_mode="standard",
            )

        ACTIVE_DECISION_PANEL_MSG_ID.pop(token, None)

        await _disable_interaction_message_if_possible(
            interaction,
            content_suffix=f"✅ Approved by {interaction.user.mention}.",
        )

        _bump_runtime_stat("approved")

        msg = str(result.get("message") or "✅ Approved.")
        if proof_saved and proof_meta:
            msg += f"\n🧬 Stored hard identity proof (`{proof_meta}`) for future confirmed-duplicate checks."
        elif proof_saved:
            msg += "\n🧬 Stored hard identity proof for future confirmed-duplicate checks."

        await interaction.followup.send(msg, ephemeral=True)
        await auto_close_after_decision(channel, closer=interaction.user, decision="APPROVED")
        return True


# ============================================================
# VC start runtime flow
# ------------------------------------------------------------
# This remains here because it is primarily a live Discord runtime
# action (unlock VC, move owner, swap the active panel) rather than
# a DB-only lifecycle decision.
# ============================================================

async def _handle_vc_start_runtime(
    interaction: discord.Interaction,
    *,
    token: str,
    guild: discord.Guild,
    ticket_ch: discord.TextChannel,
    owner: Optional[discord.Member],
) -> bool:
    if not isinstance(interaction.user, discord.Member):
        await interaction.followup.send("❌ Staff member could not be resolved.", ephemeral=True)
        return True

    if not isinstance(owner, discord.Member):
        await interaction.followup.send(
            "❌ Could not detect the ticket owner for this VC session.",
            ephemeral=True,
        )
        return True

    vc_ch = _get_vc_channel(guild)
    if not vc_ch:
        await interaction.followup.send("❌ VC verification channel not found.", ephemeral=True)
        return True

    me = guild.me
    if not me:
        await interaction.followup.send("❌ Bot member missing.", ephemeral=True)
        return True

    ok, perm_msg = _can_manage_channel(me, vc_ch)
    if not ok:
        await interaction.followup.send(
            f"❌ Bot lacks required permissions: {perm_msg}",
            ephemeral=True,
        )
        return True

    lock_ok, lock_msg = await _vc_lock_channel_for_session(
        guild,
        owner,
        interaction.user,
        token,
    )
    if not lock_ok:
        await interaction.followup.send(
            f"❌ Failed to start VC session: {lock_msg}",
            ephemeral=True,
        )
        return True

    _set_vc_request_cache(
        token,
        {
            "status": "STARTED",
            "started_by": str(interaction.user.id),
            "started_at": now_utc().isoformat(),
            "accepted_by": int(_vc_request_cache(token).get("accepted_by") or interaction.user.id),
            "accepted_staff_id": int(_vc_request_cache(token).get("accepted_staff_id") or interaction.user.id),
            "assigned_staff_id": int(_vc_request_cache(token).get("assigned_staff_id") or interaction.user.id),
            "owner_id": int(owner.id),
            "requester_id": int(owner.id),
            "requested_by": int(_vc_request_cache(token).get("requested_by") or owner.id),
            "ticket_channel_id": int(getattr(ticket_ch, "id", 0) or 0),
            "guild_id": int(guild.id),
            "vc_channel_id": int(_configured_vc_channel_id() or 0),
            "access_minutes": int(_configured_vc_access_minutes()),
        },
    )

    view = None
    try:
        view = discord.ui.View(timeout=1800)
        view.add_item(discord.ui.Button(
            label="🎙️ Join ID-Verify VC",
            style=discord.ButtonStyle.link,
            url=_discord_channel_url(guild.id, int(vc_ch.id)),
        ))
    except Exception:
        view = None

    move_result = ""
    try:
        moved, move_msg = await vc_move_member_into_verify_vc(
            guild=guild,
            member=owner,
        )
        if moved:
            move_result = f"\n✅ {move_msg}"
    except Exception:
        move_result = ""

    access_min = int(_configured_vc_access_minutes())

    try:
        await _send_or_edit_ticket_vc_status(
            ticket_channel=ticket_ch,
            owner=owner,
            content_msg=(
                f"🎙️ **VC session started** by {interaction.user.mention}\n\n"
                f"{owner.mention} tap below to join <#{int(vc_ch.id)}> now.\n"
                f"⏳ Temporary access expires in ~{access_min} minutes."
                f"{move_result}"
            ),
            view=view,
        )
    except Exception:
        pass

    try:
        approve_view = discord.ui.View(timeout=None)
        approve_view.add_item(discord.ui.Button(
            label="✅ Approve (VC)",
            style=discord.ButtonStyle.success,
            custom_id=make_custom_id("vc_approve", token),
        ))
        approve_view.add_item(discord.ui.Button(
            label="⛔ Deny & Close (VC)",
            style=discord.ButtonStyle.danger,
            custom_id=make_custom_id("vc_denyclose", token),
        ))
        approve_view.add_item(discord.ui.Button(
            label="🧹 End VC Session",
            style=discord.ButtonStyle.secondary,
            custom_id=make_custom_id("vc_end", token),
        ))

        if interaction.message:
            await interaction.message.edit(
                content=f"▶️ VC session started by {interaction.user.mention} for {owner.mention}.",
                view=approve_view,
            )
            _set_vc_request_cache(token, {"ticket_panel_msg_id": int(interaction.message.id)})
    except Exception:
        pass

    _bump_runtime_stat("vc_started")
    await interaction.followup.send(
        "▶️ VC session started. Owner + assigned staff now have VC access.",
        ephemeral=True,
    )
    return True


# ============================================================
# VC button routing
# ============================================================

async def _handle_vc_staff_action(
    interaction: discord.Interaction,
    *,
    action: str,
    token: str,
    guild: discord.Guild,
    token_info_q: Dict[str, Any],
) -> bool:
    if not isinstance(interaction.user, discord.Member) or not is_staff(interaction.user):
        await interaction.followup.send("❌ Staff only.", ephemeral=True)
        return True

    expired = token_is_expired(token_info_q)
    current_status = _vc_request_status(token)
    assigned_staff_id = _vc_request_assigned_staff_id(token)
    actor_is_elevated = _member_is_elevated_staff(interaction.user)

    if not _vc_interaction_message_is_current(interaction, token):
        await interaction.followup.send(
            "❌ Those VC controls are stale. Use the latest VC panel.",
            ephemeral=True,
        )
        return True

    if expired and action not in {"vc_reissue", "vc_cancel"}:
        await interaction.followup.send(
            "❌ This VC request token expired.\nUse **Reissue Token** or `/vc_reissue`.",
            ephemeral=True,
        )
        return True

    if current_status in VC_TERMINAL_STATUSES and action not in {"vc_reissue"}:
        await interaction.followup.send(
            f"❌ This VC request is already finished (`{current_status}`). Use the latest controls.",
            ephemeral=True,
        )
        return True

    if current_status and not _vc_action_allowed_for_status(action, current_status):
        await interaction.followup.send(
            _vc_action_status_error(action, current_status),
            ephemeral=True,
        )
        return True

    ticket_ch = await _resolve_ticket_channel_from_token_info_safe(guild, token_info_q)
    if not ticket_ch:
        await interaction.followup.send(
            "❌ Could not resolve the ticket channel for this VC request.",
            ephemeral=True,
        )
        return True

    owner = await _resolve_owner_from_token_or_ticket(
        guild=guild,
        channel=ticket_ch,
        token_info=token_info_q,
    )

    async with _interaction_action_lock(action, token, ticket_ch.id):
        token_info_live = sb_get_token_info(token) or token_info_q
        current_status = _vc_request_status(token)
        assigned_staff_id = _vc_request_assigned_staff_id(token)

        if token_info_live.get("used", False) and action not in {"vc_reissue"}:
            await interaction.followup.send(
                "❌ This VC token was already finalized. Use the latest controls.",
                ephemeral=True,
            )
            return True

        if current_status in VC_TERMINAL_STATUSES and action not in {"vc_reissue"}:
            await interaction.followup.send(
                f"❌ This VC request is already finished (`{current_status}`).",
                ephemeral=True,
            )
            return True

        if current_status and not _vc_action_allowed_for_status(action, current_status):
            await interaction.followup.send(
                _vc_action_status_error(action, current_status),
                ephemeral=True,
            )
            return True

        if (
            assigned_staff_id > 0
            and assigned_staff_id != int(interaction.user.id)
            and action in {"vc_start", "vc_upload", "vc_approve", "vc_denyclose", "vc_end", "vc_complete", "vc_cancel"}
            and not actor_is_elevated
        ):
            await interaction.followup.send(
                "❌ Another staff member already owns this VC request. Use `/vc_takeover` first.",
                ephemeral=True,
            )
            return True

        if action == "vc_start":
            return await _handle_vc_start_runtime(
                interaction,
                token=token,
                guild=guild,
                ticket_ch=ticket_ch,
                owner=owner,
            )

        if action == "vc_accept":
            result = await _voice_verify_accept_vc_request(
                guild=guild,
                token=token,
                staff_member=interaction.user,
                queue_message=interaction.message if isinstance(interaction.message, discord.Message) else None,
            )
            await _send_followup_result(interaction, result)
            return True

        if action == "vc_upload":
            result = await _voice_verify_request_upload_instead(
                guild=guild,
                token=token,
                staff_member=interaction.user,
                queue_message=interaction.message if isinstance(interaction.message, discord.Message) else None,
            )
            if result.get("ok"):
                try:
                    await _disable_interaction_message_if_possible(
                        interaction,
                        content_suffix=f"🔁 Upload requested by {interaction.user.mention}.",
                    )
                except Exception:
                    pass
            await _send_followup_result(interaction, result)
            return True

        if action == "vc_reissue":
            result = await _voice_verify_reissue_vc_token(
                guild=guild,
                token=token,
                staff_member=interaction.user,
                queue_message=interaction.message if isinstance(interaction.message, discord.Message) else None,
            )
            if result.get("ok"):
                old_token = _safe_str(result.get("old_token") or token)
                new_token = _safe_str(result.get("token"))
                ticket_obj = result.get("channel")
                parts = [
                    "✅ VC token reissued.",
                    f"Old: `{old_token}`",
                    f"New: `{new_token}`",
                ]
                if isinstance(ticket_obj, discord.TextChannel):
                    parts.append(f"Ticket: {ticket_obj.mention}")
                await interaction.followup.send("\n".join(parts), ephemeral=True)
            else:
                await _send_followup_result(interaction, result)
            return True

        if action == "vc_approve":
            result = await _voice_verify_approve_vc_request(
                guild=guild,
                token=token,
                staff_member=interaction.user,
            )

            if result.get("ok"):
                token_info_after = sb_get_token_info(token)
                proof_saved = False
                proof_meta: Optional[str] = None
                if isinstance(owner, discord.Member):
                    proof_saved, proof_meta = await _persist_identity_proof_on_approval(
                        guild=guild,
                        owner=owner,
                        token=token,
                        token_info=token_info_after,
                        staff_member=interaction.user,
                        channel=ticket_ch,
                        approval_mode="vc",
                    )

                try:
                    await _disable_interaction_message_if_possible(
                        interaction,
                        content_suffix=f"✅ VC approved by {interaction.user.mention}.",
                    )
                except Exception:
                    pass

                msg = str(result.get("message") or "Approved (VC).")
                if proof_saved and proof_meta:
                    msg += f"\n🧬 Stored hard identity proof (`{proof_meta}`) for future confirmed-duplicate checks."
                elif proof_saved:
                    msg += "\n🧬 Stored hard identity proof for future confirmed-duplicate checks."

                _bump_runtime_stat("vc_approved")
                await interaction.followup.send(f"✅ {msg}", ephemeral=True)
            else:
                if result.get("already_verified"):
                    try:
                        await _disable_interaction_message_if_possible(
                            interaction,
                            content_suffix="✅ Member already appears verified. Duplicate VC approval blocked.",
                        )
                    except Exception:
                        pass
                await _send_followup_result(interaction, result)
            return True

        if action == "vc_denyclose":
            result = await _voice_verify_deny_vc_request(
                guild=guild,
                token=token,
                staff_member=interaction.user,
            )
            if result.get("ok"):
                try:
                    await _disable_interaction_message_if_possible(
                        interaction,
                        content_suffix=f"⛔ VC denied by {interaction.user.mention}.",
                    )
                except Exception:
                    pass
                _bump_runtime_stat("vc_denied")
            await _send_followup_result(interaction, result)
            return True

        if action in {"vc_end", "vc_complete", "vc_cancel"}:
            result = await _voice_verify_end_vc_session(
                guild=guild,
                token=token,
                staff_member=interaction.user,
                reason=action,
            )
            if result.get("ok"):
                try:
                    await _disable_interaction_message_if_possible(
                        interaction,
                        content_suffix=f"🧹 VC session ended by {interaction.user.mention}.",
                    )
                except Exception:
                    pass
                _bump_runtime_stat("vc_ended")
            await _send_followup_result(interaction, result)
            return True

    return False


# ============================================================
# Main interaction router
# ============================================================

def _known_component_action(action: str) -> bool:
    return action in {
        "approve",
        "denyclose",
        "resubmit",
        "vc_accept",
        "vc_start",
        "vc_complete",
        "vc_cancel",
        "vc_upload",
        "vc_reissue",
        "vc_end",
        "vc_approve",
        "vc_denyclose",
        "sv:verify:get",
        "sv:verify:raw",
        "sv:verify:regen",
        "sv:verify:vc",
        "sv:verify:reissue",
        "verify:get_upload",
        "verify:vc",
        "verify:reveal_raw",
        "verify:regen",
    }


async def handle_component_interaction(interaction: discord.Interaction) -> None:
    if interaction.type != discord.InteractionType.component:
        return

    data = interaction.data or {}
    custom_id = (data.get("custom_id", "") or "").strip()

    try:
        if isinstance(interaction.channel, discord.TextChannel):
            mark_ticket_activity(interaction.channel.id)
    except Exception:
        pass

    try:
        handled = await maybe_handle_verify_ui_interaction(interaction, site_url=SITE_URL)
        if handled:
            return
    except Exception:
        pass

    if _is_persistent_view_managed_custom_id(custom_id):
        return

    try:
        m_action, m_uid, m_extra = parse_mod_id(custom_id)
    except Exception:
        m_action, m_uid, m_extra = (None, None, "")

    if m_action and m_uid and interaction.guild and isinstance(interaction.user, discord.Member):
        handled = await _handle_mod_quick_action(interaction, m_action, int(m_uid), m_extra)
        if handled:
            return

    action, token = parse_custom_id(custom_id)
    if not action:
        return

    if not _known_component_action(action):
        return

    await _defer_ephemeral(interaction)

    guild = interaction.guild
    if not guild:
        await interaction.followup.send("❌ Invalid context (no guild).", ephemeral=True)
        return

    if action in VC_STAFF_ACTIONS:
        if not isinstance(interaction.user, discord.Member) or not is_staff(interaction.user):
            await interaction.followup.send("❌ Staff only.", ephemeral=True)
            return

        if not token:
            await interaction.followup.send("❌ Missing decision token.", ephemeral=True)
            return

        token_info_q = sb_get_token_info(token)
        if not token_info_q:
            await interaction.followup.send("❌ Invalid or expired token.", ephemeral=True)
            return

        handled = await _handle_vc_staff_action(
            interaction,
            action=action,
            token=token,
            guild=guild,
            token_info_q=token_info_q,
        )
        if handled:
            return

    channel = interaction.channel
    if not isinstance(channel, discord.TextChannel):
        await interaction.followup.send("❌ Invalid context.", ephemeral=True)
        return

    if action not in VC_STAFF_ACTIONS and not is_verification_ticket_channel(channel):
        await interaction.followup.send("❌ Not a verification ticket channel.", ephemeral=True)
        return

    try:
        if interaction.message and bot.user and interaction.message.author and interaction.message.author.id != bot.user.id:
            await interaction.followup.send("❌ Invalid interaction source.", ephemeral=True)
            return
    except Exception:
        pass

    owner = await find_ticket_owner_retry(channel)

    if action in {"approve", "denyclose", "resubmit"}:
        handled = await _handle_standard_staff_decision(
            interaction,
            action=action,
            token=token or "",
            guild=guild,
            channel=channel,
            owner=owner,
        )
        if handled:
            return

    return


# ============================================================
# Registration
# ============================================================

def register_interaction_handlers(bot_instance: Any) -> None:
    global _INTERACTION_HANDLERS_REGISTERED

    if _INTERACTION_HANDLERS_REGISTERED:
        try:
            print("ℹ️ interaction_handlers already registered; skipping duplicate registration.")
        except Exception:
            pass
        return

    @bot_instance.event
    async def on_interaction(interaction: discord.Interaction):
        await handle_component_interaction(interaction)

    _INTERACTION_HANDLERS_REGISTERED = True

    try:
        print("✅ interaction_handlers: registered component interaction handler")
    except Exception:
        pass


__all__ = [
    "handle_possible_submission",
    "handle_component_interaction",
    "register_interaction_handlers",
    "_remove_unverified_role_if_present",
]
