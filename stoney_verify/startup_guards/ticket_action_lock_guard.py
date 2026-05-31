from __future__ import annotations

"""Add per-ticket interaction locks around ticket action controls.

The ticket channel buttons/select actions already call the canonical service
functions, but their UI callbacks can be double-clicked or hit by multiple staff
at once. This guard serializes those actions per channel/action and returns a
clear ephemeral message instead of letting duplicate messages/state races happen.
"""

import asyncio
import time
from typing import Any, Awaitable, Callable, Dict, Tuple

import discord

_LOCKS: Dict[Tuple[int, int, str], asyncio.Lock] = {}
_RECENT: Dict[Tuple[int, int, str, int], float] = {}
_RECENT_SECONDS = 2.5


def _log(message: str) -> None:
    try:
        print(f"✅ ticket_action_lock_guard: {message}")
    except Exception:
        pass


def _warn(message: str) -> None:
    try:
        print(f"⚠️ ticket_action_lock_guard: {message}")
    except Exception:
        pass


def _ids(interaction: discord.Interaction) -> tuple[int, int, int]:
    try:
        guild_id = int(getattr(getattr(interaction, "guild", None), "id", 0) or 0)
    except Exception:
        guild_id = 0
    try:
        channel_id = int(getattr(getattr(interaction, "channel", None), "id", 0) or 0)
    except Exception:
        channel_id = 0
    try:
        user_id = int(getattr(getattr(interaction, "user", None), "id", 0) or 0)
    except Exception:
        user_id = 0
    return guild_id, channel_id, user_id


async def _safe_ephemeral(interaction: discord.Interaction, content: str) -> None:
    try:
        if interaction.response.is_done():
            await interaction.followup.send(
                content,
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        else:
            await interaction.response.send_message(
                content,
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )
    except Exception:
        pass


def _prune_recent() -> None:
    try:
        now = time.monotonic()
        expired = [key for key, until in _RECENT.items() if until <= now]
        for key in expired[:200]:
            _RECENT.pop(key, None)
    except Exception:
        pass


def _lock_key(interaction: discord.Interaction, action: str) -> tuple[int, int, str]:
    guild_id, channel_id, _user_id = _ids(interaction)
    return (guild_id, channel_id, str(action))


def _recent_key(interaction: discord.Interaction, action: str) -> tuple[int, int, str, int]:
    guild_id, channel_id, user_id = _ids(interaction)
    return (guild_id, channel_id, str(action), user_id)


def _wrap_action(panel_mod: Any, action_name: str, friendly_name: str) -> bool:
    original = getattr(panel_mod, action_name, None)
    if not callable(original) or getattr(original, "_ticket_action_lock_wrapped", False):
        return False

    async def wrapper(interaction: discord.Interaction, *args: Any, **kwargs: Any):
        _prune_recent()
        recent_key = _recent_key(interaction, friendly_name)
        now = time.monotonic()

        if _RECENT.get(recent_key, 0.0) > now:
            return await _safe_ephemeral(
                interaction,
                f"That **{friendly_name}** action was just handled. Blocked the duplicate click.",
            )

        key = _lock_key(interaction, friendly_name)
        lock = _LOCKS.get(key)
        if lock is None:
            lock = asyncio.Lock()
            _LOCKS[key] = lock

        if lock.locked():
            return await _safe_ephemeral(
                interaction,
                f"A **{friendly_name}** action is already running for this ticket. Try again in a moment.",
            )

        async with lock:
            _RECENT[recent_key] = time.monotonic() + _RECENT_SECONDS
            return await original(interaction, *args, **kwargs)

    setattr(wrapper, "_ticket_action_lock_wrapped", True)
    setattr(panel_mod, action_name, wrapper)
    return True


def apply() -> bool:
    try:
        from ..tickets_new import panel as panel_mod
    except Exception as e:
        _warn(f"could not import tickets_new.panel: {e!r}")
        return False

    if getattr(panel_mod, "_TICKET_ACTION_LOCK_GUARD_APPLIED", False):
        return True

    wrapped = 0
    targets = (
        ("_action_claim", "claim"),
        ("_action_unclaim", "unclaim"),
        ("_action_transfer", "transfer"),
        ("_action_set_priority", "priority"),
        ("_action_add_note", "add-note"),
        ("_action_view_notes", "view-notes"),
        ("_action_list_macros", "list-macros"),
        ("_action_use_macro", "send-macro"),
        ("_action_close", "close"),
        ("_action_ticket_info", "ticket-info"),
    )

    for action_name, friendly_name in targets:
        try:
            if _wrap_action(panel_mod, action_name, friendly_name):
                wrapped += 1
        except Exception as e:
            _warn(f"failed wrapping {action_name}: {e!r}")

    try:
        setattr(panel_mod, "_TICKET_ACTION_LOCK_GUARD_APPLIED", True)
        _log(f"wrapped {wrapped} ticket actions with per-ticket locks")
        return True
    except Exception:
        return wrapped > 0


apply()

__all__ = ["apply"]
