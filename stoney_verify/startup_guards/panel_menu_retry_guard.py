from __future__ import annotations

import discord

_PATCHED = False


def _key(interaction: discord.Interaction):
    try:
        if interaction.guild is None or interaction.user is None:
            return None
        return (int(interaction.guild.id), int(interaction.user.id))
    except Exception:
        return None


def apply() -> bool:
    global _PATCHED
    if _PATCHED:
        return True
    try:
        from stoney_verify.commands_ext import public_ticket_panel_clean as panel_mod
        from stoney_verify.startup_guards import public_ticket_panel_clean_hardening as hardening
    except Exception:
        return False
    original = getattr(panel_mod, "_handle_panel_button", None)
    if not callable(original) or getattr(original, "_panel_retry_wrapped", False):
        return False

    async def wrapped(interaction: discord.Interaction) -> None:
        key = _key(interaction)
        if key is not None:
            try:
                hardening._MENU_SESSION_UNTIL.pop(key, None)
            except Exception:
                pass
        try:
            return await original(interaction)
        finally:
            if key is not None:
                try:
                    hardening._MENU_SESSION_UNTIL.pop(key, None)
                except Exception:
                    pass

    setattr(wrapped, "_panel_retry_wrapped", True)
    panel_mod._handle_panel_button = wrapped  # type: ignore[assignment]
    _PATCHED = True
    try:
        print("panel_menu_retry_guard active")
    except Exception:
        pass
    return True


apply()

__all__ = ["apply"]
