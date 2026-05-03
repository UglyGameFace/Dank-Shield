from __future__ import annotations

"""
Setup finish command.

Marks setup_completed=true only when service-aware setup health has no critical
blockers. Warnings are allowed because some servers may intentionally skip
optional pieces such as transcript/archive channels.
"""

from typing import Any

import discord
from discord import app_commands

from ..globals import bot
from ..config_new.setup_health import build_guild_setup_health, build_setup_health_embed
from ..config_new.setup_writer import mark_setup_completed

_REGISTERED = False


def _log(message: str) -> None:
    try:
        print(f"🧭 setup_finish_command {message}")
    except Exception:
        pass


def _warn(message: str) -> None:
    try:
        print(f"⚠️ setup_finish_command {message}")
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


def _critical_count(report: dict[str, Any]) -> int:
    try:
        summary = report.get("summary") if isinstance(report.get("summary"), dict) else {}
        return int(summary.get("critical", 0) or 0)
    except Exception:
        return 0


def register_setup_finish_command() -> None:
    global _REGISTERED
    if _REGISTERED:
        return
    _REGISTERED = True

    tree = getattr(bot, "tree", None)
    if tree is None:
        _warn("bot.tree unavailable; command not registered")
        return

    @app_commands.command(
        name="setup-finish",
        description="Mark setup complete after checking for critical setup blockers.",
    )
    @app_commands.guild_only()
    async def setup_finish(interaction: discord.Interaction) -> None:
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
            critical = _critical_count(report)

            if critical > 0:
                embed = build_setup_health_embed(report)
                embed.insert_field_at(
                    0,
                    name="Setup Not Finished Yet",
                    value=(
                        f"I found `{critical}` critical blocker(s). Fix those first, then run `/setup-finish` again.\n\n"
                        "Warnings are optional-ish. Critical blockers are what can actually break selected services."
                    ),
                    inline=False,
                )
                await interaction.followup.send(embed=embed, ephemeral=True)
                return

            await mark_setup_completed(guild.id, completed=True)

            refreshed = await build_guild_setup_health(guild)
            embed = build_setup_health_embed(refreshed)
            embed.insert_field_at(
                0,
                name="Setup Complete",
                value=(
                    "This server is marked setup complete.\n"
                    "You can rerun `/setup-services`, `/setup-targets`, or `/setup-health` anytime."
                ),
                inline=False,
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
        except Exception as e:
            await interaction.followup.send(
                f"Setup finish failed: `{type(e).__name__}: {e}`",
                ephemeral=True,
            )

    try:
        try:
            tree.remove_command("setup-finish", type=discord.AppCommandType.chat_input)
        except Exception:
            pass
        tree.add_command(setup_finish)
        _log("registered /setup-finish")
    except Exception as e:
        _warn(f"failed registering /setup-finish: {repr(e)}")


register_setup_finish_command()


__all__ = ["register_setup_finish_command"]
