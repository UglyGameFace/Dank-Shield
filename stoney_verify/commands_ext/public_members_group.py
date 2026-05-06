from __future__ import annotations

"""Public /dank members server-activity review commands.

This first production slice is intentionally scan-only:
- It previews members who look quiet inside this server.
- It explains confidence and safety status.
- It does not perform member removal.

Important accuracy rule:
- This does NOT use Discord online/offline presence.
- Users can appear offline, so presence would be misleading.
- The scan only uses server-observed activity Dank Shield can see inside this guild.
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
    description="Member server-activity review tools.",
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
        return "✅ No users were found for review with these settings."

    lines: list[str] = []
    for idx, candidate in enumerate(report.candidates[:limit], start=1):
        days = "unknown" if candidate.inactivity_days is None else f"{candidate.inactivity_days} day(s)"
        last_server_activity = "unknown"
        try:
            if candidate.last_seen_at is not None:
                last_server_activity = f"<t:{int(candidate.last_seen_at.timestamp())}:R>"
        except Exception:
            last_server_activity = "unknown"

        if candidate.status == "Review candidate":
            icon = "🟠"
            label = "Review candidate"
        elif candidate.status == "Needs review":
            icon = "🟡"
            label = "Needs manual review"
        elif candidate.status == "Protected":
            icon = "🛡️"
            label = "Safety-protected"
        elif candidate.status == "Cannot action":
            icon = "⛔"
            label = "Cannot action"
        else:
            icon = "⚪"
            label = candidate.status

        lines.append(
            f"{idx}. {icon} **{candidate.display_name}** (`{candidate.user_id}`)\n"
            f"   Status: **{label}** • Confidence: **{candidate.confidence}** • Quiet in server for: **{days}**\n"
            f"   Last server activity: {last_server_activity}\n"
            f"   Why: {candidate.short_reason(180)}"
        )

    extra = len(report.candidates) - limit
    if extra > 0:
        lines.append(f"…and **{extra}** more user(s) found for review.")

    return _trim("\n".join(lines))


def _build_activity_meter(report: InactiveScanReport) -> str:
    return (
        f"**Overall server activity:** {report.active_activity_percent}% active/recent in this server\n"
        f"**Users found for review:** {len(report.candidates)} user(s), {report.quiet_review_percent}% of members\n"
        f"**Safety locks:** {report.protected_or_blocked_count} member(s) protected or blocked by Discord permissions\n"
        f"**Data confidence:** {report.data_confidence_label} — {report.data_coverage_percent}% of optional history sources readable"
    )


def _build_data_limits_text(report: InactiveScanReport) -> str:
    if not report.data_warnings:
        return "✅ Good enough data coverage for this scan."
    intro = (
        "Dank Shield could not read every optional server-history source yet. "
        "That does **not** mean members are inactive. It means low-confidence users are shown for manual review instead of hidden."
    )
    warnings = "\n".join(f"• {warning}" for warning in report.data_warnings[:4])
    return _trim(f"{intro}\n\n{warnings}", 1024)


def _build_report_embed(report: InactiveScanReport) -> discord.Embed:
    color = discord.Color.green() if report.data_confidence_label in {"Good", "Partial"} else discord.Color.orange()
    embed = discord.Embed(
        title="🧹 Member Server Activity Review",
        description=(
            "This is a **preview only**. Nobody is removed from the server.\n\n"
            "Dank Shield does **not** use online/offline/idle status. People can appear offline, so that would be misleading. "
            "This scan only uses activity Dank Shield can observe inside this server."
        ),
        color=color,
        timestamp=report.scanned_at,
    )
    embed.add_field(name="Users Found", value=_candidate_lines(report), inline=False)
    embed.add_field(name="Activity Health", value=_build_activity_meter(report), inline=False)
    embed.add_field(name="Scan Counts", value="\n".join(report_summary_lines(report)[4:]), inline=False)
    embed.add_field(
        name="Review Settings",
        value=(
            f"Quiet after: **{report.options.inactive_days} day(s) without tracked server activity**\n"
            f"New-member grace period: **{report.options.grace_days} day(s)**\n"
            f"Bot accounts protected: **{'Yes' if report.options.protect_bots else 'No'}**\n"
            f"Staff/admin roles protected: **{'Yes' if report.options.protect_staff else 'No'}**"
        ),
        inline=False,
    )
    embed.add_field(name="Data Confidence", value=_build_data_limits_text(report), inline=False)
    embed.add_field(
        name="How To Read This",
        value=(
            "🟠 **Review candidate** = quiet in this server with enough data to inspect.\n"
            "🟡 **Needs manual review** = user was found, but server-history data is limited.\n"
            "🛡️ **Safety-protected** = owner, bot, staff/admin, protected role, or new member.\n"
            "⛔ **Cannot action** = Discord role hierarchy or permission issue."
        ),
        inline=False,
    )
    embed.set_footer(text=f"Guild {report.guild_id} • /dank members scan • server activity only, not presence")
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
            "This scan checks activity inside this server only. It does **not** use online/offline/idle status.\n\n"
            "Dank Shield protects the server owner, the bot itself, bot accounts by default, staff/admin-style roles, configured protected roles, and new members inside the grace period.\n\n"
            "Normal verified/member roles are **not** treated as cleanup-protected by default.\n\n"
            "Low-confidence users are still shown because this is a preview dashboard. Low confidence means manual review, not hidden results."
        )
        await reply_once(interaction, {"content": text, "ephemeral": True})


async def _run_activity_scan(
    interaction: discord.Interaction,
    *,
    inactive_days: int = 90,
    grace_days: int = 14,
    include_low_confidence: bool = True,
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


@members_group.command(name="inactive", description="Open a preview-only server-activity review.")
async def members_inactive(interaction: discord.Interaction) -> None:
    await _run_activity_scan(interaction)


@members_group.command(name="scan", description="Preview quiet/inactive members using server activity only.")
@app_commands.describe(
    inactive_days="Members quiet in this server this many days are shown for review.",
    grace_days="Protect members newer than this many days.",
    include_low_confidence="Show low-confidence users as Needs manual review. Default: true.",
)
async def members_scan(
    interaction: discord.Interaction,
    inactive_days: int = 90,
    grace_days: int = 14,
    include_low_confidence: bool = True,
) -> None:
    await _run_activity_scan(
        interaction,
        inactive_days=inactive_days,
        grace_days=grace_days,
        include_low_confidence=include_low_confidence,
    )


@members_group.command(name="last-scan", description="Show the latest member server-activity review since the bot started.")
async def members_last_scan(interaction: discord.Interaction) -> None:
    if not await _require_review_permission(interaction):
        return
    if interaction.guild is None:
        return
    report = get_last_scan(interaction.guild.id)
    if report is None:
        return await reply_once(
            interaction,
            {"content": "No server-activity review has been run since the bot started. Use `/dank members scan` first.", "ephemeral": True},
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
            print("✅ public_members_group: attached /dank members server-activity review commands")
        else:
            print("✅ public_members_group: /dank members already attached")
        _REGISTERED = True
    except Exception as e:
        print(f"⚠️ public_members_group failed attaching /dank members: {repr(e)}")
        raise


__all__ = ["register_public_members_group_commands", "members_group"]
