from __future__ import annotations

"""Single-owner runtime guard for the clean public ticket panel.

The clean panel module already owns category loading and durable ticket-number
allocation. This guard must never replace those implementations. Its only jobs
are:

- allow one execution per Discord interaction,
- silently ignore duplicate delivery of that same interaction,
- remove the fallback listener when the persistent view registered correctly,
- retain the fallback only when persistent-view registration failed.

This prevents the persistent callback and the 150 ms compatibility listener
from producing two private category-menu responses for one button press.
"""

import asyncio
import heapq
import time
from typing import Any, Dict

import discord

_INTERACTION_LOCKS: Dict[int, asyncio.Lock] = {}
_INTERACTION_DONE_UNTIL: Dict[int, float] = {}
_INTERACTION_EXPIRY_HEAP: list[tuple[float, int]] = []
_INTERACTION_TTL_SECONDS = 90.0


def _log(message: str) -> None:
    try:
        print(f"✅ public_ticket_panel_clean_hardening: {message}")
    except Exception:
        pass


def _warn(message: str) -> None:
    try:
        print(f"⚠️ public_ticket_panel_clean_hardening: {message}")
    except Exception:
        pass


def _interaction_key(interaction: discord.Interaction) -> int:
    try:
        interaction_id = int(getattr(interaction, "id", 0) or 0)
    except Exception:
        interaction_id = 0
    return interaction_id if interaction_id > 0 else id(interaction)


def _record_interaction_done(key: int, expires_at: float) -> None:
    """Record one completion and index it for proportional expiry cleanup."""
    _INTERACTION_DONE_UNTIL[key] = expires_at
    heapq.heappush(_INTERACTION_EXPIRY_HEAP, (expires_at, key))


def _prune_interactions() -> None:
    """Remove expired completions without scanning every live interaction."""
    now = time.monotonic()
    while _INTERACTION_EXPIRY_HEAP and _INTERACTION_EXPIRY_HEAP[0][0] <= now:
        expires_at, key = heapq.heappop(_INTERACTION_EXPIRY_HEAP)

        # A newer completion for the same key leaves an older heap entry behind.
        # Ignore that stale entry so it cannot remove the renewed TTL.
        if _INTERACTION_DONE_UNTIL.get(key) != expires_at:
            continue

        _INTERACTION_DONE_UNTIL.pop(key, None)
        lock = _INTERACTION_LOCKS.get(key)
        if lock is None or not lock.locked():
            _INTERACTION_LOCKS.pop(key, None)


async def _handle_once(original_handler: Any, interaction: discord.Interaction) -> None:
    """Run the canonical handler once for one Discord interaction ID.

    Completion is recorded on every exit so a handler exception or task
    cancellation cannot leave an immortal lock-map entry. The same interaction
    remains deduplicated for the short TTL even after failure, while a genuinely
    new button press receives a new interaction ID and can run immediately.
    """
    _prune_interactions()
    key = _interaction_key(interaction)
    now = time.monotonic()

    if _INTERACTION_DONE_UNTIL.get(key, 0.0) > now:
        return

    lock = _INTERACTION_LOCKS.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _INTERACTION_LOCKS[key] = lock

    # The persistent callback owns the first delivery. A fallback/listener copy
    # arriving while that callback is in flight must return silently rather than
    # sending another menu or a confusing duplicate-menu notice.
    if lock.locked():
        return

    async with lock:
        now = time.monotonic()
        if _INTERACTION_DONE_UNTIL.get(key, 0.0) > now:
            return
        try:
            await original_handler(interaction)
        finally:
            # asyncio.Lock releases automatically when this block exits. Recording
            # completion here makes the unlocked entry eligible for TTL pruning
            # even when the handler raises or the task is cancelled.
            _record_interaction_done(
                key,
                time.monotonic() + _INTERACTION_TTL_SECONDS,
            )


def _remove_redundant_fallback(panel_mod: Any, bot: Any) -> bool:
    """Keep the fallback only when the persistent view could not register."""
    if not bool(getattr(panel_mod, "_PANEL_VIEW_REGISTERED", False)):
        return False

    listener = getattr(panel_mod, "_component_fallback_listener", None)
    if not callable(listener):
        return False

    try:
        bot.remove_listener(listener, "on_interaction")
    except Exception as exc:
        _warn(f"could not remove redundant fallback listener: {exc!r}")
        return False

    # Leave the registration flag true after removal so repeated command-module
    # registration cannot add the redundant listener back into the same process.
    try:
        setattr(panel_mod, "_PANEL_FALLBACK_LISTENER_REGISTERED", True)
        setattr(panel_mod, "_PANEL_FALLBACK_SUPPRESSED_BY_VIEW", True)
    except Exception:
        pass
    _log("persistent view owns Create Ticket; redundant fallback listener removed")
    return True


def _register_single_owner(
    panel_mod: Any,
    original_register: Any,
    bot: Any,
    tree: Any,
) -> None:
    original_register(bot, tree)
    _remove_redundant_fallback(panel_mod, bot)


def apply() -> bool:
    try:
        from ..commands_ext import public_ticket_panel_clean as panel_mod
    except Exception as exc:
        _warn(f"could not import public_ticket_panel_clean: {exc!r}")
        return False

    if getattr(panel_mod, "_PUBLIC_TICKET_PANEL_CLEAN_HARDENED", False):
        return True

    original_handler = getattr(panel_mod, "_handle_panel_button", None)
    original_register = getattr(panel_mod, "register_public_ticket_panel_clean", None)
    if not callable(original_handler) or not callable(original_register):
        _warn("clean panel handler/register function is unavailable")
        return False

    try:
        panel_mod._handle_panel_button = (
            lambda interaction: _handle_once(original_handler, interaction)
        )
        panel_mod.register_public_ticket_panel_clean = (
            lambda bot, tree: _register_single_owner(
                panel_mod,
                original_register,
                bot,
                tree,
            )
        )
        setattr(panel_mod, "_PUBLIC_TICKET_PANEL_CLEAN_HARDENED", True)
        _log(
            "single-interaction owner active; native categories and durable "
            "ticket allocator remain untouched"
        )
        return True
    except Exception as exc:
        _warn(f"single-owner install failed: {exc!r}")
        return False


apply()

__all__ = [
    "apply",
]
