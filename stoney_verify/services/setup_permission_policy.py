from __future__ import annotations

"""Central setup permission policy for Dank Shield.

This module is deliberately pure/read-only. It computes expected overwrites and
health policy facts, but it never writes to Discord. Mutation callers must use
operation_queue/run_interaction_exclusive so one guild's repair cannot freeze
another guild's commands.
"""

from typing import Any, Optional

import discord


def bot_member(guild: discord.Guild) -> Optional[discord.Member]:
    try:
        return guild.me if isinstance(guild.me, discord.Member) else None
    except Exception:
        return None


def role_label(role: Any) -> str:
    try:
        if getattr(role, "is_default", lambda: False)():
            return "@everyone"
    except Exception:
        pass
    try:
        name = str(getattr(role, "name", "") or "").strip()
        if name == "@everyone":
            return "@everyone"
        if name:
            return f"@{name}"
    except Exception:
        pass
    return "role"


def vc_verification_overwrites(
    guild: discord.Guild,
    *,
    staff_role: Optional[discord.Role],
    control_role: Optional[discord.Role],
    unverified_role: Optional[discord.Role],
    verified_role: Optional[discord.Role],
    resident_role: Optional[discord.Role],
) -> dict[object, discord.PermissionOverwrite]:
    """Expected staff-controlled VC verification overwrites.

    Public/onboarding users may see that the VC flow exists, but they cannot
    connect freely. Dank Shield/staff grant per-member temporary access during
    the active VC verification session.
    """

    ow: dict[object, discord.PermissionOverwrite] = {
        guild.default_role: discord.PermissionOverwrite(view_channel=True, connect=False, speak=False),
    }
    me = bot_member(guild)
    if me:
        ow[me] = discord.PermissionOverwrite(
            view_channel=True,
            connect=True,
            speak=True,
            move_members=True,
            manage_channels=True,
        )
    for role in (unverified_role, verified_role, resident_role):
        if role and not role.is_default():
            ow[role] = discord.PermissionOverwrite(view_channel=True, connect=False, speak=False)
    for role in (staff_role, control_role):
        if role and not role.is_default():
            ow[role] = discord.PermissionOverwrite(view_channel=True, connect=True, speak=True, move_members=True)
    return ow


def vc_connect_is_blocker(perms: Any) -> bool:
    try:
        return bool(getattr(perms, "connect", False))
    except Exception:
        return True


def vc_view_only_is_blocker(perms: Any) -> bool:
    return False


__all__ = [
    "bot_member",
    "role_label",
    "vc_verification_overwrites",
    "vc_connect_is_blocker",
    "vc_view_only_is_blocker",
]
