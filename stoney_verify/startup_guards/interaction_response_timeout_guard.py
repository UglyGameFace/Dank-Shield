from __future__ import annotations

import asyncio

import discord

_PATCHED = False
_DELAY_SECONDS = 2.1


def _is_slash_command(interaction: discord.Interaction) -> bool:
    try:
        return interaction.type is discord.InteractionType.application_command
    except Exception:
        try:
            return int(getattr(interaction.type, "value", interaction.type)) == 2
        except Exception:
            return False


def _name(interaction: discord.Interaction) -> str:
    try:
        command = getattr(interaction, "command", None)
        qualified = getattr(command, "qualified_name", None)
        if qualified:
            return str(qualified)
    except Exception:
        pass
    try:
        data = getattr(interaction, "data", None) or {}
        if isinstance(data, dict):
            value = str(data.get("name") or "").strip()
            if value:
                return value
    except Exception:
        pass
    return "unknown"


async def _defer_if_still_open(interaction: discord.Interaction) -> None:
    try:
        await asyncio.sleep(_DELAY_SECONDS)
        if interaction.response.is_done():
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            print(f"✅ slow slash command deferred command=/{_name(interaction)}")
        except Exception:
            pass
    except discord.InteractionResponded:
        pass
    except discord.NotFound:
        pass
    except Exception as exc:
        try:
            print(f"⚠️ slow slash command defer failed command=/{_name(interaction)} error={type(exc).__name__}: {exc}")
        except Exception:
            pass


def apply() -> bool:
    global _PATCHED
    if _PATCHED:
        return True
    try:
        from stoney_verify.globals import bot

        if getattr(bot, "_DANK_RESPONSE_TIMEOUT_GUARD_ACTIVE", False):
            _PATCHED = True
            return True

        async def _on_interaction_timeout_guard(interaction: discord.Interaction) -> None:
            try:
                if not _is_slash_command(interaction):
                    return
                if interaction.response.is_done():
                    return
                asyncio.create_task(_defer_if_still_open(interaction))
            except Exception:
                pass

        bot.add_listener(_on_interaction_timeout_guard, "on_interaction")
        bot._DANK_RESPONSE_TIMEOUT_GUARD_ACTIVE = True
        _PATCHED = True
        print("✅ interaction_response_timeout_guard active; slow slash commands receive a fallback defer")
        return True
    except Exception as exc:
        try:
            print(f"⚠️ interaction_response_timeout_guard failed: {type(exc).__name__}: {exc}")
        except Exception:
            pass
        return False


apply()

__all__ = ["apply"]
