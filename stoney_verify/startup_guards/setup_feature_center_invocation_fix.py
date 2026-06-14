from __future__ import annotations

"""Temporary migration shim for /dank setup Feature Centers.

Remove this after setup_smart_home_menu_guard is converted from calling registered
app_commands directly to owned service functions. For now, Feature Center buttons
must support both raw coroutine functions and discord.app_commands.Command
objects because decorated commands are not directly callable.
"""

from typing import Any

import discord

_PATCHED = False


async def _invoke(target: Any, interaction: discord.Interaction, *args: Any, **kwargs: Any) -> Any:
    callback = getattr(target, "callback", None)
    if callable(callback):
        return await callback(interaction, *args, **kwargs)
    if callable(target):
        return await target(interaction, *args, **kwargs)
    raise TypeError(f"{type(target).__name__} object is not callable")


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
        return await _invoke(protection.protection_center, interaction)
    except Exception as exc:
        await _send_error(interaction, "Protection Center failed", exc)


async def _welcome_health(interaction: discord.Interaction) -> None:
    try:
        from stoney_verify.commands_ext import public_welcome_group as welcome
        return await _invoke(welcome.welcome_health, interaction)
    except Exception as exc:
        await _send_error(interaction, "Welcome health failed", exc)


async def _welcome_preview(interaction: discord.Interaction) -> None:
    try:
        from stoney_verify.commands_ext import public_welcome_group as welcome
        return await _invoke(welcome.welcome_preview, interaction)
    except Exception as exc:
        await _send_error(interaction, "Welcome preview failed", exc)


async def _welcome_post(interaction: discord.Interaction) -> None:
    try:
        from stoney_verify.commands_ext import public_welcome_group as welcome
        return await _invoke(welcome.welcome_post, interaction, channel=None)
    except Exception as exc:
        await _send_error(interaction, "Welcome post failed", exc)


async def _modlog_health(interaction: discord.Interaction) -> None:
    try:
        from stoney_verify.commands_ext import public_modlog_group as modlog
        return await _invoke(modlog.modlog_health, interaction)
    except Exception as exc:
        await _send_error(interaction, "Modlog health failed", exc)


async def _modlog_test(interaction: discord.Interaction) -> None:
    try:
        from stoney_verify.commands_ext import public_modlog_group as modlog
        return await _invoke(modlog.modlog_test, interaction)
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
        print("✅ setup_feature_center_invocation_fix active; Feature Center buttons can invoke app_commands safely")
        return True
    except Exception as exc:
        try:
            print(f"⚠️ setup_feature_center_invocation_fix failed: {type(exc).__name__}: {exc}")
        except Exception:
            pass
        return False


apply()

__all__ = ["apply"]
