from __future__ import annotations

"""Ensure Channel Name Fonts always has Preview & Apply.

The font screen is imported while several startup guards are still patching, so
button injection can run before ChannelFontModeView exists. This guard retries
briefly and patches the view after the class is available. It also loads the
scoped bot-access repair button for blocked font previews.
"""

import threading
from typing import Any

import discord

_PATCHED = False
_STARTED = False
_ACCESS_REPAIR_LOADED = False


async def _send_problem(interaction: discord.Interaction, exc: BaseException) -> None:
    msg = f"❌ Preview & Apply is unavailable: `{type(exc).__name__}: {str(exc)[:220]}`"
    try:
        if interaction.response.is_done():
            await interaction.followup.send(msg, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())
        else:
            await interaction.response.send_message(msg, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())
    except Exception:
        pass


def _load_access_repair() -> None:
    global _ACCESS_REPAIR_LOADED
    if _ACCESS_REPAIR_LOADED:
        return
    try:
        from stoney_verify.startup_guards import channel_font_access_repair_guard

        channel_font_access_repair_guard.apply()
        _ACCESS_REPAIR_LOADED = True
    except Exception as exc:
        try:
            print(f"⚠️ channel_font_preview_button_guard access repair failed: {exc!r}")
        except Exception:
            pass


class DirectPreviewApplyButton(discord.ui.Button):
    def __init__(self, *, row: int = 3) -> None:
        super().__init__(label="Preview & Apply Channel Renames", emoji="👀", style=discord.ButtonStyle.success, custom_id="dank_setup_font:preview_renames", row=row)

    async def callback(self, interaction: discord.Interaction) -> None:  # type: ignore[override]
        try:
            from stoney_verify.startup_guards import channel_font_rename_queue_guard as rename_guard

            _load_access_repair()
            button_cls = getattr(rename_guard, "QueuedFontRenamePreviewButton", None)
            if not callable(button_cls):
                raise RuntimeError("rename preview button class is unavailable")
            temp = button_cls(row=3)
            await temp.callback(interaction)
        except Exception as exc:
            await _send_problem(interaction, exc)


def _patch() -> bool:
    global _PATCHED
    if _PATCHED:
        return True
    try:
        _load_access_repair()
        from stoney_verify.startup_guards import setup_channel_font_mode_guard as font_guard

        view_cls = getattr(font_guard, "ChannelFontModeView", None)
        if view_cls is None:
            return False
        if getattr(view_cls, "_direct_preview_apply_patched", False):
            _PATCHED = True
            return True
        original_init = view_cls.__init__

        def patched_init(self: Any, options: dict[str, str]) -> None:
            original_init(self, options)
            try:
                if not any(str(getattr(child, "custom_id", "")) == "dank_setup_font:preview_renames" for child in getattr(self, "children", []) or []):
                    self.add_item(DirectPreviewApplyButton(row=3))
            except Exception:
                pass

        view_cls.__init__ = patched_init
        setattr(view_cls, "_direct_preview_apply_patched", True)
        _PATCHED = True
        return True
    except Exception:
        return False


def _retry(attempt: int = 0) -> None:
    if _patch() or attempt >= 20:
        return
    try:
        timer = threading.Timer(0.25, lambda: _retry(attempt + 1))
        timer.daemon = True
        timer.start()
    except Exception:
        pass


def apply() -> bool:
    global _STARTED
    _load_access_repair()
    if _patch():
        try:
            print("🔤 channel_font_preview_button_guard active; Preview & Apply and scoped access repair are attached")
        except Exception:
            pass
        return True
    if not _STARTED:
        _STARTED = True
        _retry(0)
    try:
        print("🔤 channel_font_preview_button_guard waiting for ChannelFontModeView before attaching button")
    except Exception:
        pass
    return True


apply()

__all__ = ["apply", "DirectPreviewApplyButton"]
