from __future__ import annotations

"""
Simple service selection command.

This is the first setup step for public servers:
1. Pick what services you want.
2. Save service flags to guild_configs.
3. Run setup-health immediately so the admin sees only relevant missing pieces.

It keeps setup modular and avoids making ticket-only customers configure
verification/modlog/VC features they do not want.
"""

from typing import Any, Literal

import discord
from discord import app_commands

from ..globals import bot
from ..config_new.service_presets import PRESET_LABELS, preset_summary, save_service_preset
from ..config_new.setup_health import build_guild_setup_health, build_setup_health_embed

_REGISTERED = False

PresetChoice = Literal[
    "tickets",
    "tickets_modlog",
    "verification",
    "voice_verification",
    "verification_plus_voice",
    "full",
]


def _log(message: str) -> None:
    try:
        print(f"🧭 setup_services_command {message}")
    except Exception:
        pass


def _warn(message: str) -> None:
    try:
        print(f"⚠️ setup_services_command {message}")
    except Exception:
        pass


def _is_setup_admin(member: Any) -> bool:
    try:
        perms = getattr(member, "guild_permissions", None)
        return bool(
            getattr(perms, "administrator", False)
            or getattr(perms, "manage_guild", False)
            or getattr(perms, "manage_channels", False)
        )
    except Exception:
        return False


def _preset_choices() -> list[app_commands.Choice[str]]:
    return [
        app_commands.Choice(name="Tickets only", value="tickets"),
        app_commands.Choice(name="Tickets + Modlog", value="tickets_modlog"),
        app_commands.Choice(name="ID verification only", value="verification"),
        app_commands.Choice(name="Voice verification only", value="voice_verification"),
        app_commands.Choice(name="ID + Voice verification", value="verification_plus_voice"),
        app_commands.Choice(name="Full Stoney suite", value="full"),
    ]


def register_setup_services_command() -> None:
    global _REGISTERED
    if _REGISTERED:
        return
    _REGISTERED = True

    tree = getattr(bot, "tree", None)
    if tree is None:
        _warn("bot.tree unavailable; command not registered")
        return

    @app_commands.command(
        name="setup-services",
        description="Choose which Stoney Verify services this server wants to use.",
    )
    @app_commands.describe(preset="Pick the service package this server wants.")
    @app_commands.choices(preset=_preset_choices())
    @app_commands.guild_only()
    async def setup_services(interaction: discord.Interaction, preset: app_commands.Choice[str]) -> None:
        guild = interaction.guild
        user = interaction.user

        if guild is None:
            await interaction.response.send_message(
                "This command can only be used inside a server.",
                ephemeral=True,
            )
            return

        if not _is_setup_admin(user):
            await interaction.response.send_message(
                "Staff/admin only: you need Manage Server, Manage Channels, or Administrator.",
                ephemeral=True,
            )
            return

        try:
            await interaction.response.defer(ephemeral=True, thinking=True)
        except Exception:
            pass

        try:
            preset_key = str(preset.value)
            result = await save_service_preset(guild.id, preset_key)
            report = await build_guild_setup_health(guild)
            embed = build_setup_health_embed(report)
            embed.insert_field_at(
                0,
                name="Selected Services",
                value=(
                    f"Preset: **{PRESET_LABELS.get(preset_key, preset_key)}**\n"
                    f"Enabled: `{preset_summary(preset_key)}`\n\n"
                    "Next: fix only the items listed below, then run `/setup-health` again."
                ),
                inline=False,
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
        except Exception as e:
            await interaction.followup.send(
                f"Setup service selection failed: `{type(e).__name__}: {e}`",
                ephemeral=True,
            )

    try:
        try:
            tree.remove_command("setup-services", type=discord.AppCommandType.chat_input)
        except Exception:
            pass
        tree.add_command(setup_services)
        _log("registered /setup-services")
    except Exception as e:
        _warn(f"failed registering /setup-services: {repr(e)}")


register_setup_services_command()


__all__ = ["register_setup_services_command"]
