from __future__ import annotations

"""
Admin setup-health command.

Registers a small top-level /setup-health command during startup. This is
intentionally separate from older command modules so the production readiness PR
can expose diagnostics without rewriting the whole command tree.

Safety:
- guild-only
- admin/manage-guild style permission check
- ephemeral response by default
- read-only diagnostics only
"""

from typing import Any

import discord
from discord import app_commands

from ..globals import bot
from ..config_new.setup_health import build_guild_setup_health, build_setup_health_embed

_REGISTERED = False


def _log(message: str) -> None:
    try:
        print(f"🧭 setup_health_command {message}")
    except Exception:
        pass


def _warn(message: str) -> None:
    try:
        print(f"⚠️ setup_health_command {message}")
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


def register_setup_health_command() -> None:
    global _REGISTERED
    if _REGISTERED:
        return
    _REGISTERED = True

    tree = getattr(bot, "tree", None)
    if tree is None:
        _warn("bot.tree unavailable; command not registered")
        return

    @app_commands.command(
        name="setup-health",
        description="Check what Stoney Verify setup/config is missing in this server.",
    )
    @app_commands.guild_only()
    async def setup_health(interaction: discord.Interaction) -> None:
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
            report = await build_guild_setup_health(guild)
            embed = build_setup_health_embed(report)
            await interaction.followup.send(embed=embed, ephemeral=True)
        except Exception as e:
            await interaction.followup.send(
                f"Setup health check failed: `{type(e).__name__}: {e}`",
                ephemeral=True,
            )

    try:
        # If a command with this name already exists in this process, replace it.
        try:
            tree.remove_command("setup-health", type=discord.AppCommandType.chat_input)
        except Exception:
            pass
        tree.add_command(setup_health)
        _log("registered /setup-health")
    except Exception as e:
        _warn(f"failed registering /setup-health: {repr(e)}")


register_setup_health_command()


__all__ = ["register_setup_health_command"]
