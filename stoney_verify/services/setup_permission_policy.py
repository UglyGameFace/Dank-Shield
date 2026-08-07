from __future__ import annotations

"""Central, read-only setup permission policy for Dank Shield."""

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


def _locked_voice_member_overwrite(*, can_chat: bool = False) -> discord.PermissionOverwrite:
    return discord.PermissionOverwrite(
        view_channel=False,
        connect=False,
        speak=False,
        stream=False,
        use_voice_activation=False,
        move_members=False,
        send_messages=can_chat,
        read_message_history=can_chat,
    )


def vc_verification_overwrites(
    guild: discord.Guild,
    *,
    staff_role: Optional[discord.Role],
    control_role: Optional[discord.Role],
    unverified_role: Optional[discord.Role],
    verified_role: Optional[discord.Role],
    resident_role: Optional[discord.Role],
) -> dict[object, discord.PermissionOverwrite]:
    """Expected session-locked Voice Verify room overwrites.

    No broad member, staff, or control role can see or enter the room. The
    runtime grants an exact per-member overwrite only to the active requester
    and assigned staff member, then removes it when the session ends.
    """

    ow: dict[object, discord.PermissionOverwrite] = {
        guild.default_role: _locked_voice_member_overwrite(),
    }
    me = bot_member(guild)
    if me:
        ow[me] = discord.PermissionOverwrite(
            view_channel=True,
            connect=True,
            speak=True,
            stream=True,
            use_voice_activation=True,
            move_members=True,
            manage_channels=True,
            send_messages=True,
            read_message_history=True,
        )
    for role in (unverified_role, verified_role, resident_role):
        if role and not role.is_default():
            ow[role] = _locked_voice_member_overwrite()
    for role in (staff_role, control_role):
        if role and not role.is_default():
            ow[role] = _locked_voice_member_overwrite(can_chat=True)
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
