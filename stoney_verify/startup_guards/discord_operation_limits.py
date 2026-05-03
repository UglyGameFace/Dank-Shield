from __future__ import annotations

"""
Discord operation throttling guard.

This guard patches high-risk discord.py write operations so ticket creation,
cleanup storms, role changes, and moderation actions are bounded globally and
per guild even before every feature is permanently refactored.

It intentionally does not replace discord.py's internal HTTP buckets. It reduces
our own burst pressure before calls reach discord.py.

Patched operation groups:
- Guild.create_text_channel / create_category / create_role
- TextChannel.edit / delete
- CategoryChannel.edit / delete
- Role.edit / delete
- Member.add_roles / remove_roles / edit / timeout / kick / ban

Safety rules:
- Patch is idempotent.
- Uses *args/**kwargs and returns the original method result.
- Does not swallow original exceptions.
- Uses per-guild limit labels so one noisy guild cannot monopolize all writes.
"""

import functools
from typing import Any, Callable, Optional

import discord

from ..runtime_limits import discord_guild_limit, jitter_sleep

_PATCHED = False
_ORIGINALS: dict[str, Callable[..., Any]] = {}


def _log(message: str) -> None:
    try:
        print(f"🛡️ discord_operation_limits {message}")
    except Exception:
        pass


def _warn(message: str) -> None:
    try:
        print(f"⚠️ discord_operation_limits {message}")
    except Exception:
        pass


def _guild_id_from_obj(obj: Any) -> str:
    try:
        if isinstance(obj, discord.Guild):
            return str(int(obj.id))
    except Exception:
        pass

    try:
        guild = getattr(obj, "guild", None)
        if guild is not None:
            gid = getattr(guild, "id", None)
            if gid:
                return str(int(gid))
    except Exception:
        pass

    return "0"


def _patch_async_method(cls: Any, method_name: str, *, label: str, jitter_seconds: float = 0.0) -> bool:
    key = f"{getattr(cls, '__name__', str(cls))}.{method_name}"
    if key in _ORIGINALS:
        return False

    try:
        original = getattr(cls, method_name, None)
        if original is None or not callable(original):
            return False
    except Exception:
        return False

    _ORIGINALS[key] = original

    @functools.wraps(original)
    async def _wrapped(self, *args, **kwargs):
        guild_id = _guild_id_from_obj(self)
        if jitter_seconds > 0:
            await jitter_sleep(base_seconds=0.0, max_jitter_seconds=jitter_seconds, guild_id=guild_id)
        async with discord_guild_limit(guild_id, label=label):
            return await original(self, *args, **kwargs)

    try:
        setattr(cls, method_name, _wrapped)
        return True
    except Exception as e:
        _warn(f"failed patching {key}: {repr(e)}")
        _ORIGINALS.pop(key, None)
        return False


def install_discord_operation_limits() -> None:
    global _PATCHED
    if _PATCHED:
        return
    _PATCHED = True

    patched = 0

    # Ticket/category/role setup pressure.
    for method_name, label in (
        ("create_text_channel", "channel_create"),
        ("create_category", "category_create"),
        ("create_role", "role_create"),
    ):
        if _patch_async_method(discord.Guild, method_name, label=label, jitter_seconds=0.20):
            patched += 1

    # Ticket lifecycle pressure: move/archive/delete/permission repair edits.
    for cls in (discord.TextChannel, discord.CategoryChannel):
        for method_name, label in (
            ("edit", "channel_edit"),
            ("delete", "channel_delete"),
        ):
            if _patch_async_method(cls, method_name, label=label, jitter_seconds=0.15):
                patched += 1

    # Role edits/deletes during setup/cleanup.
    for method_name, label in (
        ("edit", "role_edit"),
        ("delete", "role_delete"),
    ):
        if _patch_async_method(discord.Role, method_name, label=label, jitter_seconds=0.15):
            patched += 1

    # Member writes: verification role changes and moderation actions.
    for method_name, label in (
        ("add_roles", "member_roles"),
        ("remove_roles", "member_roles"),
        ("edit", "member_edit"),
        ("timeout", "member_timeout"),
        ("kick", "member_kick"),
        ("ban", "member_ban"),
    ):
        if _patch_async_method(discord.Member, method_name, label=label, jitter_seconds=0.10):
            patched += 1

    _log(f"patched high-risk Discord operations count={patched}")


install_discord_operation_limits()


__all__ = ["install_discord_operation_limits"]
