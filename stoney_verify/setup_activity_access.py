from __future__ import annotations

"""Read-only setup UI for Dank Shield activity-tracking access coverage.

`/dank setup -> Other Settings -> Logs & Safety -> Check Bot Access` uses the
same shared activity-scope audit as inactivity safety diagnostics. This module
never edits Discord permissions. Owners can deliberately open the existing
preview-first Fix Channel Permissions tool if they want to repair access.
"""

from typing import Any

import discord

from .members_new.activity_scope import (
    ActivityScopeProblem,
    ActivityScopeReport,
    audit_activity_scope,
)


def _problem_line(problem: ActivityScopeProblem) -> str:
    missing = ", ".join(problem.missing_permissions) or "Unknown permission"
    return f"• {problem.display_name} (`{problem.channel_id}`) — **{missing}**"


def _coverage_status(report: ActivityScopeReport) -> str:
    if not report.bot_member_resolved:
        return (
            "🚫 Dank Shield could not resolve its own bot member in this server, "
            "so activity coverage cannot be verified safely."
        )
    if report.complete:
        return (
            f"✅ **100% activity scope** — {report.accessible_channels}/{report.total_channels} "
            "message channels and active threads are inspectable."
        )
    return (
        f"⚠️ **{report.coverage_percent}% activity scope** — "
        f"{report.accessible_channels}/{report.total_channels} inspectable."
    )


def build_activity_access_embed(report: ActivityScopeReport) -> discord.Embed:
    color = discord.Color.green() if report.complete else discord.Color.orange()
    if not report.bot_member_resolved:
        color = discord.Color.red()

    embed = discord.Embed(
        title="🔐 Check Bot Access",
        description=(
            "Checks whether Dank Shield can actually inspect the channels needed for accurate "
            "member activity and inactivity tracking. **This check is read-only and changes nothing.**"
        ),
        color=color,
    )
    embed.add_field(name="Coverage", value=_coverage_status(report), inline=False)

    if report.problems:
        lines = [_problem_line(problem) for problem in report.problems[:12]]
        if len(report.problems) > 12:
            lines.append(f"…and {len(report.problems) - 12} more affected channel(s).")
        embed.add_field(
            name="Missing Bot Access",
            value="\n".join(lines)[:1024],
            inline=False,
        )
        embed.add_field(
            name="Why this matters",
            value=(
                "Dank Shield will **not** treat inactivity evidence as purge-safe while authoritative "
                "activity coverage is incomplete. Affected channels can hide real member activity."
            ),
            inline=False,
        )
        embed.add_field(
            name="How to repair it",
            value=(
                "Use **Fix Channel Permissions** to open the existing preview-first repair tool, or "
                "manually grant only the permissions listed above. Dank Shield does not silently grant itself access."
            ),
            inline=False,
        )
    elif report.bot_member_resolved:
        embed.add_field(
            name="Result",
            value="✅ No activity-tracking channel access gaps were detected.",
            inline=False,
        )

    embed.set_footer(text="Read-only access check • no Discord permissions were changed")
    return embed


class ActivityAccessView(discord.ui.View):
    def __init__(self, *, needs_repair: bool) -> None:
        super().__init__(timeout=900)
        self.fix_permissions.disabled = not bool(needs_repair)

    @discord.ui.button(
        label="Check Again",
        emoji="🔍",
        style=discord.ButtonStyle.primary,
        custom_id="dank_setup_bot_access:check_again",
        row=0,
    )
    async def check_again(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await open_activity_access_check(interaction)

    @discord.ui.button(
        label="Fix Channel Permissions",
        emoji="🛠️",
        style=discord.ButtonStyle.secondary,
        custom_id="dank_setup_bot_access:fix_permissions",
        row=0,
    )
    async def fix_permissions(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        from stoney_verify import setup_permission_repair_services

        await setup_permission_repair_services.open_permission_repair(interaction)

    @discord.ui.button(
        label="Back to Logs & Safety",
        emoji="↩️",
        style=discord.ButtonStyle.secondary,
        custom_id="dank_setup_bot_access:back",
        row=1,
    )
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        from stoney_verify.commands_ext import public_setup_recommend as recommend

        await recommend._open_advanced_monitoring_repair(interaction)

    @discord.ui.button(
        label="Back Home",
        emoji="🏠",
        style=discord.ButtonStyle.secondary,
        custom_id="dank_setup_bot_access:home",
        row=1,
    )
    async def home(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        from stoney_verify.commands_ext import public_setup_recommend as recommend

        await recommend._home_edit(interaction)


async def open_activity_access_check(interaction: discord.Interaction) -> None:
    from stoney_verify.commands_ext import public_setup_solid as solid

    if not await solid._require_setup_permission(interaction):
        return

    guild = interaction.guild
    if guild is None:
        if not interaction.response.is_done():
            await interaction.response.send_message(
                "❌ This must be used inside a server.",
                ephemeral=True,
            )
        return

    await solid._safe_defer_update(interaction)
    report = audit_activity_scope(guild)
    await solid._edit_or_followup(
        interaction,
        embed=build_activity_access_embed(report),
        view=ActivityAccessView(needs_repair=not report.complete),
    )


__all__ = [
    "ActivityAccessView",
    "build_activity_access_embed",
    "open_activity_access_check",
]
