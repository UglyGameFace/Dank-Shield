from __future__ import annotations

"""Add front-door busy/defer handling for manual transcript posting.

Manual transcript generation can take long enough for Discord interactions to
feel flaky. The transcript service already has the real per-channel lock; this
guard adds an immediate UI-level busy check and defer so repeated clicks do not
queue silently or show interaction failures.
"""

from typing import Any, Optional

import discord

_TRANSCRIPT_BUTTON_ID = "sv:ticket:transcript"
_BUSY_TEXT = "⏳ Transcript is already being generated for this ticket. Try again in a few seconds."
_EXISTS_TEXT = "ℹ️ Transcript already exists for this ticket."


def _log(message: str) -> None:
    try:
        print(f"✅ ticket_transcript_post_busy_guard: {message}")
    except Exception:
        pass


def _warn(message: str) -> None:
    try:
        print(f"⚠️ ticket_transcript_post_busy_guard: {message}")
    except Exception:
        pass


def _ticket_channel(interaction: discord.Interaction) -> Optional[discord.TextChannel]:
    try:
        channel = getattr(interaction, "channel", None)
        return channel if isinstance(channel, discord.TextChannel) else None
    except Exception:
        return None


def _transcript_lock(tx: Any, channel_id: int) -> Optional[Any]:
    try:
        container = getattr(tx, "_TRANSCRIPT_POST_LOCKS", None)
        lock_for = getattr(tx, "_lock_for", None)
        if isinstance(container, dict) and callable(lock_for) and int(channel_id) > 0:
            return lock_for(container, int(channel_id))
    except Exception:
        return None
    return None


async def _reply(tx: Any, interaction: discord.Interaction, content: str) -> None:
    try:
        await tx._reply_ephemeral(interaction, content)
        return
    except Exception:
        pass
    try:
        if not interaction.response.is_done():
            await interaction.response.send_message(content, ephemeral=True)
        else:
            await interaction.followup.send(content, ephemeral=True)
    except Exception:
        pass


async def _defer(tx: Any, interaction: discord.Interaction) -> None:
    try:
        await tx._safe_defer_ephemeral(interaction)
    except Exception:
        try:
            if not interaction.response.is_done():
                await interaction.response.defer(ephemeral=True)
        except Exception:
            pass


async def _has_transcript(tx: Any, channel_id: int) -> bool:
    try:
        checker = getattr(tx, "_ticket_has_transcript", None)
        if callable(checker):
            return bool(await checker(int(channel_id)))
    except Exception:
        return False
    return False


def _wrap_transcript_button(tx: Any, item: Any) -> None:
    original = getattr(item, "callback", None)
    if not callable(original) or getattr(original, "_ticket_transcript_busy_wrapped", False):
        return

    async def guarded(interaction: discord.Interaction) -> None:
        channel = _ticket_channel(interaction)
        if channel is None:
            return await original(interaction)

        lock = _transcript_lock(tx, channel.id)
        try:
            if lock is not None and lock.locked():
                return await _reply(tx, interaction, _BUSY_TEXT)
        except Exception:
            pass

        if await _has_transcript(tx, channel.id):
            return await _reply(tx, interaction, _EXISTS_TEXT)

        await _defer(tx, interaction)
        return await original(interaction)

    try:
        setattr(guarded, "_ticket_transcript_busy_wrapped", True)
        item.callback = guarded
    except Exception as exc:
        _warn(f"could not wrap transcript button: {exc!r}")


def _patch_closed_view(tx: Any) -> bool:
    cls = getattr(tx, "StaffClosedTicketView", None)
    if cls is None:
        _warn("StaffClosedTicketView unavailable")
        return False

    original_init = getattr(cls, "__init__", None)
    if not callable(original_init):
        _warn("StaffClosedTicketView.__init__ unavailable")
        return False
    if getattr(original_init, "_ticket_transcript_busy_init_wrapped", False):
        return True

    def patched_init(self: Any, *args: Any, **kwargs: Any) -> None:
        original_init(self, *args, **kwargs)
        try:
            for item in getattr(self, "children", []) or []:
                custom_id = str(getattr(item, "custom_id", "") or "")
                if custom_id == _TRANSCRIPT_BUTTON_ID:
                    _wrap_transcript_button(tx, item)
        except Exception as exc:
            _warn(f"StaffClosedTicketView transcript patch failed: {exc!r}")

    try:
        setattr(patched_init, "_ticket_transcript_busy_init_wrapped", True)
        cls.__init__ = patched_init
        return True
    except Exception as exc:
        _warn(f"could not patch StaffClosedTicketView: {exc!r}")
        return False


def apply() -> bool:
    try:
        from .. import transcripts as tx
    except Exception as exc:
        _warn(f"could not import transcripts: {exc!r}")
        return False

    if getattr(tx, "_TICKET_TRANSCRIPT_POST_BUSY_GUARD_APPLIED", False):
        return True

    ok = _patch_closed_view(tx)
    try:
        tx._TICKET_TRANSCRIPT_POST_BUSY_GUARD_APPLIED = bool(ok)
        if ok:
            _log("patched transcript post busy/defer handling")
        return bool(ok)
    except Exception as exc:
        _warn(f"guard flag failed: {exc!r}")
        return False


apply()

__all__ = ["apply"]
