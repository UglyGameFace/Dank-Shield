from __future__ import annotations

"""Normalize stale public setup wording in /dank setup.

This keeps old Stoney/private-server wording from leaking into the public setup
flow while the larger setup module is being refactored.
"""

from typing import Any

import discord

_PATCHED = False


def _log(message: str) -> None:
    try:
        print(f"🧼 setup_public_text_cleanup_guard {message}")
    except Exception:
        pass


def _warn(message: str) -> None:
    try:
        print(f"⚠️ setup_public_text_cleanup_guard {message}")
    except Exception:
        pass


def _clean_text(text: Any) -> Any:
    if not isinstance(text, str):
        return text
    out = text
    replacements = {
        "Stoney's": "Dank Shield's",
        "Stoney ": "Dank Shield ",
        "Stoney saved": "Dank Shield saved",
        "Use `/stoney cleanup` for cleanup tools, then return to `/dank setup`.": "Use **Start Over / Cleanup** and **Use My Existing Server** from this setup screen. Return to `/dank setup` whenever you need to continue.",
        "/stoney cleanup": "/dank setup cleanup",
        "/stoney": "/dank",
    }
    for old, new in replacements.items():
        out = out.replace(old, new)
    return out


def _clean_embed(embed: discord.Embed) -> discord.Embed:
    try:
        embed.title = _clean_text(embed.title)
        embed.description = _clean_text(embed.description)
    except Exception:
        pass
    return embed


def _wrap_solid_setup_cleanup(solid: Any) -> bool:
    view_cls = getattr(solid, "SolidSetupView", None)
    if view_cls is None:
        return False
    original = getattr(view_cls, "cleanup", None)
    if not callable(original) or getattr(original, "_public_text_cleanup_wrapped", False):
        return False

    async def wrapped_cleanup(self: Any, interaction: discord.Interaction, button: discord.ui.Button) -> Any:
        before_send = getattr(interaction.response, "edit_message", None)
        if not callable(before_send):
            return await original(self, interaction, button)

        async def cleaned_edit_message(*args: Any, **kwargs: Any) -> Any:
            embed = kwargs.get("embed")
            if isinstance(embed, discord.Embed):
                kwargs["embed"] = _clean_embed(embed)
            return await before_send(*args, **kwargs)

        try:
            interaction.response.edit_message = cleaned_edit_message  # type: ignore[method-assign]
            return await original(self, interaction, button)
        finally:
            try:
                interaction.response.edit_message = before_send  # type: ignore[method-assign]
            except Exception:
                pass

    setattr(wrapped_cleanup, "_public_text_cleanup_wrapped", True)
    setattr(view_cls, "cleanup", wrapped_cleanup)
    return True


def apply() -> bool:
    global _PATCHED
    if _PATCHED:
        return True
    try:
        from stoney_verify.commands_ext import public_setup_solid as solid

        wrapped = _wrap_solid_setup_cleanup(solid)
        _PATCHED = True
        _log(f"active cleanup_wrapped={wrapped}")
        return True
    except Exception as e:
        _warn(f"failed: {e!r}")
        return False


apply()

__all__ = ["apply"]
