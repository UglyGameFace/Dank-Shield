from __future__ import annotations

"""Public /dank members server-activity review commands.

This first production slice is intentionally scan-only:
- It previews verified/resident members who look quiet after verification.
- It explains confidence and safety status.
- It does not perform member removal.

Important accuracy rule:
- This does NOT use Discord online/offline presence.
- Users can appear offline, so presence would be misleading.
- The scan only uses server-observed activity Dank Shield can see inside this guild.
- Discord audit log is only a fallback for estimating when Verified/Resident was granted.
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
        return "✅ No verified/resident users were found with missing post-verification activity under these settings."

    lines: list[str] = []
    for idx, candidate in enumerate(report.candidates[:limit], start=1):
        days = "unknown" if candidate.inactivity_days is None else f"{candidate.inactivity_days} day(s)"
        verified_at = "unknown"
        post_verify_activity = "none found"
        try:
            if getattr(candidate, "verified_at", None) is not None:
                verified_at = f"<t:{int(candidate.verified_at.timestamp())}:R>"
            if getattr(candidate, "post_verification_activity_at", None) is not None:
                post_verify_activity = f"<t:{int(candidate.post_verification_activity_at.timestamp())}:R>"
        except Exception:
            pass

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

        verified_tag = " • Verified/Resident" if getattr(candidate, "verified_or_resident", False) else ""
        source = str(getattr(candidate, "verification_source", "unknown") or "unknown")
        lines.append(
            f"{idx}. {icon} **{candidate.display_name}** (`{candidate.user_id}`){verified_tag}\n"
            f"   Status: **{label}** • Confidence: **{candidate.confidence}** • Quiet after verify for: **{days}**\n"
            f"   Verified/resident since: {verified_at} • Post-verify activity: {post_verify_activity}\n"
            f"   Verification date source: `{source[:70]}`\n"
            f"   Why: {candidate.short_reason(180)}"
        )

    extra = len(report.candidates) - limit
    if extra > 0:
        lines.append(f"…and **{extra}** more user(s) found for review.")

    return _trim("\n".join(lines))


def _build_activity_meter(report: InactiveScanReport) -> str:
    return (
        f"**Overall server activity:** {report.active_activity_percent}% active/recent in this server\n"
        f"**Verified/resident with no post-verify activity:** {report.verified_resident_without_post_activity}/{report.verified_resident_seen} ({report.verified_vanished_percent}%)\n"
        f"**Users found for review:** {len(report.candidates)} user(s), {report.quiet_review_percent}% of members\n"
        f"**Safety locks:** {report.protected_or_blocked_count} member(s) protected or blocked by Discord permissions\n"
        f"**Data confidence:** {report.data_confidence_label} — {report.data_coverage_percent}% of optional history sources readable"
    )


def _build_data_limits_text(report: InactiveScanReport) -> str:
    extra = ""
    try:
        if report.audit_log_times_found:
            extra = f"\n\nAudit-log fallback found **{report.audit_log_times_found}** verification/resident role timestamp(s)."
    except Exception:
        pass
    if not report.data_warnings:
        return ("✅ Good enough data coverage for this scan." + extra)[:1024]
    intro = (
        "Dank Shield could not read every optional server-history source yet. "
        "That does **not** mean members are inactive. It means low-confidence users are shown for manual review instead of hidden."
    )
    warnings = "\n".join(f"• {warning}" for warning in report.data_warnings[:4])
    return _trim(f"{intro}{extra}\n\n{warnings}", 1024)


def _build_report_embed(report: InactiveScanReport) -> discord.Embed:
    color = discord.Color.green() if report.data_confidence_label in {"Good", "Partial"} else discord.Color.orange()
    embed = discord.Embed(
        title="🧹 Verified Member Activity Review",
        description=(
            "This is a **preview only**. Nobody is removed from the server.\n\n"
            "Dank Shield looks for verified/resident members who verified, then had no tracked server activity afterward. "
            "It does **not** use online/offline/idle status."
        ),
        color=color,
        timestamp=report.scanned_at,
    )
    embed.add_field(name="Users Found", value=_candidate_lines(report), inline=False)
    embed.add_field(name="Activity Health", value=_build_activity_meter(report), inline=False)
    embed.add_field(name="Scan Counts", value="\n".join(report_summary_lines(report)[5:]), inline=False)
    embed.add_field(
        name="Review Settings",
        value=(
            f"Quiet after verification for: **{report.options.inactive_days} day(s)**\n"
            f"New-member grace period: **{report.options.grace_days} day(s)**\n"
            f"Verified/resident focus: **{'Yes' if report.options.verified_resident_focus else 'No'}**\n"
            f"Audit-log fallback for verification date: **{'Yes' if report.options.use_audit_log_fallback else 'No'}**\n"
            f"Bot accounts protected: **{'Yes' if report.options.protect_bots else 'No'}**\n"
            f"Staff/admin roles protected: **{'Yes' if report.options.protect_staff else 'No'}**"
        ),
        inline=False,
    )
    embed.add_field(name="Data Confidence", value=_build_data_limits_text(report), inline=False)
    embed.add_field(
        name="How To Read This",
        value=(
            "🟠 **Review candidate** = verified/resident and quiet after verification with enough data to inspect.\n"
            "🟡 **Needs manual review** = user was found, but verification/activity history is limited.\n"
            "🛡️ **Safety-protected** = owner, bot, staff/admin, protected role, or new member.\n"
            "⛔ **Cannot action** = Discord role hierarchy or permission issue."
        ),
        inline=False,
    )
    embed.set_footer(text=f"Guild {report.guild_id} • /dank members scan • post-verification server activity only")
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
            "This scan checks **post-verification activity inside this server only**. It does **not** use online/offline/idle status.\n\n"
            "For verified/resident members, Dank Shield tries to find when the role was granted from its own records first. If that is missing, it can use Discord audit log as a fallback.\n\n"
            "Dank Shield protects the server owner, the bot itself, bot accounts by default, staff/admin-style roles, configured protected roles, and new members inside the grace period.\n\n"
            "Normal verified/member/resident roles are **not** treated as cleanup-protected by default. They are the group being reviewed."
        )
        await reply_once(interaction, {"content": text, "ephemeral": True})


async def _run_activity_scan(
    interaction: discord.Interaction,
    *,
    inactive_days: int = 90,
    grace_days: int = 14,
    include_low_confidence: bool = True,
    use_audit_log_fallback: bool = True,
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
        use_audit_log_fallback=bool(use_audit_log_fallback),
    )
    report = await scan_inactive_members(interaction.guild, options)
    await interaction.followup.send(
        embed=_build_report_embed(report),
        view=MemberActivityReviewView(report),
        ephemeral=True,
        allowed_mentions=discord.AllowedMentions.none(),
    )


@members_group.command(name="inactive", description="Open a preview-only verified member activity review.")
async def members_inactive(interaction: discord.Interaction) -> None:
    await _run_activity_scan(interaction)


@members_group.command(name="scan", description="Preview verified/resident members with no post-verification activity.")
@app_commands.describe(
    inactive_days="Verified/resident members quiet this many days after verification are shown.",
    grace_days="Protect members newer than this many days.",
    include_low_confidence="Show low-confidence users as Needs manual review. Default: true.",
    use_audit_log_fallback="Use Discord audit log to estimate when Verified/Resident was added. Default: true.",
)
async def members_scan(
    interaction: discord.Interaction,
    inactive_days: int = 90,
    grace_days: int = 14,
    include_low_confidence: bool = True,
    use_audit_log_fallback: bool = True,
) -> None:
    await _run_activity_scan(
        interaction,
        inactive_days=inactive_days,
        grace_days=grace_days,
        include_low_confidence=include_low_confidence,
        use_audit_log_fallback=use_audit_log_fallback,
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
            print("✅ public_members_group: attached /dank members post-verification activity review commands")
        else:
            print("✅ public_members_group: /dank members already attached")
        _REGISTERED = True
    except Exception as e:
        print(f"⚠️ public_members_group failed attaching /dank members: {repr(e)}")
        raise


__all__ = ["register_public_members_group_commands", "members_group"]
