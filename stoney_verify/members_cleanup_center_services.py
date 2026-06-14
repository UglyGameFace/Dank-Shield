from __future__ import annotations

"""Owned service entrypoints for the Cleanup + Members Feature Center."""

from typing import Any

import discord


async def _send_ephemeral(interaction: discord.Interaction, content: str = "", **kwargs: Any) -> None:
    try:
        if not interaction.response.is_done():
            await interaction.response.send_message(content, ephemeral=True, **kwargs)
        else:
            await interaction.followup.send(content, ephemeral=True, **kwargs)
    except Exception:
        pass


class CleanupMembersCenterView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=900)

    @discord.ui.button(label="Run 90d Review", emoji="🎯", style=discord.ButtonStyle.primary, custom_id="dank_setup_members:scan90", row=0)
    async def scan_90(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        from stoney_verify.commands_ext import public_members_group as members

        await members._run_activity_scan(interaction, inactive_days=90, grace_days=14, include_low_confidence=True, use_audit_log_fallback=True, skip_locked_users=True)

    @discord.ui.button(label="Run 30d Review", emoji="⚡", style=discord.ButtonStyle.secondary, custom_id="dank_setup_members:scan30", row=0)
    async def scan_30(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        from stoney_verify.commands_ext import public_members_group as members

        await members._run_activity_scan(interaction, inactive_days=30, grace_days=14, include_low_confidence=True, use_audit_log_fallback=True, skip_locked_users=True)

    @discord.ui.button(label="Last Scan", emoji="📋", style=discord.ButtonStyle.secondary, custom_id="dank_setup_members:last", row=1)
    async def last_scan(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        from stoney_verify.commands_ext import public_members_group as members

        if not await members._require_review_permission(interaction):
            return
        if interaction.guild is None:
            return
        report = members.get_last_scan(interaction.guild.id)
        if report is None:
            return await _send_ephemeral(interaction, "No server-activity review has been run since the bot started. Press **Run 90d Review** first.")
        await _send_ephemeral(
            interaction,
            embed=members._build_report_embed(report, page=0),
            view=members.MemberActivityReviewView(report, page=0),
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @discord.ui.button(label="Locked Users", emoji="🔒", style=discord.ButtonStyle.secondary, custom_id="dank_setup_members:locked", row=1)
    async def locked_users(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        from stoney_verify.commands_ext import public_members_group as members

        if not await members._require_review_permission(interaction):
            return
        if interaction.guild is None:
            return
        records, persistence = await members.get_scan_lock_records(int(interaction.guild.id))
        await _send_ephemeral(
            interaction,
            embed=members._build_locked_users_embed(records, persistence, guild=interaction.guild, page=0),
            view=members.LockedUsersReviewView(records, persistence, guild=interaction.guild),
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @discord.ui.button(label="Notice Results", emoji="📊", style=discord.ButtonStyle.secondary, custom_id="dank_setup_members:notices", row=2)
    async def notice_results(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        from stoney_verify.commands_ext import public_members_group as members

        if not await members._require_review_permission(interaction):
            return
        if interaction.guild is None:
            return
        await _send_ephemeral(
            interaction,
            embed=members._notice_results_embed(interaction.guild),
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @discord.ui.button(label="Cleanup Settings", emoji="⚙️", style=discord.ButtonStyle.secondary, custom_id="dank_setup_members:settings", row=2)
    async def cleanup_settings(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        from stoney_verify.commands_ext import public_members_cleanup_group as cleanup
        from stoney_verify.members_new.cleanup_settings_service import get_cleanup_settings

        if not await cleanup._require_cleanup_settings_permission(interaction):
            return
        if interaction.guild is None:
            return
        settings = await get_cleanup_settings(int(interaction.guild.id))
        embed = cleanup._settings_embed(settings)
        embed.title = "🧹 Cleanup Settings"
        await _send_ephemeral(interaction, embed=embed, allowed_mentions=discord.AllowedMentions.none())


async def open_cleanup_members_center(interaction: discord.Interaction) -> None:
    from stoney_verify.commands_ext import public_members_group as members

    if not await members._require_review_permission(interaction):
        return
    embed = discord.Embed(
        title="🧹 Cleanup + Members Center",
        description=(
            "Review inactive verified/resident members, manage scan locks, and check cleanup settings from setup.\n\n"
            "This center is preview-first. It does not purge anyone from the home panel. Cleanup actions still use explicit confirmation flows."
        ),
        color=discord.Color.blurple(),
        timestamp=discord.utils.utcnow(),
    )
    embed.add_field(
        name="Safety rules",
        value=(
            "• Uses server activity evidence, not online/offline presence.\n"
            "• Low-confidence users are manual-review, not purge-safe by default.\n"
            "• Locked users are skipped from future scans.\n"
            "• Purge/cleanup actions keep their own confirmation and final validation gates."
        ),
        inline=False,
    )
    embed.add_field(name="Start here", value="Run **90d Review** for a normal cleanup review, or **30d Review** for a faster check.", inline=False)
    embed.set_footer(text="/dank setup • Feature Centers • Cleanup + Members")
    await _send_ephemeral(interaction, embed=embed, view=CleanupMembersCenterView(), allowed_mentions=discord.AllowedMentions.none())


__all__ = ["open_cleanup_members_center", "CleanupMembersCenterView"]
