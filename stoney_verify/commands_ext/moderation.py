from __future__ import annotations

from typing import Optional

import discord
from discord import app_commands

from ..globals import *  # noqa: F401,F403
from ..globals import now_utc

from .common import _staff_check, RUNTIME_STATS


def register_moderation_commands(bot, tree) -> None:
    # ============================================================
    # /mod_kick
    # ============================================================
    @tree.command(
        name="mod_kick",
        description="(Staff) Kick a member.",
    )
    @app_commands.describe(
        member="Member to kick",
        reason="Reason (optional)",
    )
    async def mod_kick_slash(
        interaction: discord.Interaction,
        member: discord.Member,
        reason: Optional[str] = None,
    ):
        if not _staff_check(interaction):
            return await interaction.response.send_message("❌ Staff only.", ephemeral=True)

        await interaction.response.defer(ephemeral=True)

        guild = interaction.guild
        if not guild or not guild.me:
            return await interaction.followup.send("❌ Invalid context.", ephemeral=True)

        me = guild.me
        if not me.guild_permissions.kick_members:
            return await interaction.followup.send(
                "❌ I lack **Kick Members** permission.",
                ephemeral=True,
            )

        try:
            if me.top_role <= member.top_role and not me.guild_permissions.administrator:
                return await interaction.followup.send(
                    "❌ I can’t kick that member (role hierarchy).",
                    ephemeral=True,
                )
        except Exception:
            pass

        try:
            await guild.kick(
                member,
                reason=reason or f"Kick by {interaction.user} ({interaction.user.id})",
            )
            try:
                RUNTIME_STATS["mod_actions"] += 1
            except Exception:
                pass

            return await interaction.followup.send(
                f"👢 Kicked {member.mention}.",
                ephemeral=True,
            )

        except discord.Forbidden:
            return await interaction.followup.send(
                "❌ Forbidden (permissions/hierarchy).",
                ephemeral=True,
            )
        except Exception as e:
            return await interaction.followup.send(
                f"❌ Error: {e}",
                ephemeral=True,
            )

    # ============================================================
    # /mod_ban
    # ============================================================
    @tree.command(
        name="mod_ban",
        description="(Staff) Ban a member.",
    )
    @app_commands.describe(
        member="Member to ban",
        reason="Reason (optional)",
        delete_message_days="Delete message days (0-7)",
    )
    async def mod_ban_slash(
        interaction: discord.Interaction,
        member: discord.Member,
        reason: Optional[str] = None,
        delete_message_days: Optional[int] = 0,
    ):
        if not _staff_check(interaction):
            return await interaction.response.send_message("❌ Staff only.", ephemeral=True)

        await interaction.response.defer(ephemeral=True)

        guild = interaction.guild
        if not guild or not guild.me:
            return await interaction.followup.send("❌ Invalid context.", ephemeral=True)

        me = guild.me
        if not me.guild_permissions.ban_members:
            return await interaction.followup.send(
                "❌ I lack **Ban Members** permission.",
                ephemeral=True,
            )

        try:
            if me.top_role <= member.top_role and not me.guild_permissions.administrator:
                return await interaction.followup.send(
                    "❌ I can’t ban that member (role hierarchy).",
                    ephemeral=True,
                )
        except Exception:
            pass

        dmd = int(delete_message_days or 0)
        dmd = max(0, min(7, dmd))

        try:
            await guild.ban(
                member,
                reason=reason or f"Ban by {interaction.user} ({interaction.user.id})",
                delete_message_days=dmd,
            )
            try:
                RUNTIME_STATS["mod_actions"] += 1
            except Exception:
                pass

            return await interaction.followup.send(
                f"🔨 Banned {member.mention}.",
                ephemeral=True,
            )

        except discord.Forbidden:
            return await interaction.followup.send(
                "❌ Forbidden (permissions/hierarchy).",
                ephemeral=True,
            )
        except Exception as e:
            return await interaction.followup.send(
                f"❌ Error: {e}",
                ephemeral=True,
            )

    # ============================================================
    # /mod_timeout
    # ============================================================
    @tree.command(
        name="mod_timeout",
        description="(Staff) Timeout a member.",
    )
    @app_commands.describe(
        member="Member to timeout",
        minutes="Minutes (default MOD_TIMEOUT_MINUTES)",
        reason="Reason (optional)",
    )
    async def mod_timeout_slash(
        interaction: discord.Interaction,
        member: discord.Member,
        minutes: Optional[int] = None,
        reason: Optional[str] = None,
    ):
        if not _staff_check(interaction):
            return await interaction.response.send_message("❌ Staff only.", ephemeral=True)

        await interaction.response.defer(ephemeral=True)

        guild = interaction.guild
        if not guild or not guild.me:
            return await interaction.followup.send("❌ Invalid context.", ephemeral=True)

        me = guild.me
        if not me.guild_permissions.moderate_members:
            return await interaction.followup.send(
                "❌ I lack **Moderate Members** permission.",
                ephemeral=True,
            )

        mins = int(minutes or MOD_TIMEOUT_MINUTES)
        mins = max(1, min(60 * 24 * 28, mins))

        try:
            if me.top_role <= member.top_role and not me.guild_permissions.administrator:
                return await interaction.followup.send(
                    "❌ I can’t timeout that member (role hierarchy).",
                    ephemeral=True,
                )
        except Exception:
            pass

        until = now_utc() + timedelta(minutes=mins)

        try:
            await member.timeout(
                until,
                reason=reason or f"Timeout by {interaction.user} ({interaction.user.id})",
            )
            try:
                RUNTIME_STATS["mod_actions"] += 1
            except Exception:
                pass

            return await interaction.followup.send(
                f"⏳ Timed out {member.mention} for {mins} minutes.",
                ephemeral=True,
            )

        except discord.Forbidden:
            return await interaction.followup.send(
                "❌ Forbidden (permissions/hierarchy).",
                ephemeral=True,
            )
        except Exception as e:
            return await interaction.followup.send(
                f"❌ Error: {e}",
                ephemeral=True,
            )

    # ============================================================
    # /debug_intents
    # ============================================================
    @tree.command(
        name="debug_intents",
        description="Check bot intents and member visibility",
    )
    async def debug_intents(interaction: discord.Interaction):
        if not _staff_check(interaction):
            return await interaction.response.send_message("❌ Staff only.", ephemeral=True)

        await interaction.response.defer(ephemeral=True)

        lines = []
        lines.append(f"**Bot User:** {bot.user}")
        lines.append(f"**Guild ID:** {interaction.guild.id}")
        lines.append(f"**Guild Name:** {interaction.guild.name}")

        intents = bot.intents
        lines.append("**Intents:**")
        lines.append(f"- Guilds: {intents.guilds}")
        lines.append(f"- Members: {intents.members}")
        lines.append(f"- Presence: {intents.presences}")
        lines.append(f"- Message Content: {intents.message_content}")

        try:
            me = interaction.guild.me
            lines.append(f"**Bot Member:** {me} (roles: {len(me.roles)})")

            members = [m for m in interaction.guild.members[:5]]
            lines.append(
                "**First few members in cache:** "
                + (", ".join(str(m) for m in members) if members else "none")
            )
        except Exception as e:
            lines.append(f"❌ Error accessing members: {e}")

        await interaction.followup.send("\n".join(lines), ephemeral=True)