# stoney_verify/verification_new/service.py
from __future__ import annotations

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
    from ..transcripts import (
        auto_close_after_decision,
        check_bot_can_assign_roles,
        ensure_verify_ui_present,
    )
except Exception:
    async def auto_close_after_decision(*args, **kwargs) -> None:  # type: ignore
        return None

    async def check_bot_can_assign_roles(*args, **kwargs) -> Tuple[bool, str, List[discord.Role]]:  # type: ignore
        return False, "transcripts.py missing", []

    async def ensure_verify_ui_present(*args, **kwargs) -> bool:  # type: ignore
        return False


try:
    from ..tickets_new.repository import safe_optional_update_by_channel_id
except Exception:
    async def safe_optional_update_by_channel_id(*args, **kwargs) -> bool:  # type: ignore
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
        return str(value)
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


async def _auto_close_after_decision_safe(
    channel: Optional[discord.TextChannel],
    closer: discord.Member,
    decision: str,
) -> None:
    if not isinstance(channel, discord.TextChannel):
        return

    try:
        await auto_close_after_decision(
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
        ok = await ensure_verify_ui_present(
            channel,
            requester_id=requester_id,
            site_url=VERIFY_SITE_URL,
            ttl_minutes=int(TOKEN_TTL_MINUTES or 20),
            allow_regen=bool(ALLOW_USER_VERIFYLINK),
        )
        return _result(
            bool(ok),
            "Verify UI ensured." if ok else "Verify UI could not be ensured.",
        )
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
        new_token = await post_or_replace_verify_ui(
            channel,
            requester_id=requester_id,
            reason=f"{reason}:{actor_id}",
            site_url=VERIFY_SITE_URL,
            ttl_minutes=int(TOKEN_TTL_MINUTES or 20),
            allow_regen=bool(ALLOW_USER_VERIFYLINK),
        )
        if not new_token:
            return _result(False, "Failed to reissue verify link.", token=None)

        return _result(True, "Verify link reissued.", token=new_token)
    except Exception as e:
        return _result(False, f"Failed to reissue verify link: {e}", token=None)


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

    await _mark_decision_safe(token, "RESUBMIT REQUESTED", int(staff_member.id))
    await _update_ticket_decision_metadata(
        channel=resolved_channel,
        decision="RESUBMIT REQUESTED",
        staff_member=staff_member,
        owner=resolved_owner if isinstance(resolved_owner, discord.Member) else None,
    )

    reissue = await reissue_verify_ui(
        channel=resolved_channel,
        requester_id=int(resolved_owner.id) if isinstance(resolved_owner, discord.Member) else None,
        actor_id=int(staff_member.id),
        reason="resubmit_requested",
    )

    if prompt_in_channel:
        try:
            if isinstance(resolved_owner, discord.Member):
                await resolved_channel.send(
                    f"🔁 {resolved_owner.mention} Please **resubmit** your ID using the new secure upload button above.",
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            else:
                await resolved_channel.send(
                    "🔁 Please **resubmit** your ID using the new secure upload button above.",
                    allowed_mentions=discord.AllowedMentions.none(),
                )
        except Exception:
            pass

    return _result(
        bool(reissue.get("ok")),
        "Resubmission requested." if reissue.get("ok") else str(reissue.get("message") or "Failed to request resubmission."),
        token=str(reissue.get("token") or ""),
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

    if token_info and bool(token_info.get("used", False)):
        return _result(
            False,
            "This token has already been used.",
            owner=resolved_owner,
            channel=resolved_channel,
        )

    # HARD STOP: shared service-level duplicate protection.
    if _member_looks_already_verified(resolved_owner):
        await _mark_decision_safe(
            token,
            f"{decision_text} (already verified)",
            int(staff_member.id),
            approved_user_id=int(resolved_owner.id) if isinstance(resolved_owner, discord.Member) else None,
        )
        await _set_used_safe(token, True)
        await _update_ticket_decision_metadata(
            channel=resolved_channel if isinstance(resolved_channel, discord.TextChannel) else None,
            decision=f"{decision_text} (already verified)",
            staff_member=staff_member,
            owner=resolved_owner if isinstance(resolved_owner, discord.Member) else None,
        )

        return _result(
            False,
            "Duplicate approval blocked: member already appears verified.",
            owner=resolved_owner,
            channel=resolved_channel,
            roles=[],
            already_verified=True,
        )

    can_assign, error_msg, roles_to_assign = await check_bot_can_assign_roles(guild)
    if not can_assign:
        await _mark_decision_safe(token, f"{decision_text} (roles failed)", int(staff_member.id))
        await _set_used_safe(token, True)
        await _update_ticket_decision_metadata(
            channel=resolved_channel if isinstance(resolved_channel, discord.TextChannel) else None,
            decision=f"{decision_text} (roles failed)",
            staff_member=staff_member,
            owner=resolved_owner if isinstance(resolved_owner, discord.Member) else None,
        )

        if close_after and isinstance(resolved_channel, discord.TextChannel):
            await _auto_close_after_decision_safe(
                resolved_channel,
                closer=staff_member,
                decision=f"{decision_text} (roles failed)",
            )

        return _result(
            False,
            f"Cannot assign roles: {error_msg}",
            owner=resolved_owner,
            channel=resolved_channel,
            roles=[],
        )

    if not isinstance(resolved_owner, discord.Member):
        await _mark_decision_safe(token, f"{decision_text} (owner not detected)", int(staff_member.id))
        await _set_used_safe(token, True)
        await _update_ticket_decision_metadata(
            channel=resolved_channel if isinstance(resolved_channel, discord.TextChannel) else None,
            decision=f"{decision_text} (owner not detected)",
            staff_member=staff_member,
            owner=None,
        )

        if close_after and isinstance(resolved_channel, discord.TextChannel):
            await _auto_close_after_decision_safe(
                resolved_channel,
                closer=staff_member,
                decision=f"{decision_text} (owner not detected)",
            )

        return _result(
            False,
            "Approved decision saved, but I couldn't detect the ticket owner to grant roles.",
            owner=None,
            channel=resolved_channel,
            roles=[],
        )

    grant_roles = [
        role
        for role in _unique_roles(roles_to_assign)
        if isinstance(role, discord.Role)
        and role.guild.id == resolved_owner.guild.id
        and role not in resolved_owner.roles
    ]

    try:
        if grant_roles:
            await resolved_owner.add_roles(
                *grant_roles,
                reason=f"Stoney Verify approved by {staff_member} ({staff_member.id})",
            )

        _, remove_error = await _remove_unverified_role_if_present(
            resolved_owner,
            reason=f"Stoney Verify approval cleanup by {staff_member} ({staff_member.id})",
        )

        if remove_error:
            await _mark_decision_safe(
                token,
                f"{decision_text} (unverified cleanup failed)",
                int(staff_member.id),
                approved_user_id=int(resolved_owner.id),
            )
            await _set_used_safe(token, True)
            await _update_ticket_decision_metadata(
                channel=resolved_channel if isinstance(resolved_channel, discord.TextChannel) else None,
                decision=f"{decision_text} (unverified cleanup failed)",
                staff_member=staff_member,
                owner=resolved_owner,
            )

            return _result(
                False,
                f"Roles were added, but removing Unverified failed: {remove_error}",
                owner=resolved_owner,
                channel=resolved_channel,
                roles=grant_roles,
                partial_success=True,
            )

    except discord.Forbidden:
        await _mark_decision_safe(token, f"{decision_text} (role add failed)", int(staff_member.id))
        await _update_ticket_decision_metadata(
            channel=resolved_channel if isinstance(resolved_channel, discord.TextChannel) else None,
            decision=f"{decision_text} (role add failed)",
            staff_member=staff_member,
            owner=resolved_owner,
        )

        return _result(
            False,
            "I can't add roles. Fix my role position + permissions (Manage Roles) and try again.",
            owner=resolved_owner,
            channel=resolved_channel,
            roles=[],
        )
    except Exception as e:
        return _result(
            False,
            f"Unexpected error: {e}",
            owner=resolved_owner,
            channel=resolved_channel,
            roles=[],
        )

    await _mark_decision_safe(
        token,
        decision_text,
        int(staff_member.id),
        approved_user_id=int(resolved_owner.id),
    )
    await _set_used_safe(token, True)
    await _update_ticket_decision_metadata(
        channel=resolved_channel if isinstance(resolved_channel, discord.TextChannel) else None,
        decision=decision_text,
        staff_member=staff_member,
        owner=resolved_owner,
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

    if token_info and bool(token_info.get("used", False)):
        return _result(
            False,
            "This token has already been used.",
            owner=resolved_owner,
            channel=resolved_channel,
        )

    await _mark_decision_safe(token, decision_text, int(staff_member.id))
    await _set_used_safe(token, True)
    await _update_ticket_decision_metadata(
        channel=resolved_channel if isinstance(resolved_channel, discord.TextChannel) else None,
        decision=decision_text,
        staff_member=staff_member,
        owner=resolved_owner if isinstance(resolved_owner, discord.Member) else None,
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
