from __future__ import annotations

"""Clarify Channel Name Fonts menu wording.

The font mode buttons save defaults only. Existing channels are renamed only
through the preview/apply flow. This guard patches labels/descriptions so users
cannot confuse saving defaults with applying channel renames.
"""

from typing import Any

import discord

_PATCHED = False


def _log(message: str) -> None:
    try:
        print(f"🔤 channel_font_menu_clarity_guard {message}")
    except Exception:
        pass


def _safe_str(value: Any, default: str = "") -> str:
    try:
        if value is None:
            return default
        text = str(value).strip()
        return text if text else default
    except Exception:
        return default


def _patch_embed(font_guard: Any) -> None:
    original = getattr(font_guard, "build_channel_font_embed", None)
    if not callable(original) or getattr(original, "_clarity_wrapped", False):
        return

    async def wrapped_build_channel_font_embed(guild_id: int, *, saved_message: str | None = None, options_override: dict[str, str] | None = None):
        embed: discord.Embed = await original(guild_id, saved_message=saved_message, options_override=options_override)
        embed.description = (
            "**Step 1 — Save defaults:** choose a font and default apply mode below. "
            "This only saves settings and updates the preview.\n\n"
            "**Step 2 — Rename existing channels:** press **Preview & Apply Channel Renames**. "
            "You will see the exact rename plan before anything changes.\n\n"
            "**Step 3 — Undo:** after applying, use **Undo Last Font Rename** if needed."
        )
        try:
            for index, field in enumerate(list(embed.fields)):
                name = _safe_str(getattr(field, "name", ""))
                if name == "Apply mode":
                    embed.set_field_at(index, name="Saved default mode", value=getattr(field, "value", ""), inline=getattr(field, "inline", True))
                elif name == "Note":
                    embed.set_field_at(
                        index,
                        name="Important",
                        value=(
                            "The font/mode buttons below do **not** rename channels. "
                            "They save defaults only. Use **Preview & Apply Channel Renames** for real changes."
                        ),
                        inline=False,
                    )
        except Exception:
            pass
        embed.set_footer(text="Save defaults first, then use Preview & Apply Channel Renames to change existing channels.")
        return embed

    setattr(wrapped_build_channel_font_embed, "_clarity_wrapped", True)
    font_guard.build_channel_font_embed = wrapped_build_channel_font_embed


def _patch_view(font_guard: Any) -> None:
    view_cls = getattr(font_guard, "ChannelFontModeView", None)
    if view_cls is None or getattr(view_cls, "_clarity_patched", False):
        return
    original_init = view_cls.__init__

    def patched_init(self: Any, options: dict[str, str]) -> None:
        original_init(self, options)
        current_scope = _safe_str((options or {}).get("unicodeStyleScope") or (options or {}).get("unicode_style_scope") or "whole_name")
        for child in getattr(self, "children", []) or []:
            cid = _safe_str(getattr(child, "custom_id", ""))
            if cid.endswith(":whole"):
                child.label = "✅ Saved default: Style generated name" if current_scope == "whole_name" else "Save default: Style generated name"
                child.style = discord.ButtonStyle.primary if current_scope == "whole_name" else discord.ButtonStyle.secondary
            elif cid.endswith(":text_only"):
                child.label = "✅ Saved default: Text only — keep emoji" if current_scope == "text_only" else "Save default: Text only — keep emoji"
                child.style = discord.ButtonStyle.primary if current_scope == "text_only" else discord.ButtonStyle.secondary
            elif cid == "dank_setup_font:preview_renames":
                child.label = "Preview & Apply Channel Renames"
                child.style = discord.ButtonStyle.success

    view_cls.__init__ = patched_init
    setattr(view_cls, "_clarity_patched", True)


def _patch_queue_preview_button(queue_guard: Any) -> None:
    button_cls = getattr(queue_guard, "QueuedFontRenamePreviewButton", None)
    if button_cls is None or getattr(button_cls, "_clarity_patched", False):
        return
    original_init = button_cls.__init__

    def patched_init(self: Any, *args: Any, **kwargs: Any) -> None:
        original_init(self, *args, **kwargs)
        self.label = "Preview & Apply Channel Renames"
        self.style = discord.ButtonStyle.success

    button_cls.__init__ = patched_init
    setattr(button_cls, "_clarity_patched", True)


def apply() -> bool:
    global _PATCHED
    if _PATCHED:
        return True
    try:
        from stoney_verify.startup_guards import setup_channel_font_mode_guard as font_guard
        from stoney_verify.startup_guards import channel_font_rename_queue_guard as queue_guard

        _patch_embed(font_guard)
        _patch_view(font_guard)
        _patch_queue_preview_button(queue_guard)
        _PATCHED = True
        _log("active; Channel Name Fonts menu separates save defaults from preview/apply renames")
        return True
    except Exception as exc:
        try:
            print(f"⚠️ channel_font_menu_clarity_guard failed: {exc!r}")
        except Exception:
            pass
        return False


apply()

__all__ = ["apply"]
