from __future__ import annotations

"""
Simple setup target picker command.

After /setup-services, admins can run /setup-targets and pick only the
channels/categories/roles they want saved. Every option is optional so servers
using only one or two services are not forced through irrelevant fields.
"""

from typing import Any, Optional

import discord
from discord import app_commands

from ..globals import bot
from ..config_new.setup_health import build_guild_setup_health, build_setup_health_embed
from ..config_new.setup_writer import save_setup_targets

_REGISTERED = False


def _log(message: str) -> None:
    try:
        print(f"🧭 setup_targets_command {message}")
    except Exception:
        pass


def _warn(message: str) -> None:
    try:
        print(f"⚠️ setup_targets_command {message}")
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


def register_setup_targets_command() -> None:
    global _REGISTERED
    if _REGISTERED:
        return
    _REGISTERED = True

    tree = getattr(bot, "tree", None)
    if tree is None:
        _warn("bot.tree unavailable; command not registered")
        return

    @app_commands.command(
        name="setup-targets",
        description="Save Stoney Verify channels/categories/roles using Discord pickers.",
    )
    @app_commands.describe(
        ticket_category="Category where open support tickets should be created.",
        ticket_archive_category="Category where closed tickets should be moved.",
        transcripts_channel="Channel where ticket transcripts should be posted.",
        staff_role="Role allowed to work tickets/staff panels.",
        verify_channel="Channel where users start ID verification.",
        verified_role="Role granted after successful verification.",
        unverified_role="Role removed/managed during verification.",
        vc_verify_channel="Channel used for voice verification sessions.",
        vc_verify_queue_channel="Channel used for voice verification requests/queue.",
        modlog_channel="Channel where moderation logs should be posted.",
        resident_role="Optional resident/member role.",
    )
    @app_commands.guild_only()
    async def setup_targets(
        interaction: discord.Interaction,
        ticket_category: Optional[discord.CategoryChannel] = None,
        ticket_archive_category: Optional[discord.CategoryChannel] = None,
        transcripts_channel: Optional[discord.TextChannel] = None,
        staff_role: Optional[discord.Role] = None,
        verify_channel: Optional[discord.TextChannel] = None,
        verified_role: Optional[discord.Role] = None,
        unverified_role: Optional[discord.Role] = None,
        vc_verify_channel: Optional[discord.TextChannel] = None,
        vc_verify_queue_channel: Optional[discord.TextChannel] = None,
        modlog_channel: Optional[discord.TextChannel] = None,
        resident_role: Optional[discord.Role] = None,
    ) -> None:
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
            result = await save_setup_targets(
                guild.id,
                ticket_category_id=ticket_category,
                ticket_archive_category_id=ticket_archive_category,
                transcripts_channel_id=transcripts_channel,
                staff_role_id=staff_role,
                verify_channel_id=verify_channel,
                verified_role_id=verified_role,
                unverified_role_id=unverified_role,
                vc_verify_channel_id=vc_verify_channel,
                vc_verify_queue_channel_id=vc_verify_queue_channel,
                modlog_channel_id=modlog_channel,
                resident_role_id=resident_role,
            )

            if not result.get("ok"):
                await interaction.followup.send(
                    "Nothing was saved. Pick at least one channel, category, or role.\n"
                    "Tip: run `/setup-services` first, then `/setup-targets` for only the pieces that service needs.",
                    ephemeral=True,
                )
                return

            report = await build_guild_setup_health(guild)
            embed = build_setup_health_embed(report)

            saved = result.get("fields") if isinstance(result.get("fields"), dict) else {}
            saved_lines = [f"`{key}` → `{value}`" for key, value in saved.items()]
            embed.insert_field_at(
                0,
                name="Saved Setup Targets",
                value="\n".join(saved_lines)[:1024] if saved_lines else "Saved selected setup targets.",
                inline=False,
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
        except Exception as e:
            await interaction.followup.send(
                f"Setup target save failed: `{type(e).__name__}: {e}`",
                ephemeral=True,
            )

    try:
        try:
            tree.remove_command("setup-targets", type=discord.AppCommandType.chat_input)
        except Exception:
            pass
        tree.add_command(setup_targets)
        _log("registered /setup-targets")
    except Exception as e:
        _warn(f"failed registering /setup-targets: {repr(e)}")


register_setup_targets_command()


__all__ = ["register_setup_targets_command"]
