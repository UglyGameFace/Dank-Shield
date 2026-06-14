from __future__ import annotations

"""Temporary migration shim for /dank setup Feature Centers.

Removal path: delete this file after setup_smart_home_menu_guard imports and calls
owned service functions directly. Until then, this shim keeps Feature Center
buttons on the owned services instead of invoking decorated slash Command
objects.
"""

import discord

_PATCHED = False


async def _send_error(interaction: discord.Interaction, label: str, exc: BaseException) -> None:
    msg = f"❌ {label}: `{type(exc).__name__}: {str(exc)[:240]}`"
    try:
        if interaction.response.is_done():
            await interaction.followup.send(msg, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())
        else:
            await interaction.response.send_message(msg, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())
    except Exception:
        pass


async def _open_protection_center(interaction: discord.Interaction) -> None:
    try:
        from stoney_verify.commands_ext import public_protection_center as protection

        target = getattr(protection, "open_protection_center", None)
        if callable(target):
            return await target(interaction)
        # Temporary fallback until Protection Center is split into owned services too.
        command = getattr(protection, "protection_center", None)
        callback = getattr(command, "callback", None)
        if callable(callback):
            return await callback(interaction)
        raise TypeError("Protection Center service unavailable")
    except Exception as exc:
        await _send_error(interaction, "Protection Center failed", exc)


async def _welcome_health(interaction: discord.Interaction) -> None:
    try:
        from stoney_verify.commands_ext import public_welcome_group as welcome

        return await welcome.open_welcome_health(interaction)
    except Exception as exc:
        await _send_error(interaction, "Welcome health failed", exc)


async def _welcome_preview(interaction: discord.Interaction) -> None:
    try:
        from stoney_verify.commands_ext import public_welcome_group as welcome

        return await welcome.open_welcome_preview(interaction)
    except Exception as exc:
        await _send_error(interaction, "Welcome preview failed", exc)


async def _welcome_post(interaction: discord.Interaction) -> None:
    try:
        from stoney_verify.commands_ext import public_welcome_group as welcome

        return await welcome.post_welcome_message(interaction, channel=None)
    except Exception as exc:
        await _send_error(interaction, "Welcome post failed", exc)


async def _modlog_health(interaction: discord.Interaction) -> None:
    try:
        from stoney_verify.commands_ext import public_modlog_group as modlog

        return await modlog.open_modlog_health(interaction)
    except Exception as exc:
        await _send_error(interaction, "Modlog health failed", exc)


async def _modlog_test(interaction: discord.Interaction) -> None:
    try:
        from stoney_verify.commands_ext import public_modlog_group as modlog

        return await modlog.send_modlog_test(interaction)
    except Exception as exc:
        await _send_error(interaction, "Modlog test failed", exc)


def apply() -> bool:
    global _PATCHED
    if _PATCHED:
        return True
    try:
        from stoney_verify.startup_guards import setup_smart_home_menu_guard as hub

        hub._open_protection_center = _open_protection_center
        hub._welcome_health = _welcome_health
        hub._welcome_preview = _welcome_preview
        hub._welcome_post = _welcome_post
        hub._modlog_health = _modlog_health
        hub._modlog_test = _modlog_test
        _PATCHED = True
        print("✅ setup_feature_center_invocation_fix active; Feature Center buttons route to owned services")
        return True
    except Exception as exc:
        try:
            print(f"⚠️ setup_feature_center_invocation_fix failed: {type(exc).__name__}: {exc}")
        except Exception:
            pass
        return False


apply()

__all__ = ["apply"]
