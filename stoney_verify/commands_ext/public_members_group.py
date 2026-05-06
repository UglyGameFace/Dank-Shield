from __future__ import annotations

"""Public /dank members activity review commands.

This first production slice is intentionally scan-only:
- It previews inactive/quiet members.
- It explains confidence and safety status.
- It does not perform member removal.

Removal/cleanup execution should be a later reviewed PR after the preview data is
trusted in live servers.
"""

from typing import Any

import discord
from discord import app_commands

from .common import reply_once
from .public_setup_group import stoney_group
from stoney_verify.members_new.activity_service import (
    InactiveScanOptions,
    InactiveScanReport,
    get_last_scan,
    report_summary_lines,
    scan_inactive_members,
)


members_group = app_commands.Group(
    name="members",
    description="Member activity review tools.",
)

_REGISTERED = False


def _can_review_members(interaction: discord.Interaction) -> bool:
    try:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            return False
        perms = interaction.user.guild_permissions
        return bool(perms.administrator or perms.manage_guild or perms.kick_members or perms.moderate_members)
    except Exception:
        return False


async def _require_review_permission(interaction: discord.Interaction) -> bool:
    if interaction.guild is None:
        await reply_once(interaction, {"content": "❌ This must be used inside a server.", "ephemeral": True})
        return False
    if not _can_review_members(interaction):
        await reply_once(
            interaction,
            {"content": "❌ Member activity review requires Administrator, Manage Server, Kick Members, or Moderate Members.", "ephemeral": True},
        )
        return False
    return True


def _trim(text: str, limit: int = 3900) -> str:
    raw = str(text or "")
    return raw if len(raw) <= limit else raw[: max(0, limit - 1)] + "…"


def _candidate_lines(report: InactiveScanReport, *, limit: int = 10) -> str:
    if not report.candidates:
        return "✅ No quiet/inactive members matched these settings."

    lines: list[str] = []
    for idx, candidate in enumerate(report.candidates[:limit], start=1):
        days = "unknown" if candidate.inactivity_days is None else f"{candidate.inactivity_days} day(s)"
        last_seen = "unknown"
        try:
            if candidate.last_seen_at is not None:
                last_seen = f"<t:{int(candidate.last_seen_at.timestamp())}:R>"
        except Exception:
            last_seen = "unknown"

        if candidate.status == "Removable":
            icon = "🟠"
            label = "Review candidate"
        elif candidate.status == "Needs review":
            icon = "🟡"
            label = "Needs manual review"
        elif candidate.status == "Protected":
            icon = "🛡️"
            label = "Protected"
        elif candidate.status == "Cannot remove":
            icon = "⛔"
            label = "Cannot action"
        else:
            icon = "⚪"
            label = candidate.status

        lines.append(
            f"{idx}. {icon} **{candidate.display_name}** (`{candidate.user_id}`)\n"
            f"   Status: **{label}** • Confidence: **{candidate.confidence}** • Quiet for: **{days}** • Last seen: {last_seen}\n"
            f"   Why: {candidate.short_reason(180)}"
        )

    extra = len(report.candidates) - limit
    if extra > 0:
        lines.append(f"…and **{extra}** more member(s) in this review.")

    return _trim("\n".join(lines))


def _build_report_embed(report: InactiveScanReport) -> discord.Embed:
    embed = discord.Embed(
        title="🧹 Member Activity Review",
        description=(
            "This is a **preview only**. Nobody is removed from the server.\n\n"
            "Dank Shield looks at the activity data it can safely read, then explains who looks quiet, protected, blocked by role hierarchy, or needs manual review."
        ),
        color=discord.Color.blurple(),
        timestamp=report.scanned_at,
    )
    embed.add_field(name="Summary", value="\n".join(report_summary_lines(report)), inline=False)
    embed.add_field(
        name="Review Settings",
        value=(
            f"Quiet after: **{report.options.inactive_days} day(s)**\n"
            f"New-member grace period: **{report.options.grace_days} day(s)**\n"
            f"Bot accounts protected: **{'Yes' if report.options.protect_bots else 'No'}**\n"
            f"Staff/protected roles protected: **{'Yes' if report.options.protect_staff else 'No'}**"
        ),
        inline=False,
    )
    if report.data_warnings:
        embed.add_field(
            name="Data Limits",
            value=_trim("\n".join(f"• {warning}" for warning in report.data_warnings[:6]), 1024),
            inline=False,
        )
    embed.add_field(name="Top Review Items", value=_candidate_lines(report), inline=False)
    embed.add_field(
        name="How To Read This",
        value=(
            "🟠 **Review candidate** = looks quiet with enough data to review.\n"
            "🟡 **Needs manual review** = not enough history for an automatic decision.\n"
            "🛡️ **Protected** = staff, protected role, bot, server owner, or new member.\n"
            "⛔ **Cannot action** = Discord role hierarchy or permission issue."
        ),
        inline=False,
    )
    embed.set_footer(text=f"Guild {report.guild_id} • /dank members scan")
    return embed


class MemberActivityReviewView(discord.ui.View):
    def __init__(self, report: InactiveScanReport) -> None:
        super().__init__(timeout=600)
        self.report = report

    @discord.ui.button(label="Refresh Scan", emoji="🔄", style=discord.ButtonStyle.primary)
    async def refresh_scan(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_review_permission(interaction):
            return
        if interaction.guild is None:
            return
        await interaction.response.defer(ephemeral=True)
        report = await scan_inactive_members(interaction.guild, self.report.options)
        await interaction.edit_original_response(embed=_build_report_embed(report), view=MemberActivityReviewView(report))

    @discord.ui.button(label="Explain Safety", emoji="🛡️", style=discord.ButtonStyle.secondary)
    async def explain_safety(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        text = (
            "🛡️ **Safety rules used by this scan**\n\n"
            "Dank Shield protects the server owner, the bot itself, bot accounts by default, staff/admin-style roles, configured protected roles, and new members inside the grace period.\n\n"
            "It also checks whether the bot has permission and role hierarchy before marking a member as action-ready. This screen is preview-only."
        )
        await reply_once(interaction, {"content": text, "ephemeral": True})


async def _run_activity_scan(
    interaction: discord.Interaction,
    *,
    inactive_days: int = 90,
    grace_days: int = 14,
    include_low_confidence: bool = False,
) -> None:
    if not await _require_review_permission(interaction):
        return
    if interaction.guild is None:
        return

    await interaction.response.defer(ephemeral=True, thinking=True)
    options = InactiveScanOptions(
        inactive_days=max(7, min(int(inactive_days), 730)),
        grace_days=max(1, min(int(grace_days), 90)),
        include_low_confidence=bool(include_low_confidence),
    )
    report = await scan_inactive_members(interaction.guild, options)
    await interaction.followup.send(
        embed=_build_report_embed(report),
        view=MemberActivityReviewView(report),
        ephemeral=True,
        allowed_mentions=discord.AllowedMentions.none(),
    )


@members_group.command(name="inactive", description="Open a preview-only inactive member activity review.")
async def members_inactive(interaction: discord.Interaction) -> None:
    await _run_activity_scan(interaction)


@members_group.command(name="scan", description="Preview quiet/inactive members without changing the server.")
@app_commands.describe(
    inactive_days="Members quiet this many days are shown for review.",
    grace_days="Protect members newer than this many days.",
    include_low_confidence="Show low-confidence results when the bot has limited history.",
)
async def members_scan(
    interaction: discord.Interaction,
    inactive_days: int = 90,
    grace_days: int = 14,
    include_low_confidence: bool = False,
) -> None:
    await _run_activity_scan(
        interaction,
        inactive_days=inactive_days,
        grace_days=grace_days,
        include_low_confidence=include_low_confidence,
    )


@members_group.command(name="last-scan", description="Show the latest member activity review since the bot started.")
async def members_last_scan(interaction: discord.Interaction) -> None:
    if not await _require_review_permission(interaction):
        return
    if interaction.guild is None:
        return
    report = get_last_scan(interaction.guild.id)
    if report is None:
        return await reply_once(
            interaction,
            {"content": "No activity review has been run since the bot started. Use `/dank members scan` first.", "ephemeral": True},
        )
    await interaction.response.send_message(
        embed=_build_report_embed(report),
        view=MemberActivityReviewView(report),
        ephemeral=True,
        allowed_mentions=discord.AllowedMentions.none(),
    )


def register_public_members_group_commands(bot: Any, tree: Any) -> None:
    global _REGISTERED
    _ = bot, tree
    if _REGISTERED:
        return

    try:
        if stoney_group.get_command("members") is None:
            stoney_group.add_command(members_group)
            print("✅ public_members_group: attached /dank members activity review commands")
        else:
            print("✅ public_members_group: /dank members already attached")
        _REGISTERED = True
    except Exception as e:
        print(f"⚠️ public_members_group failed attaching /dank members: {repr(e)}")
        raise


__all__ = ["register_public_members_group_commands", "members_group"]
