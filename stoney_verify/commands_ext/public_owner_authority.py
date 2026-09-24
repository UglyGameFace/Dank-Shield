from __future__ import annotations

"""Canonical Discord guild-owner and manager authority helpers.

Guild ownership is identity truth, not a derived role/permission result. Public
management surfaces must therefore recognize the actual guild owner before they
depend on Member cache shape, configured roles, or resolved guild permissions.
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


def is_actual_guild_owner(user: Any, guild: Any = None) -> bool:
    try:
        resolved_guild = guild if guild is not None else getattr(user, "guild", None)
        user_id = _safe_int(getattr(user, "id", 0), 0)
        owner_id = _safe_int(getattr(resolved_guild, "owner_id", 0), 0)
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


def interaction_has_manage_guild_authority(interaction: Any) -> bool:
    """Owner, Administrator, or Manage Server for public management surfaces."""
    if interaction_is_actual_guild_owner(interaction):
        return True

    try:
        guild = getattr(interaction, "guild", None)
        user = getattr(interaction, "user", None)
        if guild is None or not isinstance(user, discord.Member):
            return False
        permissions = user.guild_permissions
        return bool(
            getattr(permissions, "administrator", False)
            or getattr(permissions, "manage_guild", False)
        )
    except Exception:
        return False


__all__ = [
    "interaction_has_manage_guild_authority",
    "interaction_is_actual_guild_owner",
    "is_actual_guild_owner",
]
