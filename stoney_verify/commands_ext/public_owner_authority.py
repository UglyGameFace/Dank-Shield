from __future__ import annotations

"""Canonical Discord guild-owner and manager authority helpers.

Guild ownership is identity truth, not a derived role/permission result. Public
management surfaces must recognize the actual guild owner before they depend on
Member cache shape, configured roles, or resolved guild permissions.

Interaction payload permissions are also authoritative runtime evidence for
Administrator / Manage Server. They are deliberately checked independently of
the cached discord Member object because application-command interactions can be
usable even when cached member or guild state is incomplete.
"""

from typing import Any

import discord


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or isinstance(value, bool):
            return int(default)
        text = str(value).strip()
        return int(text) if text else int(default)
    except Exception:
        return int(default)


def _guild_owner_id(guild: Any) -> int:
    """Resolve owner identity from either canonical guild owner surface."""
    owner_id = _safe_int(getattr(guild, "owner_id", 0), 0)
    if owner_id > 0:
        return owner_id
    return _safe_int(getattr(getattr(guild, "owner", None), "id", 0), 0)


def _permission_flag(permissions: Any, name: str) -> bool:
    try:
        return bool(getattr(permissions, str(name), False))
    except Exception:
        return False


def _interaction_permission_flag(interaction: Any, name: str) -> bool:
    """Read Discord-resolved interaction permissions without Member cache dependence."""
    try:
        permissions = getattr(interaction, "permissions", None)
        if permissions is not None and _permission_flag(permissions, name):
            return True
    except Exception:
        pass
    return False


def _member_permission_flag(user: Any, name: str) -> bool:
    try:
        if not isinstance(user, discord.Member):
            return False
        return _permission_flag(getattr(user, "guild_permissions", None), name)
    except Exception:
        return False


def is_actual_guild_owner(user: Any, guild: Any = None) -> bool:
    try:
        resolved_guild = guild if guild is not None else getattr(user, "guild", None)
        user_id = _safe_int(getattr(user, "id", 0), 0)
        owner_id = _guild_owner_id(resolved_guild)
        return bool(user_id > 0 and owner_id > 0 and user_id == owner_id)
    except Exception:
        return False


def interaction_is_actual_guild_owner(interaction: Any) -> bool:
    try:
        return is_actual_guild_owner(
            getattr(interaction, "user", None),
            getattr(interaction, "guild", None),
        )
    except Exception:
        return False


def interaction_has_administrator_authority(interaction: Any) -> bool:
    """Owner or Discord-resolved Administrator, independent of Member cache shape."""
    if interaction_is_actual_guild_owner(interaction):
        return True

    if _interaction_permission_flag(interaction, "administrator"):
        return True

    return _member_permission_flag(getattr(interaction, "user", None), "administrator")


def interaction_has_manage_guild_authority(interaction: Any) -> bool:
    """Owner, Administrator, or Manage Server for public management surfaces."""
    if interaction_has_administrator_authority(interaction):
        return True

    if _interaction_permission_flag(interaction, "manage_guild"):
        return True

    return _member_permission_flag(getattr(interaction, "user", None), "manage_guild")


def interaction_has_channel_management_authority(interaction: Any) -> bool:
    """Owner, Administrator, Manage Server, or Manage Channels.

    This is the canonical authority boundary for channel-access repair UIs.
    Discord-resolved interaction permissions are checked before cached Member
    permissions so repair controls do not reject a real guild owner/manager just
    because member cache state is partial.
    """
    if interaction_has_manage_guild_authority(interaction):
        return True

    if _interaction_permission_flag(interaction, "manage_channels"):
        return True

    return _member_permission_flag(
        getattr(interaction, "user", None),
        "manage_channels",
    )


def interaction_authority_snapshot(interaction: Any) -> dict[str, Any]:
    """Small non-secret diagnostic snapshot for denied management interactions."""
    guild = getattr(interaction, "guild", None)
    user = getattr(interaction, "user", None)
    resolved = getattr(interaction, "permissions", None)
    member_permissions = getattr(user, "guild_permissions", None)
    return {
        "guild_id": _safe_int(getattr(guild, "id", 0), 0),
        "user_id": _safe_int(getattr(user, "id", 0), 0),
        "guild_owner_id": _safe_int(getattr(guild, "owner_id", 0), 0),
        "guild_owner_object_id": _safe_int(
            getattr(getattr(guild, "owner", None), "id", 0),
            0,
        ),
        "interaction_administrator": _permission_flag(resolved, "administrator"),
        "interaction_manage_guild": _permission_flag(resolved, "manage_guild"),
        "interaction_manage_channels": _permission_flag(resolved, "manage_channels"),
        "member_administrator": _permission_flag(member_permissions, "administrator"),
        "member_manage_guild": _permission_flag(member_permissions, "manage_guild"),
        "member_manage_channels": _permission_flag(member_permissions, "manage_channels"),
        "user_type": type(user).__name__ if user is not None else "None",
    }


__all__ = [
    "interaction_authority_snapshot",
    "interaction_has_administrator_authority",
    "interaction_has_manage_guild_authority",
    "interaction_has_channel_management_authority",
    "interaction_is_actual_guild_owner",
    "is_actual_guild_owner",
]
