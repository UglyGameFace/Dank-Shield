from __future__ import annotations

import discord
from discord import app_commands

from ..globals import bot
from ..tickets_new.panel import send_ticket_panel, send_staff_ghost_ticket_panel
from ..events_new.members import (
    run_full_member_sync_for_guild,
    run_departed_reconciliation_for_guild,
)


# ============================================================
# New structured ticket/member admin commands
# ------------------------------------------------------------
# These are safe admin/staff commands to help operate the new
# ticket + member sync system while migrating away from the
# old flat structure.
# ============================================================


def _is_staff(interaction: discord.Interaction) -> bool:
    user = interaction.user
    if not isinstance(user, discord.Member):
        return False

    try:
        return (
            user.guild_permissions.manage_guild
            or user.guild_permissions.manage_channels
            or user.guild_permissions.administrator
        )
    except Exception:
        return False


@bot.tree.command(name="post_ticket_panel", description="Post the public ticket panel in this channel.")
async def post_ticket_panel(interaction: discord.Interaction):
    if not _is_staff(interaction):
        await interaction.response.send_message(
            "You do not have permission to use this command.",
            ephemeral=True,
        )
        return

    channel = interaction.channel
    if not isinstance(channel, discord.TextChannel):
        await interaction.response.send_message(
            "This command can only be used in a text channel.",
            ephemeral=True,
        )
        return

    await interaction.response.defer(ephemeral=True)

    try:
        await send_ticket_panel(channel)
        await interaction.followup.send(
            f"✅ Public ticket panel posted in {channel.mention}.",
            ephemeral=True,
        )
    except Exception as e:
        await interaction.followup.send(
            f"❌ Failed to post public ticket panel: {repr(e)}",
            ephemeral=True,
        )


@bot.tree.command(name="post_ghost_ticket_panel", description="Post the hidden staff-only ghost ticket panel in this channel.")
async def post_ghost_ticket_panel(interaction: discord.Interaction):
    if not _is_staff(interaction):
        await interaction.response.send_message(
            "You do not have permission to use this command.",
            ephemeral=True,
        )
        return

    channel = interaction.channel
    if not isinstance(channel, discord.TextChannel):
        await interaction.response.send_message(
            "This command can only be used in a text channel.",
            ephemeral=True,
        )
        return

    await interaction.response.defer(ephemeral=True)

    try:
        await send_staff_ghost_ticket_panel(channel)
        await interaction.followup.send(
            f"✅ Staff-only ghost ticket panel posted in {channel.mention}.",
            ephemeral=True,
        )
    except Exception as e:
        await interaction.followup.send(
            f"❌ Failed to post ghost ticket panel: {repr(e)}",
            ephemeral=True,
        )


@bot.tree.command(name="sync_members_now", description="Run a full member sync for the configured guild.")
async def sync_members_now(interaction: discord.Interaction):
    if not _is_staff(interaction):
        await interaction.response.send_message(
            "You do not have permission to use this command.",
            ephemeral=True,
        )
        return

    guild = interaction.guild
    if guild is None:
        await interaction.response.send_message(
            "This command must be run in the server.",
            ephemeral=True,
        )
        return

    await interaction.response.defer(ephemeral=True)

    try:
        summary = await run_full_member_sync_for_guild(guild)
        await interaction.followup.send(
            f"✅ Member sync complete.\nProcessed: {summary.get('processed', 0)}\n"
            f"Failed: {summary.get('failed', 0)}\n"
            f"Seen: {summary.get('total_seen', 0)}",
            ephemeral=True,
        )
    except Exception as e:
        await interaction.followup.send(
            f"❌ Member sync failed: {repr(e)}",
            ephemeral=True,
        )


@bot.tree.command(name="reconcile_departed_members", description="Mark missing users as departed in the dashboard database.")
async def reconcile_departed_members(interaction: discord.Interaction):
    if not _is_staff(interaction):
        await interaction.response.send_message(
            "You do not have permission to use this command.",
            ephemeral=True,
        )
        return

    guild = interaction.guild
    if guild is None:
        await interaction.response.send_message(
            "This command must be run in the server.",
            ephemeral=True,
        )
        return

    await interaction.response.defer(ephemeral=True)

    try:
        summary = await run_departed_reconciliation_for_guild(guild)
        await interaction.followup.send(
            f"✅ Departed reconciliation complete.\nChecked: {summary.get('checked', 0)}\n"
            f"Marked departed: {summary.get('marked_departed', 0)}",
            ephemeral=True,
        )
    except Exception as e:
        await interaction.followup.send(
            f"❌ Departed reconciliation failed: {repr(e)}",
            ephemeral=True,
        )