from __future__ import annotations

"""Add the missing transcripts picker to /dank setup Ticket Basics.

The setup scoreboard requires transcripts for production ticket readiness, so the
Ticket Basics screen must offer a direct way to save the transcripts channel.
"""

from typing import Any

import discord

_PATCHED = False


def _log(message: str) -> None:
    try:
        print(f"🧩 setup_ticket_transcripts_picker_guard {message}")
    except Exception:
        pass


def _warn(message: str) -> None:
    try:
        print(f"⚠️ setup_ticket_transcripts_picker_guard {message}")
    except Exception:
        pass


def _has_transcript_picker(view: discord.ui.View) -> bool:
    try:
        for item in getattr(view, "children", []) or []:
            columns = tuple(getattr(item, "columns", ()) or ())
            also_same = tuple(getattr(item, "also_same", ()) or ())
            if "transcripts_channel_id" in columns or "transcripts_channel_id" in also_same:
                return True
    except Exception:
        pass
    return False


def apply() -> bool:
    global _PATCHED
    if _PATCHED:
        return True
    try:
        from stoney_verify.commands_ext import public_setup_solid as solid

        view_cls = getattr(solid, "TicketBasicsPickerView", None)
        save_channel_select = getattr(solid, "SaveChannelSelect", None)
        if view_cls is None or save_channel_select is None:
            _warn("TicketBasicsPickerView or SaveChannelSelect missing")
            return False
        original_init = getattr(view_cls, "__init__", None)
        if not callable(original_init):
            _warn("TicketBasicsPickerView.__init__ missing")
            return False
        if getattr(original_init, "_transcripts_picker_guard_wrapped", False):
            _PATCHED = True
            return True

        def wrapped_init(self: discord.ui.View, *args: Any, **kwargs: Any) -> None:
            original_init(self, *args, **kwargs)
            if _has_transcript_picker(self):
                return
            try:
                self.add_item(
                    save_channel_select(
                        placeholder="Where ticket transcripts are posted",
                        columns=("transcripts_channel_id",),
                        also_same=("transcript_channel_id",),
                        channel_types=[discord.ChannelType.text],
                        row=4,
                        require_text=True,
                        require_files=True,
                    )
                )
            except Exception as e:
                _warn(f"failed adding transcripts picker: {e!r}")

        setattr(wrapped_init, "_transcripts_picker_guard_wrapped", True)
        setattr(view_cls, "__init__", wrapped_init)
        _PATCHED = True
        _log("active; Ticket Basics now includes transcripts picker")
        return True
    except Exception as e:
        _warn(f"failed: {e!r}")
        return False


apply()

__all__ = ["apply"]
