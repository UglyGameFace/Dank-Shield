from __future__ import annotations

"""Refresh the visible open-ticket status card after staff ticket actions.

The richer open controls card shows status, priority, transcript state, and who
claimed the ticket.  This guard makes the card refresh after claim, unclaim,
transfer, and priority changes so staff do not see stale ownership/status data.
"""

from typing import Any, Optional

import discord


def _log(message: str) -> None:
    try:
        print(f"✅ ticket_action_controls_refresh_guard: {message}")
    except Exception:
        pass


def _warn(message: str) -> None:
    try:
        print(f"⚠️ ticket_action_controls_refresh_guard: {message}")
    except Exception:
        pass


def _ticket_channel_from_interaction(interaction: Any) -> Optional[discord.TextChannel]:
    try:
        channel = getattr(interaction, "channel", None)
        return channel if isinstance(channel, discord.TextChannel) else None
    except Exception:
        return None


async def _refresh_open_controls(channel: Optional[discord.TextChannel], *, reason: str) -> None:
    if not isinstance(channel, discord.TextChannel):
        return
    try:
        from .. import transcripts as tx
        refresher = getattr(tx, "post_or_replace_open_ticket_controls", None)
        if callable(refresher):
            await refresher(channel)
            return
    except Exception as exc:
        _warn(f"open-controls refresh failed channel={getattr(channel, 'id', 0)} reason={reason}: {type(exc).__name__}: {exc!r}")


def _wrap_action(panel_mod: Any, name: str) -> bool:
    original = getattr(panel_mod, name, None)
    if not callable(original):
        _warn(f"{name} is unavailable")
        return False
    if getattr(original, "_ticket_controls_refresh_wrapped", False):
        return True

    async def wrapped(interaction: discord.Interaction) -> None:
        channel = _ticket_channel_from_interaction(interaction)
        try:
            return await original(interaction)
        finally:
            await _refresh_open_controls(channel, reason=name)

    try:
        setattr(wrapped, "_ticket_controls_refresh_wrapped", True)
        setattr(panel_mod, name, wrapped)
        return True
    except Exception as exc:
        _warn(f"could not wrap {name}: {exc!r}")
        return False


def _wrap_modal_on_submit(modal_cls: Any, label: str) -> bool:
    original = getattr(modal_cls, "on_submit", None)
    if not callable(original):
        _warn(f"{label}.on_submit is unavailable")
        return False
    if getattr(original, "_ticket_controls_refresh_wrapped", False):
        return True

    async def wrapped(self: Any, interaction: discord.Interaction) -> None:
        channel = _ticket_channel_from_interaction(interaction)
        try:
            return await original(self, interaction)
        finally:
            await _refresh_open_controls(channel, reason=label)

    try:
        setattr(wrapped, "_ticket_controls_refresh_wrapped", True)
        setattr(modal_cls, "on_submit", wrapped)
        return True
    except Exception as exc:
        _warn(f"could not wrap {label}.on_submit: {exc!r}")
        return False


def apply() -> bool:
    try:
        from ..tickets_new import panel as panel_mod
    except Exception as exc:
        _warn(f"could not import tickets_new.panel: {exc!r}")
        return False

    if getattr(panel_mod, "_TICKET_ACTION_CONTROLS_REFRESH_GUARD_APPLIED", False):
        return True

    ok = True
    ok = _wrap_action(panel_mod, "_action_claim") and ok
    ok = _wrap_action(panel_mod, "_action_unclaim") and ok
    transfer_modal = getattr(panel_mod, "TransferTicketModal", None)
    priority_modal = getattr(panel_mod, "SetPriorityModal", None)
    if transfer_modal is not None:
        ok = _wrap_modal_on_submit(transfer_modal, "TransferTicketModal") and ok
    if priority_modal is not None:
        ok = _wrap_modal_on_submit(priority_modal, "SetPriorityModal") and ok

    try:
        panel_mod._TICKET_ACTION_CONTROLS_REFRESH_GUARD_APPLIED = bool(ok)
        if ok:
            _log("patched ticket action controls refresh")
        return bool(ok)
    except Exception as exc:
        _warn(f"guard flag failed: {exc!r}")
        return False


apply()

__all__ = ["apply"]
