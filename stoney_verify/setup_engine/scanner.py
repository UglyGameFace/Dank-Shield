from __future__ import annotations

from typing import Any

import discord

from .models import LiveTarget


def target_id(obj: Any) -> int:
    try:
        return int(getattr(obj, "id", 0) or 0)
    except Exception:
        return 0


def target_name(obj: Any) -> str:
    try:
        return str(getattr(obj, "name", "") or "")
    except Exception:
        return ""


def target_label(obj: Any) -> str:
    try:
        return str(getattr(obj, "mention", None) or getattr(obj, "name", "unknown"))
    except Exception:
        return "unknown"


def parent_id(obj: Any) -> int:
    try:
        parent = getattr(obj, "category", None)
        return target_id(parent)
    except Exception:
        return 0


def parent_name(obj: Any) -> str:
    try:
        parent = getattr(obj, "category", None)
        return target_name(parent)
    except Exception:
        return ""


def is_voice(obj: Any) -> bool:
    try:
        stage = getattr(discord, "StageChannel", None)
        return isinstance(obj, discord.VoiceChannel) or (stage is not None and isinstance(obj, stage))
    except Exception:
        return False


def get_channel(guild: discord.Guild, channel_id: int) -> Any:
    try:
        return guild.get_channel(int(channel_id or 0)) if int(channel_id or 0) > 0 else None
    except Exception:
        return None


def get_role(guild: discord.Guild, role_id: int) -> discord.Role | None:
    try:
        role = guild.get_role(int(role_id or 0)) if int(role_id or 0) > 0 else None
        return role if isinstance(role, discord.Role) else None
    except Exception:
        return None


def bot_member(guild: discord.Guild) -> discord.Member | None:
    try:
        me = getattr(guild, "me", None)
        if isinstance(me, discord.Member):
            return me
    except Exception:
        pass
    try:
        user = getattr(getattr(guild, "_state", None), "user", None)
        if user is not None:
            member = guild.get_member(int(user.id))
            return member if isinstance(member, discord.Member) else None
    except Exception:
        pass
    return None


def can_see(obj: Any, role: discord.Role | discord.Member | None) -> bool:
    try:
        return bool(role and obj and obj.permissions_for(role).view_channel)
    except Exception:
        return False


def can_send(obj: Any, role: discord.Role | discord.Member | None) -> bool:
    try:
        return bool(role and obj and getattr(obj.permissions_for(role), "send_messages", False))
    except Exception:
        return False


def permission(obj: Any, role: discord.Role | discord.Member | None, name: str) -> bool:
    try:
        return bool(role and obj and getattr(obj.permissions_for(role), name, False))
    except Exception:
        return False


def live_target(obj: Any) -> LiveTarget:
    return LiveTarget(
        id=target_id(obj),
        name=target_name(obj),
        kind="category" if isinstance(obj, discord.CategoryChannel) else "voice" if is_voice(obj) else "text" if isinstance(obj, discord.TextChannel) else "channel",
        mention=target_label(obj),
        parent_id=parent_id(obj),
        parent_name=parent_name(obj),
        is_category=isinstance(obj, discord.CategoryChannel),
        is_text=isinstance(obj, discord.TextChannel),
        is_voice=is_voice(obj),
    )


def all_channel_targets(guild: discord.Guild) -> list[Any]:
    targets: list[Any] = []
    targets.extend(list(getattr(guild, "categories", []) or []))
    targets.extend(list(getattr(guild, "channels", []) or []))
    seen: set[int] = set()
    out: list[Any] = []
    for item in targets:
        tid = target_id(item)
        if tid <= 0 or tid in seen:
            continue
        seen.add(tid)
        out.append(item)
    return out
