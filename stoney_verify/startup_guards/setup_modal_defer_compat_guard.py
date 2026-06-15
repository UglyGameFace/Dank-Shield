from __future__ import annotations

"""Compatibility helper for setup/recovery modals.

Some recovery cleanup flows defer modal submissions through
``public_setup_solid._safe_defer_modal``. Older setup code only exposed the
button/update defer helper, so modal submit callbacks could raise an AttributeError
before acknowledging Discord's modal interaction. That shows up to users as the
red 'Something went wrong. Try again.' modal error.

Removal path: move _safe_defer_modal directly into public_setup_solid.py and
remove this startup guard.
"""

import discord

_PATCHED = False


async def _safe_defer_modal(interaction: discord.Interaction) -> None:
    try:
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True, thinking=True)
    except Exception:
        pass


def apply() -> bool:
    global _PATCHED
    if _PATCHED:
        return True
    try:
        from stoney_verify.commands_ext import public_setup_solid as solid

        existing = getattr(solid, "_safe_defer_modal", None)
        if not callable(existing):
            solid._safe_defer_modal = _safe_defer_modal  # type: ignore[attr-defined]
        _PATCHED = True
        print("✅ setup_modal_defer_compat_guard active; setup modal submissions can defer safely")
        return True
    except Exception as exc:
        try:
            print(f"⚠️ setup_modal_defer_compat_guard failed: {type(exc).__name__}: {exc}")
        except Exception:
            pass
        return False


apply()

__all__ = ["apply"]
