from __future__ import annotations

"""Refresh the open-ticket status panel after staff state changes.

The richer open-ticket controls show live fields like Claimed By and Priority.
This guard keeps that panel fresh after claim/unclaim/transfer/priority changes
without changing the underlying ticket service behavior.
"""

from typing import Any, Callable

import discord


def _log(message: str) -> None:
    try:
        print(f"✅ ticket_open_controls_refresh_guard: {message}")
    except Exception:
        pass


def _warn(message: str) -> None:
    try:
        print(f"⚠️ ticket_open_controls_refresh_guard: {message}")
    except Exception:
        pass


async def _refresh(channel: Any) -> None:
    try:
        if not isinstance(channel, discord.TextChannel):
            return
        from .. import transcripts as tx
        poster = getattr(tx, "post_or_replace_open_ticket_controls", None)
        if callable(poster):
            await poster(channel)
    except Exception as e:
        _warn(f"open controls refresh failed channel={getattr(channel, 'id', 0)}: {type(e).__name__}: {e}")


def _wrap_action(panel_mod: Any, action_name: str) -> bool:
    original = getattr(panel_mod, action_name, None)
    if not callable(original) or getattr(original, "_open_controls_refresh_wrapped", False):
        return False

    async def wrapper(interaction: discord.Interaction, *args: Any, **kwargs: Any):
        channel = getattr(interaction, "channel", None)
        result = await original(interaction, *args, **kwargs)
        await _refresh(channel)
        return result

    setattr(wrapper, "_open_controls_refresh_wrapped", True)
    setattr(panel_mod, action_name, wrapper)
    return True


def _wrap_modal_submit(modal_cls: type[Any]) -> bool:
    original = getattr(modal_cls, "on_submit", None)
    if not callable(original) or getattr(original, "_open_controls_refresh_wrapped", False):
        return False

    async def wrapper(self: Any, interaction: discord.Interaction, *args: Any, **kwargs: Any):
        channel = getattr(interaction, "channel", None)
        result = await original(self, interaction, *args, **kwargs)
        await _refresh(channel)
        return result

    setattr(wrapper, "_open_controls_refresh_wrapped", True)
    setattr(modal_cls, "on_submit", wrapper)
    return True


def apply() -> bool:
    try:
        from ..tickets_new import panel as panel_mod
    except Exception as e:
        _warn(f"could not import tickets_new.panel: {e!r}")
        return False

    if getattr(panel_mod, "_TICKET_OPEN_CONTROLS_REFRESH_GUARD_APPLIED", False):
        return True

    wrapped = 0
    for action_name in ("_action_claim", "_action_unclaim"):
        try:
            if _wrap_action(panel_mod, action_name):
                wrapped += 1
        except Exception as e:
            _warn(f"failed wrapping {action_name}: {e!r}")

    for cls_name in ("TransferTicketModal", "SetPriorityModal"):
        try:
            cls = getattr(panel_mod, cls_name, None)
            if isinstance(cls, type) and _wrap_modal_submit(cls):
                wrapped += 1
        except Exception as e:
            _warn(f"failed wrapping {cls_name}: {e!r}")

    try:
        setattr(panel_mod, "_TICKET_OPEN_CONTROLS_REFRESH_GUARD_APPLIED", True)
        _log(f"wrapped {wrapped} ticket state-change handlers")
        return True
    except Exception:
        return wrapped > 0


apply()

__all__ = ["apply"]
