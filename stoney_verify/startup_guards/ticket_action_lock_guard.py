from __future__ import annotations

"""Add per-ticket operation safety around ticket action controls.

The ticket channel buttons/select actions already call the canonical service
functions, but their UI callbacks can be double-clicked or hit by multiple staff
at once. This guard routes ticket controls through the shared operation queue
and keeps a local fallback lock so duplicate messages/state races do not happen.
"""

import asyncio
import json
import time
from typing import Any, Dict, Tuple

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
            await interaction.followup.send(content, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())
        else:
            await interaction.response.send_message(content, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())
    except Exception:
        pass


def _fingerprint(value: Any) -> str:
    try:
        return json.dumps(value, sort_keys=True, default=str, separators=(",", ":"))[:500]
    except Exception:
        return str(value)[:500]


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


async def _fallback_locked(interaction: discord.Interaction, original: Any, friendly_name: str, args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
    _prune_recent()
    recent_key = _recent_key(interaction, friendly_name)
    now = time.monotonic()

    if _RECENT.get(recent_key, 0.0) > now:
        return await _safe_ephemeral(interaction, f"That **{friendly_name}** action was just handled. Blocked the duplicate click.")

    key = _lock_key(interaction, friendly_name)
    lock = _LOCKS.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _LOCKS[key] = lock

    if lock.locked():
        return await _safe_ephemeral(interaction, f"A **{friendly_name}** action is already running for this ticket. Try again in a moment.")

    async with lock:
        _RECENT[recent_key] = time.monotonic() + _RECENT_SECONDS
        return await original(interaction, *args, **kwargs)


def _wrap_action(panel_mod: Any, action_name: str, friendly_name: str) -> bool:
    original = getattr(panel_mod, action_name, None)
    if not callable(original) or getattr(original, "_ticket_action_lock_wrapped", False):
        return False

    async def wrapper(interaction: discord.Interaction, *args: Any, **kwargs: Any):
        _guild_id, channel_id, _user_id = _ids(interaction)
        try:
            from ..operation_queue import run_interaction_exclusive

            return await run_interaction_exclusive(
                interaction=interaction,
                operation_type=f"ticket_{friendly_name.replace('-', '_')}",
                action_label=f"ticket {friendly_name}",
                fingerprint={"channel_id": channel_id, "action": friendly_name, "args": _fingerprint(args), "kwargs": _fingerprint(kwargs)},
                risk_level="moderate",
                source="discord_command",
                concurrency_class="ticket_channel_mutation",
                concurrency_key=f"channel:{channel_id or 'unknown'}:{friendly_name}",
                timeout_seconds=180.0,
                factory=lambda: original(interaction, *args, **kwargs),
            )
        except Exception as e:
            _warn(f"operation queue unavailable for ticket {friendly_name}; using fallback lock: {e!r}")
            return await _fallback_locked(interaction, original, friendly_name, args, kwargs)

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
        _log(f"wrapped {wrapped} ticket actions with per-ticket queue locks")
        return True
    except Exception:
        return wrapped > 0


apply()

try:
    from . import verification_operation_queue_guard as _verification_operation_queue_guard  # noqa: F401
except Exception as e:
    _warn(f"verification operation queue guard could not load from ticket guard: {e!r}")

__all__ = ["apply"]
