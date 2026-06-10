from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import discord

from ..globals import *  # noqa: F401,F403

from ..tickets import (
    find_ticket_owner_retry,
    is_verification_ticket_channel,
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
    from ..verify_ui import post_or_replace_verify_ui
except Exception:
    async def post_or_replace_verify_ui(*args, **kwargs) -> Optional[str]:  # type: ignore
        return None


try:
    from ..tickets_new.repository import (
        safe_optional_update_by_channel_id,
        get_ticket_by_any_channel_id,
    )
except Exception:
    async def safe_optional_update_by_channel_id(*args, **kwargs) -> bool:  # type: ignore
        return False

    async def get_ticket_by_any_channel_id(*args, **kwargs) -> Optional[Dict[str, Any]]:  # type: ignore
        return None


_VERIFICATION_ACTION_LOCKS: Dict[str, asyncio.Lock] = {}


# ============================================================
# Lazy transcript imports
# ------------------------------------------------------------
# IMPORTANT:
# Do NOT import ..transcripts at module load time.
# That created a circular import:
# verification_new.service -> transcripts.py -> verification_new.service
#
# So these helpers import only when needed.
# ============================================================

async def _transcripts_auto_close_after_decision(
    channel: Optional[discord.TextChannel],
    *,
    closer: discord.Member,
    decision: str,
) -> None:
    if not isinstance(channel, discord.TextChannel):
        return
    try:
        from ..transcripts import auto_close_after_decision as _auto_close_after_decision
        await _auto_close_after_decision(
            channel,
            closer=closer,
            decision=decision,
        )
    except Exception:
        return


async def _transcripts_check_bot_can_assign_roles(
    guild: discord.Guild,
) -> Tuple[bool, str, List[discord.Role]]:
    try:
        from ..transcripts import check_bot_can_assign_roles as _check_bot_can_assign_roles
        return await _check_bot_can_assign_roles(guild)
    except Exception as e:
        return False, f"transcripts import failed: {e}", []


async def _transcripts_ensure_verify_ui_present(
    channel: discord.TextChannel,
    *,
    reason: str,
) -> bool:
    try:
        from ..transcripts import ensure_verify_ui_present as _ensure_verify_ui_present
        return await _ensure_verify_ui_present(channel, reason=reason)
    except Exception:
        return False


# ============================================================
# Datetime / expiration helpers
# ============================================================

def _utc_now() -> datetime:
    try:
        return now_utc()
    except Exception:
        return datetime.now(timezone.utc)


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


try:
    from ..commands_ext.common import token_is_expired
except Exception:
    def token_is_expired(token_info: Optional[Dict[str, Any]]) -> bool:  # type: ignore
        try:
            if not token_info:
                return True

            raw = token_info.get("expires_at")
            if not raw:
                return False

            parsed = _parse_iso_datetime(str(raw))
            if parsed is None:
                return False

            return parsed <= _utc_now()
        except Exception:
            return False


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


def _role_by_id(guild: discord.Guild, role_id: int) -> Optional[discord.Role]:
    try:
        if not guild or not role_id or int(role_id) <= 0:
            return None
        role = guild.get_role(int(role_id))
        return role if isinstance(role, discord.Role) else None
    except Exception:
        return None


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


def _unique_roles(roles: List[discord.Role]) -> List[discord.Role]:
    out: List[discord.Role] = []
    seen: set[int] = set()

    for role in roles or []:
        try:
            if not isinstance(role, discord.Role):
                continue
            rid = int(role.id)
            if rid in seen:
                continue
            seen.add(rid)
            out.append(role)
        except Exception:
            continue

    return out


def _roles_display(roles: List[discord.Role]) -> str:
    names = [r.name for r in roles if isinstance(r, discord.Role)]
    return ", ".join(names) if names else "no new roles"


def _decision_kind(decision_text: str) -> str:
    text = _safe_str(decision_text).upper()
    if "DENIED" in text:
        return "denied"
    if "RESUBMIT" in text:
        return "resubmit"
    return "approved"


def _verification_source_from_decision(decision_text: str) -> str:
    text = _safe_str(decision_text).upper()
    if "VC" in text and "DENIED" in text:
        return "vc_staff_denial"
    if "VC" in text and "APPROVED" in text:
        return "vc_staff_approval"
    if "DENIED" in text:
        return "ticket_staff_denial"
    if "RESUBMIT" in text:
        return "ticket_resubmit_requested"
    return "ticket_staff_approval"


def _entry_method_from_token_info(
    token_info: Optional[Dict[str, Any]],
    fallback: str = "manual_verification",
) -> str:
    if not isinstance(token_info, dict):
        return fallback

    for key in ("entry_method", "join_source", "verification_source"):
        value = _safe_str(token_info.get(key))
        if value:
            return value

    return fallback


def _member_display_name(member: Optional[discord.Member]) -> Optional[str]:
    try:
        if not isinstance(member, discord.Member):
            return None
        return str(member.display_name or member.name or member)
    except Exception:
        return None


def _ticket_channel_id_value(channel: Optional[discord.TextChannel]) -> Optional[str]:
    try:
        if isinstance(channel, discord.TextChannel):
            return str(channel.id)
    except Exception:
        pass
    return None


def _action_lock_key(
    *,
    guild: discord.Guild,
    token: Optional[str],
    channel: Optional[discord.TextChannel],
    owner: Optional[discord.Member],
    action: str,
) -> str:
    if _safe_str(token):
        return f"{guild.id}:token:{_safe_str(token)}:{action}"
    if isinstance(channel, discord.TextChannel):
        return f"{guild.id}:channel:{channel.id}:{action}"
    if isinstance(owner, discord.Member):
        return f"{guild.id}:owner:{owner.id}:{action}"
    return f"{guild.id}:fallback:{action}"


def _action_lock(
    *,
    guild: discord.Guild,
    token: Optional[str],
    channel: Optional[discord.TextChannel],
    owner: Optional[discord.Member],
    action: str,
) -> asyncio.Lock:
    key = _action_lock_key(
        guild=guild,
        token=token,
        channel=channel,
        owner=owner,
        action=action,
    )
    lock = _VERIFICATION_ACTION_LOCKS.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _VERIFICATION_ACTION_LOCKS[key] = lock
    return lock


async def _refresh_token_info(token: str) -> Optional[Dict[str, Any]]:
    try:
        tok = _safe_str(token)
        if not tok:
            return None
        return sb_get_token_info(tok)
    except Exception:
        return None


def _sync_member_verification_context(
    *,
    guild: discord.Guild,
    owner: Optional[discord.Member],
    channel: Optional[discord.TextChannel],
    token_info: Optional[Dict[str, Any]],
    staff_member: discord.Member,
    decision_text: str,
) -> None:
    try:
        if not isinstance(owner, discord.Member):
            return

        sb = get_supabase()
        if not sb:
            return

        guild_id = str(guild.id)
        user_id = str(owner.id)
        now_iso = _utc_now().isoformat()
        decision_kind = _decision_kind(decision_text)
        verification_source = _verification_source_from_decision(decision_text)
        entry_method = _entry_method_from_token_info(token_info)
        ticket_channel_id = _ticket_channel_id_value(channel)
        staff_id = str(staff_member.id)
        staff_name = _member_display_name(staff_member) or str(staff_member)

        member_patch: Dict[str, Any] = {
            "approved_by": staff_id,
            "approved_by_name": staff_name,
            "approval_reason": str(decision_text),
            "verification_source": verification_source,
            "entry_method": entry_method,
            "updated_at": now_iso,
            "synced_at": now_iso,
            "source_ticket_id": ticket_channel_id,
            "verification_ticket_id": ticket_channel_id,
        }

        if isinstance(token_info, dict):
            invite_code = _safe_str(token_info.get("invite_code"))
            invited_by = _safe_str(token_info.get("invited_by"))
            invited_by_name = _safe_str(token_info.get("invited_by_name"))
            vouched_by = _safe_str(token_info.get("vouched_by"))
            vouched_by_name = _safe_str(token_info.get("vouched_by_name"))

            if invite_code:
                member_patch["invite_code"] = invite_code
            if invited_by:
                member_patch["invited_by"] = invited_by
            if invited_by_name:
                member_patch["invited_by_name"] = invited_by_name
            if vouched_by:
                member_patch["vouched_by"] = vouched_by
            if vouched_by_name:
                member_patch["vouched_by_name"] = vouched_by_name

        if decision_kind == "denied":
            member_patch["has_verified_role"] = False
            member_patch["role_state"] = "unverified_only"
            member_patch["role_state_reason"] = "Verification denied."
        elif decision_kind == "approved":
            member_patch["has_verified_role"] = True
            member_patch["has_unverified"] = False
            member_patch["in_guild"] = True
            member_patch["role_state"] = "verified_ok"
            member_patch["role_state_reason"] = "Verification approved by staff."
        elif decision_kind == "resubmit":
            member_patch["role_state_reason"] = "Staff requested resubmission."

        try:
            (
                sb.table("guild_members")
                .update(member_patch)
                .eq("guild_id", guild_id)
                .eq("user_id", user_id)
                .execute()
            )
        except Exception:
            pass

        latest_join_row: Optional[Dict[str, Any]] = None
        try:
            join_res = (
                sb.table("member_joins")
                .select("id")
                .eq("guild_id", guild_id)
                .eq("user_id", user_id)
                .order("joined_at", desc=True)
                .limit(1)
                .execute()
            )
            join_rows = getattr(join_res, "data", None) or []
            if join_rows:
                latest_join_row = dict(join_rows[0])
        except Exception:
            latest_join_row = None

        join_patch: Dict[str, Any] = {
            "approved_by": staff_id,
            "approved_by_name": staff_name,
            "join_note": str(decision_text),
            "entry_method": entry_method,
            "verification_source": verification_source,
            "source_ticket_id": ticket_channel_id,
        }

        if latest_join_row and latest_join_row.get("id") is not None:
            try:
                (
                    sb.table("member_joins")
                    .update(join_patch)
                    .eq("id", latest_join_row.get("id"))
                    .execute()
                )
            except Exception:
                pass

        event_type = {
            "approved": "verification_approved",
            "denied": "verification_denied",
            "resubmit": "verification_resubmit_requested",
        }.get(decision_kind, "verification_updated")

        title = {
            "approved": "Verification Approved",
            "denied": "Verification Denied",
            "resubmit": "Verification Resubmission Requested",
        }.get(decision_kind, "Verification Updated")

        metadata: Dict[str, Any] = {
            "decision": str(decision_text),
            "decision_kind": decision_kind,
            "verification_source": verification_source,
            "entry_method": entry_method,
            "source_ticket_id": ticket_channel_id,
            "channel_id": ticket_channel_id,
            "channel_name": channel.name if isinstance(channel, discord.TextChannel) else None,
        }

        try:
            (
                sb.table("member_events")
                .insert(
                    {
                        "guild_id": guild_id,
                        "user_id": user_id,
                        "actor_id": staff_id,
                        "actor_name": staff_name,
                        "event_type": event_type,
                        "title": title,
                        "reason": str(decision_text),
                        "metadata": metadata,
                        "created_at": now_iso,
                    }
                )
                .execute()
            )
        except Exception:
            pass
    except Exception:
        pass


def _ui_result_is_success(value: Any) -> bool:
    try:
        if value is True:
            return True
        text = _safe_str(value).lower()
        return text in {"posted", "updated", "ok", "success", "true"}
    except Exception:
        return False


async def _mark_decision_safe(
    token: str,
    decision: str,
    staff_id: int,
    approved_user_id: Optional[int] = None,
) -> None:
    if not _safe_str(token):
        return
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
    if not _safe_str(token):
        return
    try:
        sb_set_used(token, used)
    except Exception:
        pass


async def _auto_close_after_decision_safe(
    channel: Optional[discord.TextChannel],
    closer: discord.Member,
    decision: str,
) -> None:
    if not isinstance(channel, discord.TextChannel):
        return

    try:
        await _transcripts_auto_close_after_decision(
            channel,
            closer=closer,
            decision=decision,
        )
    except Exception:
        pass


async def _update_ticket_decision_metadata(
    *,
    channel: Optional[discord.TextChannel],
    decision: str,
    staff_member: discord.Member,
    owner: Optional[discord.Member] = None,
) -> None:
    _ = staff_member
    if not isinstance(channel, discord.TextChannel):
        return

    try:
        payload: Dict[str, Any] = {
            "decision": decision,
            "closed_reason": decision,
        }
        if isinstance(owner, discord.Member):
            payload["user_id"] = str(owner.id)
            payload["username"] = str(owner)
        await safe_optional_update_by_channel_id(channel.id, payload)
    except Exception:
        pass


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


def _member_has_any_role(member: Optional[discord.Member], role_ids: List[int]) -> bool:
    try:
        if not isinstance(member, discord.Member):
            return False
        wanted = {int(r) for r in role_ids if int(r) > 0}
        if not wanted:
            return False
        return any(int(getattr(role, "id", 0) or 0) in wanted for role in (member.roles or []))
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


def _token_belongs_to_guild(token_info: Dict[str, Any], guild: discord.Guild) -> bool:
    try:
        token_guild = str(token_info.get("guild_id") or "").strip()
        if not token_guild:
            return True
        return token_guild == str(guild.id)
    except Exception:
        return False


def _token_belongs_to_channel(
    token_info: Dict[str, Any],
    channel: Optional[discord.TextChannel],
) -> bool:
    try:
        token_channel = str(token_info.get("channel_id") or "").strip()

        if not isinstance(channel, discord.TextChannel):
            return True

        if not token_channel:
            return False

        return token_channel == str(channel.id)
    except Exception:
        return False


async def _resolve_ticket_channel_from_token_info(
    guild: discord.Guild,
    token_info: Dict[str, Any],
) -> Optional[discord.TextChannel]:
    try:
        channel_id = int(str(token_info.get("channel_id") or "0") or 0)
        if channel_id <= 0:
            return None
    except Exception:
        return None

    try:
        channel = guild.get_channel(channel_id)
        if isinstance(channel, discord.TextChannel):
            return channel
    except Exception:
        pass

    try:
        fetched = await guild.fetch_channel(channel_id)
        if isinstance(fetched, discord.TextChannel):
            return fetched
    except Exception:
        pass

    return None


async def _resolve_ticket_owner(
    *,
    guild: discord.Guild,
    channel: Optional[discord.TextChannel],
    token_info: Optional[Dict[str, Any]],
) -> Optional[discord.Member]:
    try:
        if token_info:
            owner_id = int(
                str(
                    token_info.get("requester_id")
                    or token_info.get("user_id")
                    or "0"
                ) or 0
            )
            if owner_id > 0:
                try:
                    member = guild.get_member(owner_id)
                    if member is None:
                        member = await guild.fetch_member(owner_id)
                    if isinstance(member, discord.Member):
                        return member
                except Exception:
                    pass
    except Exception:
        pass

    if isinstance(channel, discord.TextChannel):
        try:
            return await find_ticket_owner_retry(channel)
        except Exception:
            return None

    return None


def _ensure_staff_member(staff_member: discord.Member) -> Tuple[bool, str]:
    try:
        if not isinstance(staff_member, discord.Member):
            return False, "Staff member context is invalid."
        if not _is_staff_member(staff_member):
            return False, "Staff only."
        return True, ""
    except Exception:
        return False, "Staff validation failed."


def _decision_already_finalized(token_info: Optional[Dict[str, Any]]) -> bool:
    try:
        if not token_info:
            return False

        decision = _safe_str(token_info.get("decision")).upper()
        if decision in {
            "APPROVED",
            "APPROVED (VC)",
            "DENIED",
            "DENIED (VC)",
            "APPROVED (ALREADY VERIFIED)",
            "APPROVED (VC) (ALREADY VERIFIED)",
            "RESUBMIT REQUESTED",
        }:
            return True

        status = _safe_str(token_info.get("status")).lower()
        if status in {"approved", "denied", "used"}:
            return True

        if bool(token_info.get("used", False)):
            return True

        return False
    except Exception:
        return False


def _roles_to_grant_for_member(
    member: discord.Member,
    roles_to_assign: List[discord.Role],
) -> List[discord.Role]:
    return [
        role
        for role in _unique_roles(roles_to_assign)
        if isinstance(role, discord.Role)
        and role.guild.id == member.guild.id
        and role not in member.roles
    ]


async def _finalize_token_decision(
    *,
    token: str,
    decision: str,
    staff_member: discord.Member,
    approved_user_id: Optional[int] = None,
    mark_used: bool = True,
) -> None:
    if not _safe_str(token):
        return
    await _mark_decision_safe(
        token,
        decision,
        int(staff_member.id),
        approved_user_id=approved_user_id,
    )
    if mark_used:
        await _set_used_safe(token, True)


async def _apply_verified_roles(
    *,
    member: discord.Member,
    staff_member: discord.Member,
    roles_to_assign: List[discord.Role],
) -> Tuple[List[discord.Role], Optional[str]]:
    """Apply approval roles while reducing mechanical member-update churn.

    The old flow performed two Discord role operations:

    1. add verified/resident roles
    2. remove Unverified

    Discord can emit a separate member-update event for each operation. For the
    normal verification path, prefer one atomic role-set edit so the approval
    mutation is seen as one final role state.

    Safety guard:
    If the member has managed roles or roles at/above the bot's top role, fall
    back to the old add/remove behavior. That avoids breaking approval for edge
    cases where Discord may reject a full role-set edit because of hierarchy or
    managed integration roles.
    """

    grant_roles = _roles_to_grant_for_member(member, roles_to_assign)
    unverified_role = _role_by_id(member.guild, int(UNVERIFIED_ROLE_ID or 0))
    had_unverified = isinstance(unverified_role, discord.Role) and unverified_role in (member.roles or [])

    if not grant_roles and not had_unverified:
        return [], None

    current_roles = [
        role
        for role in (member.roles or [])
        if isinstance(role, discord.Role)
        and not role.is_default()
        and role.guild.id == member.guild.id
    ]

    final_by_id: Dict[int, discord.Role] = {
        int(role.id): role
        for role in current_roles
    }

    for role in grant_roles:
        final_by_id[int(role.id)] = role

    if isinstance(unverified_role, discord.Role):
        final_by_id.pop(int(unverified_role.id), None)

    atomic_edit_safe = False
    try:
        me = member.guild.me
        bot_top_role = getattr(me, "top_role", None)
        atomic_edit_safe = isinstance(bot_top_role, discord.Role)

        if atomic_edit_safe:
            for role in list(final_by_id.values()):
                if not isinstance(role, discord.Role):
                    atomic_edit_safe = False
                    break
                if getattr(role, "managed", False):
                    atomic_edit_safe = False
                    break
                if role >= bot_top_role:
                    atomic_edit_safe = False
                    break
    except Exception:
        atomic_edit_safe = False

    if atomic_edit_safe:
        await member.edit(
            roles=list(final_by_id.values()),
            reason=f"Dank Shield approval roles by {staff_member} ({staff_member.id})",
        )
        return grant_roles, None

    if grant_roles:
        await member.add_roles(
            *grant_roles,
            reason=f"Dank Shield approved by {staff_member} ({staff_member.id})",
        )

    _, remove_error = await _remove_unverified_role_if_present(
        member,
        reason=f"Dank Shield approval cleanup by {staff_member} ({staff_member.id})",
    )

    return grant_roles, remove_error


# ============================================================
# Shared context resolution
# ============================================================

async def resolve_verification_context(
    *,
    guild: discord.Guild,
    token: Optional[str] = None,
    channel: Optional[discord.TextChannel] = None,
    allow_non_ticket_channel: bool = False,
) -> Dict[str, Any]:
    tok = str(token or "").strip()
    token_info: Optional[Dict[str, Any]] = None
    resolved_channel = channel

    if not tok and not isinstance(resolved_channel, discord.TextChannel):
        return _result(
            False,
            "Missing verification context.",
            token=None,
            token_info=None,
            channel=None,
            owner=None,
        )

    if tok:
        token_info = sb_get_token_info(tok)
        if not token_info:
            return _result(
                False,
                "Invalid or expired token.",
                token=None,
                token_info=None,
                channel=None,
                owner=None,
            )

        if not _token_belongs_to_guild(token_info, guild):
            return _result(
                False,
                "That token doesn’t belong to this server.",
                token=tok,
                token_info=token_info,
                channel=None,
                owner=None,
            )

        token_channel = await _resolve_ticket_channel_from_token_info(guild, token_info)
        if isinstance(token_channel, discord.TextChannel):
            resolved_channel = token_channel

        if not _token_belongs_to_channel(token_info, resolved_channel):
            return _result(
                False,
                "That token doesn’t belong to this ticket.",
                token=tok,
                token_info=token_info,
                channel=resolved_channel,
                owner=None,
            )

        if token_is_expired(token_info):
            return _result(
                False,
                "This token expired. Generate a new link.",
                token=tok,
                token_info=token_info,
                channel=resolved_channel,
                owner=None,
            )

    if isinstance(resolved_channel, discord.TextChannel):
        if not allow_non_ticket_channel and not is_verification_ticket_channel(resolved_channel):
            return _result(
                False,
                "Not a verification ticket channel.",
                token=tok,
                token_info=token_info,
                channel=resolved_channel,
                owner=None,
            )

    owner = await _resolve_ticket_owner(
        guild=guild,
        channel=resolved_channel if isinstance(resolved_channel, discord.TextChannel) else None,
        token_info=token_info,
    )

    return _result(
        True,
        "OK",
        token=tok,
        token_info=token_info,
        channel=resolved_channel,
        owner=owner,
    )


# ============================================================
# UI helpers
# ============================================================

async def ensure_ticket_verify_ui(
    channel: discord.TextChannel,
    *,
    requester_id: Optional[int] = None,
) -> Dict[str, Any]:
    try:
        ok = await _transcripts_ensure_verify_ui_present(
            channel,
            reason=f"verification_service_ensure:{int(requester_id or 0)}",
        )
        if ok:
            return _result(True, "Verify UI ensured.")

        fallback = await post_or_replace_verify_ui(
            channel,
            requester_id=requester_id,
            reason=f"verification_service_fallback:{int(requester_id or 0)}",
            site_url=VERIFY_SITE_URL,
            ttl_minutes=int(TOKEN_TTL_MINUTES or 20),
            allow_regen=bool(ALLOW_USER_VERIFYLINK),
        )
        if _ui_result_is_success(fallback):
            return _result(True, "Verify UI refreshed.", ui_result=str(fallback))
        return _result(False, "Verify UI could not be ensured.")
    except Exception as e:
        return _result(False, f"Failed to ensure verify UI: {e}")


async def reissue_verify_ui(
    *,
    channel: discord.TextChannel,
    requester_id: Optional[int],
    actor_id: int,
    reason: str = "reissue",
) -> Dict[str, Any]:
    try:
        ui_result = await post_or_replace_verify_ui(
            channel,
            requester_id=requester_id,
            reason=f"{reason}:{actor_id}",
            site_url=VERIFY_SITE_URL,
            ttl_minutes=int(TOKEN_TTL_MINUTES or 20),
            allow_regen=bool(ALLOW_USER_VERIFYLINK),
        )
        if not _ui_result_is_success(ui_result):
            return _result(False, "Failed to refresh verify UI.", ui_result=str(ui_result or ""))
        return _result(True, "Verify UI refreshed.", ui_result=str(ui_result or ""))
    except Exception as e:
        return _result(False, f"Failed to refresh verify UI: {e}", ui_result="")


async def request_resubmission(
    *,
    guild: discord.Guild,
    channel: discord.TextChannel,
    token: str,
    staff_member: discord.Member,
    owner: Optional[discord.Member] = None,
    prompt_in_channel: bool = True,
) -> Dict[str, Any]:
    ok_staff, staff_err = _ensure_staff_member(staff_member)
    if not ok_staff:
        return _result(False, staff_err)

    ctx = await resolve_verification_context(
        guild=guild,
        token=token,
        channel=channel,
    )
    if not ctx.get("ok"):
        return ctx

    resolved_channel = ctx.get("channel")
    if not isinstance(resolved_channel, discord.TextChannel):
        return _result(False, "Could not resolve the verification ticket channel.")

    resolved_owner = owner if isinstance(owner, discord.Member) else ctx.get("owner")
    if resolved_owner is not None and not isinstance(resolved_owner, discord.Member):
        resolved_owner = None

    token_info = ctx.get("token_info")

    lock = _action_lock(
        guild=guild,
        token=token,
        channel=resolved_channel,
        owner=resolved_owner if isinstance(resolved_owner, discord.Member) else None,
        action="resubmit",
    )

    async with lock:
        fresh_token_info = await _refresh_token_info(token)
        if _decision_already_finalized(fresh_token_info):
            return _result(
                False,
                "This verification decision is already finalized.",
                token=token,
                owner=resolved_owner,
                channel=resolved_channel,
                already_finalized=True,
            )

        await _mark_decision_safe(token, "RESUBMIT REQUESTED", int(staff_member.id))
        await _update_ticket_decision_metadata(
            channel=resolved_channel,
            decision="RESUBMIT REQUESTED",
            staff_member=staff_member,
            owner=resolved_owner if isinstance(resolved_owner, discord.Member) else None,
        )
        _sync_member_verification_context(
            guild=guild,
            owner=resolved_owner if isinstance(resolved_owner, discord.Member) else None,
            channel=resolved_channel,
            token_info=fresh_token_info if isinstance(fresh_token_info, dict) else (token_info if isinstance(token_info, dict) else None),
            staff_member=staff_member,
            decision_text="RESUBMIT REQUESTED",
        )

        reissue = await reissue_verify_ui(
            channel=resolved_channel,
            requester_id=int(resolved_owner.id) if isinstance(resolved_owner, discord.Member) else None,
            actor_id=int(staff_member.id),
            reason="resubmit_requested",
        )

        # Do not mark token used on resubmit request.
        # The user needs a fresh path, and "used" can wrongly block later actions.

        if prompt_in_channel:
            try:
                if isinstance(resolved_owner, discord.Member):
                    await resolved_channel.send(
                        f"🔁 {resolved_owner.mention} Please **resubmit** your ID using the secure upload button above.",
                        allowed_mentions=discord.AllowedMentions.none(),
                    )
                else:
                    await resolved_channel.send(
                        "🔁 Please **resubmit** your ID using the secure upload button above.",
                        allowed_mentions=discord.AllowedMentions.none(),
                    )
            except Exception:
                pass

        return _result(
            bool(reissue.get("ok")),
            "Resubmission requested." if reissue.get("ok") else str(reissue.get("message") or "Failed to request resubmission."),
            ui_result=str(reissue.get("ui_result") or ""),
            owner=resolved_owner,
            channel=resolved_channel,
        )


# ============================================================
# Decision helpers
# ============================================================

async def approve_verification(
    *,
    guild: discord.Guild,
    channel: Optional[discord.TextChannel],
    token: str,
    staff_member: discord.Member,
    decision_text: str = "APPROVED",
    close_after: bool = True,
    owner: Optional[discord.Member] = None,
) -> Dict[str, Any]:
    ok_staff, staff_err = _ensure_staff_member(staff_member)
    if not ok_staff:
        return _result(False, staff_err)

    ctx = await resolve_verification_context(
        guild=guild,
        token=token,
        channel=channel,
    )
    if not ctx.get("ok"):
        return ctx

    resolved_channel = ctx.get("channel")
    resolved_owner = owner if isinstance(owner, discord.Member) else ctx.get("owner")
    token_info = ctx.get("token_info")

    if resolved_owner is not None and not isinstance(resolved_owner, discord.Member):
        resolved_owner = None

    lock = _action_lock(
        guild=guild,
        token=token,
        channel=resolved_channel if isinstance(resolved_channel, discord.TextChannel) else None,
        owner=resolved_owner if isinstance(resolved_owner, discord.Member) else None,
        action="approve",
    )

    async with lock:
        fresh_token_info = await _refresh_token_info(token)
        if _decision_already_finalized(fresh_token_info):
            return _result(
                False,
                "This verification decision is already finalized.",
                owner=resolved_owner,
                channel=resolved_channel,
                already_finalized=True,
            )

        if _member_looks_already_verified(resolved_owner):
            already_decision = f"{decision_text} (already verified)"
            await _finalize_token_decision(
                token=token,
                decision=already_decision,
                staff_member=staff_member,
                approved_user_id=int(resolved_owner.id) if isinstance(resolved_owner, discord.Member) else None,
                mark_used=True,
            )
            await _update_ticket_decision_metadata(
                channel=resolved_channel if isinstance(resolved_channel, discord.TextChannel) else None,
                decision=already_decision,
                staff_member=staff_member,
                owner=resolved_owner if isinstance(resolved_owner, discord.Member) else None,
            )
            _sync_member_verification_context(
                guild=guild,
                owner=resolved_owner if isinstance(resolved_owner, discord.Member) else None,
                channel=resolved_channel if isinstance(resolved_channel, discord.TextChannel) else None,
                token_info=fresh_token_info if isinstance(fresh_token_info, dict) else (token_info if isinstance(token_info, dict) else None),
                staff_member=staff_member,
                decision_text=already_decision,
            )

            return _result(
                False,
                "Duplicate approval blocked: member already appears verified.",
                owner=resolved_owner,
                channel=resolved_channel,
                roles=[],
                already_verified=True,
            )

        can_assign, error_msg, roles_to_assign = await _transcripts_check_bot_can_assign_roles(guild)
        if not can_assign:
            failed_decision = f"{decision_text} (roles unavailable)"
            await _mark_decision_safe(token, failed_decision, int(staff_member.id))
            await _update_ticket_decision_metadata(
                channel=resolved_channel if isinstance(resolved_channel, discord.TextChannel) else None,
                decision=failed_decision,
                staff_member=staff_member,
                owner=resolved_owner if isinstance(resolved_owner, discord.Member) else None,
            )
            _sync_member_verification_context(
                guild=guild,
                owner=resolved_owner if isinstance(resolved_owner, discord.Member) else None,
                channel=resolved_channel if isinstance(resolved_channel, discord.TextChannel) else None,
                token_info=fresh_token_info if isinstance(fresh_token_info, dict) else (token_info if isinstance(token_info, dict) else None),
                staff_member=staff_member,
                decision_text=failed_decision,
            )

            return _result(
                False,
                f"Cannot assign roles: {error_msg}",
                owner=resolved_owner,
                channel=resolved_channel,
                roles=[],
                retryable=True,
            )

        if not isinstance(resolved_owner, discord.Member):
            no_owner_decision = f"{decision_text} (owner not detected)"
            await _mark_decision_safe(token, no_owner_decision, int(staff_member.id))
            await _update_ticket_decision_metadata(
                channel=resolved_channel if isinstance(resolved_channel, discord.TextChannel) else None,
                decision=no_owner_decision,
                staff_member=staff_member,
                owner=None,
            )

            return _result(
                False,
                "Approval is blocked because I couldn't detect the ticket owner to grant roles.",
                owner=None,
                channel=resolved_channel,
                roles=[],
                retryable=True,
            )

        try:
            grant_roles, remove_error = await _apply_verified_roles(
                member=resolved_owner,
                staff_member=staff_member,
                roles_to_assign=roles_to_assign,
            )
        except discord.Forbidden:
            failed_decision = f"{decision_text} (role add failed)"
            await _mark_decision_safe(token, failed_decision, int(staff_member.id))
            await _update_ticket_decision_metadata(
                channel=resolved_channel if isinstance(resolved_channel, discord.TextChannel) else None,
                decision=failed_decision,
                staff_member=staff_member,
                owner=resolved_owner,
            )
            _sync_member_verification_context(
                guild=guild,
                owner=resolved_owner,
                channel=resolved_channel if isinstance(resolved_channel, discord.TextChannel) else None,
                token_info=fresh_token_info if isinstance(fresh_token_info, dict) else (token_info if isinstance(token_info, dict) else None),
                staff_member=staff_member,
                decision_text=failed_decision,
            )

            return _result(
                False,
                "I can't add roles. Fix my role position + permissions (Manage Roles) and try again.",
                owner=resolved_owner,
                channel=resolved_channel,
                roles=[],
                retryable=True,
            )
        except Exception as e:
            return _result(
                False,
                f"Unexpected error: {e}",
                owner=resolved_owner,
                channel=resolved_channel,
                roles=[],
                retryable=True,
            )

        if remove_error:
            partial_decision = f"{decision_text} (unverified cleanup failed)"
            await _finalize_token_decision(
                token=token,
                decision=partial_decision,
                staff_member=staff_member,
                approved_user_id=int(resolved_owner.id),
                mark_used=True,
            )
            await _update_ticket_decision_metadata(
                channel=resolved_channel if isinstance(resolved_channel, discord.TextChannel) else None,
                decision=partial_decision,
                staff_member=staff_member,
                owner=resolved_owner,
            )
            _sync_member_verification_context(
                guild=guild,
                owner=resolved_owner,
                channel=resolved_channel if isinstance(resolved_channel, discord.TextChannel) else None,
                token_info=fresh_token_info if isinstance(fresh_token_info, dict) else (token_info if isinstance(token_info, dict) else None),
                staff_member=staff_member,
                decision_text=partial_decision,
            )

            return _result(
                False,
                f"Roles were added, but removing Unverified failed: {remove_error}",
                owner=resolved_owner,
                channel=resolved_channel,
                roles=grant_roles,
                partial_success=True,
            )

        await _finalize_token_decision(
            token=token,
            decision=decision_text,
            staff_member=staff_member,
            approved_user_id=int(resolved_owner.id),
            mark_used=True,
        )
        await _update_ticket_decision_metadata(
            channel=resolved_channel if isinstance(resolved_channel, discord.TextChannel) else None,
            decision=decision_text,
            staff_member=staff_member,
            owner=resolved_owner,
        )
        _sync_member_verification_context(
            guild=guild,
            owner=resolved_owner,
            channel=resolved_channel if isinstance(resolved_channel, discord.TextChannel) else None,
            token_info=fresh_token_info if isinstance(fresh_token_info, dict) else (token_info if isinstance(token_info, dict) else None),
            staff_member=staff_member,
            decision_text=decision_text,
        )

        if close_after and isinstance(resolved_channel, discord.TextChannel):
            await _auto_close_after_decision_safe(
                resolved_channel,
                closer=staff_member,
                decision=decision_text,
            )

        message = (
            f"Approved. Granted {_roles_display(grant_roles)} to {resolved_owner.display_name}."
            if grant_roles
            else f"Approved for {resolved_owner.display_name}."
        )

        return _result(
            True,
            message,
            owner=resolved_owner,
            channel=resolved_channel,
            roles=grant_roles,
        )


async def deny_verification(
    *,
    guild: discord.Guild,
    channel: Optional[discord.TextChannel],
    token: str,
    staff_member: discord.Member,
    decision_text: str = "DENIED",
    close_after: bool = True,
) -> Dict[str, Any]:
    ok_staff, staff_err = _ensure_staff_member(staff_member)
    if not ok_staff:
        return _result(False, staff_err)

    ctx = await resolve_verification_context(
        guild=guild,
        token=token,
        channel=channel,
    )
    if not ctx.get("ok"):
        return ctx

    resolved_channel = ctx.get("channel")
    token_info = ctx.get("token_info")
    resolved_owner = ctx.get("owner")

    lock = _action_lock(
        guild=guild,
        token=token,
        channel=resolved_channel if isinstance(resolved_channel, discord.TextChannel) else None,
        owner=resolved_owner if isinstance(resolved_owner, discord.Member) else None,
        action="deny",
    )

    async with lock:
        fresh_token_info = await _refresh_token_info(token)
        if _decision_already_finalized(fresh_token_info):
            return _result(
                False,
                "This verification decision is already finalized.",
                owner=resolved_owner,
                channel=resolved_channel,
                already_finalized=True,
            )

        await _finalize_token_decision(
            token=token,
            decision=decision_text,
            staff_member=staff_member,
            approved_user_id=None,
            mark_used=True,
        )
        await _update_ticket_decision_metadata(
            channel=resolved_channel if isinstance(resolved_channel, discord.TextChannel) else None,
            decision=decision_text,
            staff_member=staff_member,
            owner=resolved_owner if isinstance(resolved_owner, discord.Member) else None,
        )
        _sync_member_verification_context(
            guild=guild,
            owner=resolved_owner if isinstance(resolved_owner, discord.Member) else None,
            channel=resolved_channel if isinstance(resolved_channel, discord.TextChannel) else None,
            token_info=fresh_token_info if isinstance(fresh_token_info, dict) else (token_info if isinstance(token_info, dict) else None),
            staff_member=staff_member,
            decision_text=decision_text,
        )

        if close_after and isinstance(resolved_channel, discord.TextChannel):
            await _auto_close_after_decision_safe(
                resolved_channel,
                closer=staff_member,
                decision=decision_text,
            )

        return _result(
            True,
            "Denied and saved.",
            owner=resolved_owner,
            channel=resolved_channel,
        )


# ============================================================
# Convenience helpers for future VC / API refactor work
# ============================================================

async def approve_vc_verification(
    *,
    guild: discord.Guild,
    channel: Optional[discord.TextChannel],
    token: str,
    staff_member: discord.Member,
    owner: Optional[discord.Member] = None,
    close_after: bool = True,
) -> Dict[str, Any]:
    return await approve_verification(
        guild=guild,
        channel=channel,
        token=token,
        staff_member=staff_member,
        decision_text="APPROVED (VC)",
        close_after=close_after,
        owner=owner,
    )


async def deny_vc_verification(
    *,
    guild: discord.Guild,
    channel: Optional[discord.TextChannel],
    token: str,
    staff_member: discord.Member,
    close_after: bool = True,
) -> Dict[str, Any]:
    return await deny_verification(
        guild=guild,
        channel=channel,
        token=token,
        staff_member=staff_member,
        decision_text="DENIED (VC)",
        close_after=close_after,
    )


__all__ = [
    "resolve_verification_context",
    "ensure_ticket_verify_ui",
    "reissue_verify_ui",
    "request_resubmission",
    "approve_verification",
    "deny_verification",
    "approve_vc_verification",
    "deny_vc_verification",
]
