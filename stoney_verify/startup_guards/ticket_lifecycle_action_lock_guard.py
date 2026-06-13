from __future__ import annotations

"""Add front-door busy checks for ticket close/reopen/delete controls.

The underlying transcript/ticket lifecycle code already owns the real close,
reopen, and delete mutations. This guard improves the interaction layer: if one
staff member starts a lifecycle action, duplicate button clicks get a clear busy
reply instead of waiting behind the lock or making Discord show a stale failure.
"""

from typing import Any, Callable, Optional

import discord

_ACTION_LOCKS = {
    "sv:ticket:close": "_CLOSE_ACTION_LOCKS",
    "sv:ticket:confirm_close": "_CLOSE_ACTION_LOCKS",
    "sv:ticket:reopen": "_REOPEN_ACTION_LOCKS",
    "sv:ticket:delete": "_DELETE_ACTION_LOCKS",
    "sv:ticket:delete_open": "_DELETE_ACTION_LOCKS",
}

_BUSY_TEXT = {
    "sv:ticket:close": "⏳ Close is already running for this ticket. Try again in a few seconds.",
    "sv:ticket:confirm_close": "⏳ Close is already running for this ticket. Try again in a few seconds.",
    "sv:ticket:reopen": "⏳ Reopen is already running for this ticket. Try again in a few seconds.",
    "sv:ticket:delete": "⏳ Delete is already running for this ticket. Try again in a few seconds.",
    "sv:ticket:delete_open": "⏳ Delete is already running for this ticket. Try again in a few seconds.",
}


def _log(message: str) -> None:
    try:
        print(f"✅ ticket_lifecycle_action_lock_guard: {message}")
    except Exception:
        pass


def _warn(message: str) -> None:
    try:
        print(f"⚠️ ticket_lifecycle_action_lock_guard: {message}")
    except Exception:
        pass


def _channel_id(interaction: discord.Interaction) -> int:
    try:
        channel = getattr(interaction, "channel", None)
        return int(getattr(channel, "id", 0) or 0) if isinstance(channel, discord.TextChannel) else 0
    except Exception:
        return 0


def _lock_for_action(tx: Any, custom_id: str, channel_id: int) -> Optional[Any]:
    if channel_id <= 0:
        return None
    container_name = _ACTION_LOCKS.get(custom_id)
    if not container_name:
        return None
    try:
        container = getattr(tx, container_name, None)
        lock_for = getattr(tx, "_lock_for", None)
        if isinstance(container, dict) and callable(lock_for):
            return lock_for(container, channel_id)
    except Exception:
        return None
    return None


async def _reply_busy(tx: Any, interaction: discord.Interaction, custom_id: str) -> None:
    try:
        await tx._reply_ephemeral(interaction, _BUSY_TEXT.get(custom_id, "⏳ That ticket action is already running."))
    except Exception:
        try:
            if getattr(interaction, "response", None) is not None and not interaction.response.is_done():
                await interaction.response.send_message(_BUSY_TEXT.get(custom_id, "⏳ That ticket action is already running."), ephemeral=True)
            else:
                await interaction.followup.send(_BUSY_TEXT.get(custom_id, "⏳ That ticket action is already running."), ephemeral=True)
        except Exception:
            pass


def _wrap_button_callback(tx: Any, item: Any, custom_id: str) -> None:
    original = getattr(item, "callback", None)
    if not callable(original) or getattr(original, "_ticket_lifecycle_action_lock_wrapped", False):
        return

    async def guarded(interaction: discord.Interaction) -> None:
        cid = _channel_id(interaction)
        lock = _lock_for_action(tx, custom_id, cid)
        try:
            if lock is not None and lock.locked():
                return await _reply_busy(tx, interaction, custom_id)
        except Exception:
            pass
        return await original(interaction)

    try:
        setattr(guarded, "_ticket_lifecycle_action_lock_wrapped", True)
        item.callback = guarded
    except Exception as exc:
        _warn(f"could not wrap {custom_id}: {exc!r}")


def _patch_view_init(tx: Any, cls: Any, label: str) -> bool:
    original_init = getattr(cls, "__init__", None)
    if not callable(original_init):
        _warn(f"{label}.__init__ unavailable")
        return False
    if getattr(original_init, "_ticket_lifecycle_action_lock_wrapped", False):
        return True

    def patched_init(self: Any, *args: Any, **kwargs: Any) -> None:
        original_init(self, *args, **kwargs)
        try:
            for item in getattr(self, "children", []) or []:
                custom_id = str(getattr(item, "custom_id", "") or "")
                if custom_id in _ACTION_LOCKS:
                    _wrap_button_callback(tx, item, custom_id)
        except Exception as exc:
            _warn(f"{label} child callback patch failed: {exc!r}")

    try:
        setattr(patched_init, "_ticket_lifecycle_action_lock_wrapped", True)
        cls.__init__ = patched_init
        return True
    except Exception as exc:
        _warn(f"could not patch {label}: {exc!r}")
        return False


def apply() -> bool:
    try:
        from .. import transcripts as tx
    except Exception as exc:
        _warn(f"could not import transcripts: {exc!r}")
        return False

    if getattr(tx, "_TICKET_LIFECYCLE_ACTION_LOCK_GUARD_APPLIED", False):
        return True

    ok = True
    for cls_name in ("TicketOpenActionsView", "StaffClosedTicketView", "ConfirmCloseTicketView"):
        cls = getattr(tx, cls_name, None)
        if cls is None:
            _warn(f"{cls_name} unavailable")
            ok = False
            continue
        ok = _patch_view_init(tx, cls, cls_name) and ok

    try:
        tx._TICKET_LIFECYCLE_ACTION_LOCK_GUARD_APPLIED = bool(ok)
        if ok:
            _log("patched close/reopen/delete front-door busy checks")
        return bool(ok)
    except Exception as exc:
        _warn(f"guard flag failed: {exc!r}")
        return False


apply()

__all__ = ["apply"]
