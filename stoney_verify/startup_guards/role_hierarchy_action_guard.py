from __future__ import annotations

"""Role-hierarchy action guard for bot-powered member removals/timeouts."""

import re
from typing import Any, Optional

import discord

_PATCHED = False
_ORIGINAL_GUILD_KICK: Any = None
_ORIGINAL_MEMBER_KICK: Any = None
_ORIGINAL_GUILD_BAN: Any = None
_ORIGINAL_MEMBER_BAN: Any = None
_ORIGINAL_MEMBER_TIMEOUT: Any = None

_REASON_ACTOR_RE = re.compile(r"(?:by|actor|staff|moderator|mod)[:= ]+<?@?([0-9]{15,25})>?", re.I)
_PRIVILEGED_ROLE_KEYS = {
    "admin", "administrator", "moderator", "mod", "staff", "staffteam", "support", "supportteam",
    "helper", "manager", "servermanager", "botmanager", "ticketstaff", "ticketteam",
}
_OWNER_SAFE_REASON = "this member is protected from staff-side actions"


class RoleHierarchyActionBlocked(PermissionError):
    """Raised when Dank Shield blocks a privileged-target moderation action."""


def _log(message: str) -> None:
    try:
        print(f"🛡️ role_hierarchy_action_guard {message}")
    except Exception:
        pass


def _warn(message: str) -> None:
    try:
        print(f"⚠️ role_hierarchy_action_guard {message}")
    except Exception:
        pass


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or isinstance(value, bool):
            return int(default)
        text = str(value).strip()
        return int(text) if text else int(default)
    except Exception:
        return int(default)


def _role_key(value: Any) -> str:
    try:
        return re.sub(r"[^a-z0-9]+", "", str(value or "").casefold())
    except Exception:
        return ""


def _extract_actor_id(reason: Any) -> int:
    text = str(reason or "")
    match = _REASON_ACTOR_RE.search(text)
    if match:
        return _safe_int(match.group(1), 0)
    ids = re.findall(r"[0-9]{15,25}", text)
    unique = list(dict.fromkeys(ids))
    return _safe_int(unique[0], 0) if len(unique) == 1 else 0


def _role_ids(member: discord.Member) -> set[int]:
    ids: set[int] = set()
    try:
        for role in getattr(member, "roles", []) or []:
            rid = _safe_int(getattr(role, "id", 0), 0)
            if rid > 0:
                ids.add(rid)
    except Exception:
        pass
    return ids


def _named_privileged_role(member: discord.Member) -> bool:
    try:
        for role in getattr(member, "roles", []) or []:
            if getattr(role, "is_default", lambda: False)():
                continue
            if _role_key(getattr(role, "name", "")) in _PRIVILEGED_ROLE_KEYS:
                return True
    except Exception:
        pass
    return False


def _configured_privileged_role(member: discord.Member) -> bool:
    try:
        from stoney_verify.commands_ext.public_access_control import configured_control_role_ids_for_guild

        guild_id = _safe_int(getattr(getattr(member, "guild", None), "id", 0), 0)
        ids = set(int(x) for x in configured_control_role_ids_for_guild(guild_id))
        try:
            from stoney_verify.commands_ext.public_access_control import _configured_staff_role_ids  # type: ignore[attr-defined]
            ids.update(int(x) for x in _configured_staff_role_ids(member))
        except Exception:
            pass
        return bool(ids and _role_ids(member).intersection(ids))
    except Exception:
        return False


def _permission_privileged(member: discord.Member) -> bool:
    try:
        perms = getattr(member, "guild_permissions", None)
        return bool(perms and (
            getattr(perms, "administrator", False)
            or getattr(perms, "manage_guild", False)
            or getattr(perms, "manage_roles", False)
            or getattr(perms, "kick_members", False)
            or getattr(perms, "ban_members", False)
            or getattr(perms, "moderate_members", False)
            or getattr(perms, "manage_messages", False)
        ))
    except Exception:
        return False


def _is_privileged_member(member: Any) -> bool:
    if not isinstance(member, discord.Member):
        return False
    try:
        if getattr(member, "bot", False):
            return False
    except Exception:
        pass
    return bool(_configured_privileged_role(member) or _permission_privileged(member) or _named_privileged_role(member))


def _actor_from_reason(guild: discord.Guild, reason: Any) -> Optional[discord.Member]:
    actor_id = _extract_actor_id(reason)
    if actor_id <= 0:
        return None
    try:
        member = guild.get_member(actor_id)
        return member if isinstance(member, discord.Member) else None
    except Exception:
        return None


def _outranks(actor: discord.Member, target: discord.Member) -> bool:
    try:
        guild = target.guild
        if int(actor.id) == int(getattr(guild, "owner_id", 0) or 0):
            return True
        if int(target.id) == int(getattr(guild, "owner_id", 0) or 0):
            return False
        if int(actor.id) == int(target.id):
            return False
        return bool(actor.top_role > target.top_role)
    except Exception:
        return False


async def _is_owner_safe_member(target: Any) -> bool:
    try:
        from stoney_verify.startup_guards.owner_safe_members_guard import is_safe_member

        return bool(await is_safe_member(target))
    except Exception:
        return False


async def _should_block(action: str, guild: discord.Guild, target: Any, reason: Any) -> tuple[bool, str, Optional[discord.Member]]:
    if not isinstance(target, discord.Member):
        return False, "", None
    actor = _actor_from_reason(guild, reason)
    if await _is_owner_safe_member(target):
        return True, _OWNER_SAFE_REASON, actor
    if not _is_privileged_member(target):
        return False, "", actor
    if actor is None:
        return True, "unknown actor for privileged target", None
    if not _is_privileged_member(actor):
        return True, "non-privileged actor targeting privileged member", actor
    if not _outranks(actor, target):
        return True, "actor does not outrank privileged target", actor
    return False, "", actor


def _member_label(member: Any) -> str:
    try:
        return f"{getattr(member, 'display_name', None) or getattr(member, 'name', 'member')} (`{getattr(member, 'id', 'unknown')}`)"
    except Exception:
        return "that member"


async def _notify_blocked_actor(*, actor: Optional[discord.Member], target: Any, action: str, why: str) -> None:
    if actor is None:
        return
    try:
        vague_safe = str(why or "").strip().lower() == _OWNER_SAFE_REASON
        if vague_safe:
            description = (
                f"Dank Shield blocked your **{action}** action against {_member_label(target)}.\n\n"
                "This member is protected from staff moderation through the bot. "
                "Ask an authorized senior manager if action is needed."
            )
            reason_value = "member safety protection"
        else:
            description = (
                f"Dank Shield blocked your **{action}** action against {_member_label(target)}.\n\n"
                "Staff/mod/control members cannot be moderated by equal or lower-ranked staff through the bot. "
                "Ask a higher-ranked manager or the server owner to handle it."
            )
            reason_value = str(why or "role hierarchy protection")[:1024]
        embed = discord.Embed(
            title="🛡️ Staff Safety Blocked This Action",
            description=description,
            color=discord.Color.orange(),
        )
        embed.add_field(name="Reason", value=reason_value, inline=False)
        await actor.send(embed=embed)
    except Exception:
        pass


async def _block_or_run(action: str, guild: discord.Guild, target: Any, reason: Any, runner: Any) -> Any:
    blocked, why, actor = await _should_block(action, guild, target, reason)
    if blocked:
        _warn(
            f"blocked hierarchy action={action} guild={getattr(guild, 'id', None)} "
            f"target={getattr(target, 'id', None)} actor={getattr(actor, 'id', None)} why={why}"
        )
        await _notify_blocked_actor(actor=actor, target=target, action=action, why=why)
        raise RoleHierarchyActionBlocked(
            f"Dank Shield blocked {action} against protected target {getattr(target, 'id', None)}: {why}"
        )
    return await runner()


def apply() -> bool:
    """Retired global monkey patch.

    Role hierarchy protection now belongs inside explicit moderation command
    handlers. Do not patch discord.Guild/discord.Member kick/ban/timeout globally.
    """

    global _PATCHED
    if _PATCHED:
        return True
    _PATCHED = True
    _log("retired; no Discord native moderation methods are monkey-patched")
    return True


apply()

__all__ = ["apply", "RoleHierarchyActionBlocked"]
