from __future__ import annotations

"""Central, read-only setup permission policy for Dank Shield."""

from typing import Any, Optional

import discord


def bot_member(guild: discord.Guild) -> Optional[Any]:
    """Return the guild's bot member without rejecting compatible proxies."""

    try:
        member = guild.me
        return member if member is not None else None
    except Exception:
        return None


def _clone_permissions(value: Any) -> discord.Permissions:
    try:
        return discord.Permissions(int(getattr(value, "value", value) or 0))
    except Exception:
        return discord.Permissions.none()


def _apply_overwrite(base: discord.Permissions, overwrite: discord.PermissionOverwrite) -> None:
    try:
        allow, deny = overwrite.pair()
        base.handle_overwrite(int(allow.value), int(deny.value))
    except Exception:
        pass


def permissions_without_administrator(
    channel: Any,
    member: Any,
) -> Optional[discord.Permissions]:
    """Resolve the member's underlying channel permissions without Admin bypass.

    discord.py returns Permissions.all() before channel overwrites whenever the
    member has Administrator. Emergency Fix Access needs the opposite view: keep
    every ordinary guild-role/channel/member overwrite, but ignore only the
    Administrator short-circuit so already-locked targets remain visible while
    temporary recovery authority is active.
    """

    try:
        if isinstance(channel, discord.Thread):
            parent = getattr(channel, "parent", None)
            if parent is None:
                return None
            channel = parent

        guild = getattr(channel, "guild", None)
        if guild is None:
            return None
        if int(getattr(guild, "owner_id", 0) or 0) == int(getattr(member, "id", 0) or 0):
            return discord.Permissions.all()

        default_role = getattr(guild, "default_role", None)
        if default_role is None:
            return None

        base = _clone_permissions(getattr(default_role, "permissions", None))
        for role in list(getattr(member, "roles", []) or []):
            try:
                base.value |= int(getattr(getattr(role, "permissions", None), "value", 0) or 0)
            except Exception:
                continue

        # Ignore only Administrator itself. All ordinary guild role permissions
        # remain, then Discord's overwrite order is applied below.
        try:
            base.administrator = False
        except Exception:
            pass

        _apply_overwrite(base, channel.overwrites_for(default_role))

        role_denies = discord.Permissions.none()
        role_allows = discord.Permissions.none()
        member_id = int(getattr(member, "id", 0) or 0)
        default_id = int(getattr(default_role, "id", 0) or 0)

        for role in list(getattr(member, "roles", []) or []):
            try:
                if int(getattr(role, "id", 0) or 0) == default_id:
                    continue
                overwrite = channel.overwrites_for(role)
                allow, deny = overwrite.pair()
                role_denies.value |= int(deny.value)
                role_allows.value |= int(allow.value)
            except Exception:
                continue

        base.handle_overwrite(int(role_allows.value), int(role_denies.value))
        try:
            _apply_overwrite(base, channel.overwrites_for(member))
        except Exception:
            pass

        # Mirror the channel visibility/message dependency rules relevant to
        # Diagnostics. These happen after overwrite resolution in discord.py.
        if not bool(getattr(base, "send_messages", False)):
            for name in ("send_tts_messages", "mention_everyone", "embed_links", "attach_files"):
                try:
                    setattr(base, name, False)
                except Exception:
                    pass
        if not bool(getattr(base, "view_channel", False)):
            try:
                base.value &= ~discord.Permissions.all_channel().value
            except Exception:
                pass

        if isinstance(channel, (discord.TextChannel, discord.ForumChannel)):
            try:
                base.value &= ~discord.Permissions.voice().value
            except Exception:
                pass

        return base
    except Exception:
        return None


def bot_channel_permissions(
    channel: Any,
    member: Any,
    *,
    ignore_administrator: bool = False,
) -> Optional[discord.Permissions]:
    """Resolve channel permissions, optionally exposing the non-Admin truth."""

    if ignore_administrator:
        return permissions_without_administrator(channel, member)
    try:
        return channel.permissions_for(member)
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
            manage_roles=True,
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
    "bot_channel_permissions",
    "permissions_without_administrator",
    "role_label",
    "vc_verification_overwrites",
    "vc_connect_is_blocker",
    "vc_view_only_is_blocker",
]
