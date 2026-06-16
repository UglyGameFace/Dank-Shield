from __future__ import annotations

"""Staff-on-staff moderation safety guard.

Discord role hierarchy protects some actions, but bot-powered moderation can be
more dangerous because the bot often outranks staff. This guard blocks Dank
Shield moderation actions against staff/control members unless the actor clearly
outranks the target or is the guild owner.

Default mode: hierarchy + server-owner override.
"""

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

_STAFF_NAME_KEYS = {
    "admin",
    "administrator",
    "moderator",
    "mod",
    "staff",
    "staffteam",
    "support",
    "supportteam",
    "helper",
    "manager",
    "servermanager",
    "botmanager",
    "ticketstaff",
    "ticketteam",
}


def _log(message: str) -> None:
    try:
        print(f"🛡️ staff_moderation_safety_guard {message}")
    except Exception:
        pass


def _warn(message: str) -> None:
    try:
        print(f"⚠️ staff_moderation_safety_guard {message}")
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
    # Fallback: if a reason includes exactly one user-like snowflake, treat it
    # as the actor. This catches many bot reasons such as "Requested by 123".
    ids = re.findall(r"[0-9]{15,25}", text)
    unique = list(dict.fromkeys(ids))
    return _safe_int(unique[0], 0) if len(unique) == 1 else 0


def _role_ids(member: discord.Member) -> set[int]:
    out: set[int] = set()
    try:
        for role in getattr(member, "roles", []) or []:
            rid = _safe_int(getattr(role, "id", 0), 0)
            if rid > 0:
                out.add(rid)
    except Exception:
        pass
    return out


def _named_staff_role(member: discord.Member) -> bool:
    try:
        for role in getattr(member, "roles", []) or []:
            if getattr(role, "is_default", lambda: False)():
                continue
            if _role_key(getattr(role, "name", "")) in _STAFF_NAME_KEYS:
                return True
    except Exception:
        pass
    return False


def _configured_staff_or_control(member: discord.Member) -> bool:
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


def _permission_staff(member: discord.Member) -> bool:
    try:
        perms = getattr(member, "guild_permissions", None)
        return bool(
            perms
            and (
                getattr(perms, "administrator", False)
                or getattr(perms, "manage_guild", False)
                or getattr(perms, "manage_roles", False)
                or getattr(perms, "kick_members", False)
                or getattr(perms, "ban_members", False)
                or getattr(perms, "moderate_members", False)
                or getattr(perms, "manage_messages", False)
            )
        )
    except Exception:
        return False


def _is_staff_like(member: Any) -> bool:
    if not isinstance(member, discord.Member):
        return False
    try:
        if getattr(member, "bot", False):
            return False
    except Exception:
        pass
    if _configured_staff_or_control(member):
        return True
    if _permission_staff(member):
        return True
    if _named_staff_role(member):
        return True
    return False


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


def _should_block(action: str, guild: discord.Guild, target: Any, reason: Any) -> tuple[bool, str]:
    if not isinstance(target, discord.Member):
        return False, ""
    if not _is_staff_like(target):
        return False, ""
    actor = _actor_from_reason(guild, reason)
    if actor is None:
        # Unknown actor + staff target = safer to block. Internal automation
        # should not be moderating staff without a clear actor in the reason.
        return True, "unknown actor for staff target"
    if not _is_staff_like(actor):
        return True, "non-staff actor targeting staff"
    if not _outranks(actor, target):
        return True, "actor does not outrank staff target"
    return False, ""


async def _block_or_run(action: str, guild: discord.Guild, target: Any, reason: Any, runner: Any) -> Any:
    blocked, why = _should_block(action, guild, target, reason)
    if blocked:
        _warn(
            f"blocked staff-on-staff {action} guild={getattr(guild, 'id', None)} "
            f"target={getattr(target, 'id', None)} why={why} reason={reason!r}"
        )
        return None
    return await runner()


def _patch_guild_kick() -> None:
    global _ORIGINAL_GUILD_KICK
    original = getattr(discord.Guild, "kick", None)
    if not callable(original) or getattr(original, "_staff_moderation_safety_wrapped", False):
        return

    async def wrapped(self: discord.Guild, user: Any, *, reason: Optional[str] = None) -> Any:
        return await _block_or_run("kick", self, user, reason, lambda: original(self, user, reason=reason))

    setattr(wrapped, "_staff_moderation_safety_wrapped", True)
    setattr(wrapped, "_staff_moderation_safety_original", original)
    _ORIGINAL_GUILD_KICK = original
    discord.Guild.kick = wrapped  # type: ignore[method-assign]


def _patch_member_kick() -> None:
    global _ORIGINAL_MEMBER_KICK
    original = getattr(discord.Member, "kick", None)
    if not callable(original) or getattr(original, "_staff_moderation_safety_wrapped", False):
        return

    async def wrapped(self: discord.Member, *, reason: Optional[str] = None) -> Any:
        return await _block_or_run("kick", self.guild, self, reason, lambda: original(self, reason=reason))

    setattr(wrapped, "_staff_moderation_safety_wrapped", True)
    setattr(wrapped, "_staff_moderation_safety_original", original)
    _ORIGINAL_MEMBER_KICK = original
    discord.Member.kick = wrapped  # type: ignore[method-assign]


def _patch_guild_ban() -> None:
    global _ORIGINAL_GUILD_BAN
    original = getattr(discord.Guild, "ban", None)
    if not callable(original) or getattr(original, "_staff_moderation_safety_wrapped", False):
        return

    async def wrapped(self: discord.Guild, user: Any, *, reason: Optional[str] = None, **kwargs: Any) -> Any:
        return await _block_or_run("ban", self, user, reason, lambda: original(self, user, reason=reason, **kwargs))

    setattr(wrapped, "_staff_moderation_safety_wrapped", True)
    setattr(wrapped, "_staff_moderation_safety_original", original)
    _ORIGINAL_GUILD_BAN = original
    discord.Guild.ban = wrapped  # type: ignore[method-assign]


def _patch_member_ban() -> None:
    global _ORIGINAL_MEMBER_BAN
    original = getattr(discord.Member, "ban", None)
    if not callable(original) or getattr(original, "_staff_moderation_safety_wrapped", False):
        return

    async def wrapped(self: discord.Member, *, reason: Optional[str] = None, **kwargs: Any) -> Any:
        return await _block_or_run("ban", self.guild, self, reason, lambda: original(self, reason=reason, **kwargs))

    setattr(wrapped, "_staff_moderation_safety_wrapped", True)
    setattr(wrapped, "_staff_moderation_safety_original", original)
    _ORIGINAL_MEMBER_BAN = original
    discord.Member.ban = wrapped  # type: ignore[method-assign]


def _patch_member_timeout() -> None:
    global _ORIGINAL_MEMBER_TIMEOUT
    original = getattr(discord.Member, "timeout", None)
    if not callable(original) or getattr(original, "_staff_moderation_safety_wrapped", False):
        return

    async def wrapped(self: discord.Member, *args: Any, reason: Optional[str] = None, **kwargs: Any) -> Any:
        return await _block_or_run("timeout", self.guild, self, reason, lambda: original(self, *args, reason=reason, **kwargs))

    setattr(wrapped, "_staff_moderation_safety_wrapped", True)
    setattr(wrapped, "_staff_moderation_safety_original", original)
    _ORIGINAL_MEMBER_TIMEOUT = original
    discord.Member.timeout = wrapped  # type: ignore[method-assign]


def apply() -> bool:
    """Retired global monkey patch.

    Staff moderation safety now belongs inside explicit command handlers.
    Patching discord.Guild/discord.Member native methods globally caused
    kick/ban/timeout wrapper conflicts in production.
    """

    global _PATCHED
    if _PATCHED:
        return True
    _PATCHED = True
    _log("retired; no Discord native moderation methods are monkey-patched")
    return True


apply()

__all__ = ["apply"]
