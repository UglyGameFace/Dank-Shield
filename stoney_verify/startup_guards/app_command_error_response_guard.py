from __future__ import annotations

"""Always answer failed slash-command interactions when possible.

Discord shows the useless red "Interaction failed" banner when an app command
raises before a response/defer is sent. This guard installs a tree-level fallback
that logs the real exception and sends a short ephemeral failure message instead
of leaving staff with no clue.
"""

import traceback
from typing import Any

import discord
from discord import app_commands

_PATCHED = False


def _command_name(interaction: discord.Interaction) -> str:
    try:
        command = getattr(interaction, "command", None)
        qualified = getattr(command, "qualified_name", None)
        if qualified:
            return str(qualified)
    except Exception:
        pass
    try:
        data = getattr(interaction, "data", None) or {}
        name = data.get("name") if isinstance(data, dict) else None
        return str(name or "unknown")
    except Exception:
        return "unknown"


async def _send_failure(interaction: discord.Interaction, message: str) -> None:
    payload = {
        "content": message[:1900],
        "ephemeral": True,
        "allowed_mentions": discord.AllowedMentions.none(),
    }
    try:
        if interaction.response.is_done():
            await interaction.followup.send(**payload)
        else:
            await interaction.response.send_message(**payload)
        return
    except Exception:
        pass
    try:
        await interaction.followup.send(**payload)
    except Exception:
        pass


def _friendly_error(error: BaseException) -> str:
    original = getattr(error, "original", error)
    if isinstance(original, app_commands.CommandOnCooldown):
        return f"⏳ That command is cooling down. Try again in {original.retry_after:.1f}s."
    if isinstance(original, app_commands.MissingPermissions):
        return "❌ You are missing the required Discord permissions for that command."
    if isinstance(original, app_commands.BotMissingPermissions):
        return "❌ I am missing the required Discord permissions for that command."
    if isinstance(original, discord.Forbidden):
        return "❌ Discord blocked that action because of permissions or role hierarchy."
    return "❌ That command hit an internal error. I logged it so it can be fixed instead of silently failing."


def apply() -> bool:
    global _PATCHED
    if _PATCHED:
        return True
    try:
        from stoney_verify.globals import bot

        tree = getattr(bot, "tree", None)
        if tree is None or getattr(tree, "_DANK_APP_ERROR_RESPONSE_ACTIVE", False):
            _PATCHED = True
            return True

        original_on_error = getattr(tree, "on_error", None)

        async def _on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
            command_name = _command_name(interaction)
            original = getattr(error, "original", error)
            try:
                print(f"❌ app command error command=/{command_name} user={getattr(interaction.user, 'id', 'unknown')}: {type(original).__name__}: {original!r}")
                traceback.print_exception(type(original), original, getattr(original, "__traceback__", None))
            except Exception:
                pass

            await _send_failure(interaction, _friendly_error(error))

            try:
                if callable(original_on_error) and original_on_error is not _on_app_command_error:
                    await original_on_error(interaction, error)
            except Exception:
                pass

        tree.on_error = _on_app_command_error  # type: ignore[method-assign]
        tree._DANK_APP_ERROR_RESPONSE_ACTIVE = True
        _PATCHED = True
        print("✅ app_command_error_response_guard active; slash failures now send an ephemeral error and log traceback")
        return True
    except Exception as exc:
        try:
            print(f"⚠️ app_command_error_response_guard failed: {type(exc).__name__}: {exc}")
        except Exception:
            pass
        return False


apply()

__all__ = ["apply"]
