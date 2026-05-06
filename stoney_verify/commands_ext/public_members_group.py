from __future__ import annotations

"""Public /dank members server-activity review commands.

This production slice is intentionally scan/review only:
- It previews verified/resident members who look quiet after verification.
- It explains confidence and safety status.
- It provides manual review controls for staff.
- It does not perform member removal.

Accuracy rule:
- This does NOT use Discord online/offline presence.
- Users can appear offline, so presence would be misleading.
- The scan only uses server-observed activity Dank Shield can see inside this guild.
- Discord audit log is only a fallback for estimating when Verified/Resident was granted.
"""

from math import ceil
from typing import Any, Optional

import discord
from discord import app_commands

from .common import reply_once
from .public_setup_group import stoney_group
from stoney_verify.members_new.activity_service import (
    InactiveScanOptions,
    InactiveMemberCandidate,
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
_FIELD_LIMIT = 1024
_SAFE_FIELD_LIMIT = 950
_PAGE_SIZE = 4


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


def _safe_field(text: str, limit: int = _FIELD_LIMIT) -> str:
    return _trim(str(text or "None"), max(1, int(limit)))


def _fmt_ts(value: Any, *, fallback: str = "unknown") -> str:
    try:
        if value is not None:
            return f"<t:{int(value.timestamp())}:R>"
    except Exception:
        pass
    return fallback


def _status_icon(candidate: Any) -> tuple[str, str]:
    status = str(getattr(candidate, "status", "") or "")
    if status == "Review candidate":
        return "🟠", "Review candidate"
    if status == "Needs review":
        return "🟡", "Needs manual review"
    if status == "Protected":
        return "🛡️", "Safety-protected"
    if status == "Cannot action":
        return "⛔", "Cannot action"
    return "⚪", status or "Unknown"


def _candidate_title(candidate: Any, idx: int) -> str:
    icon, label = _status_icon(candidate)
    verified_tag = " • Verified/Resident" if getattr(candidate, "verified_or_resident", False) else ""
    return f"{idx}. {icon} {candidate.display_name}{verified_tag} — {label}"


def _candidate_summary(candidate: Any, idx: int) -> tuple[str, str]:
    days = "unknown" if candidate.inactivity_days is None else f"{candidate.inactivity_days} day(s)"
    verified_at = _fmt_ts(getattr(candidate, "verified_at", None))
    post_verify_activity = _fmt_ts(getattr(candidate, "post_verification_activity_at", None), fallback="none found")
    source = str(getattr(candidate, "verification_source", "unknown") or "unknown")
    name = _safe_field(_candidate_title(candidate, idx), 256)
    value = _safe_field(
        f"User: {getattr(candidate, 'mention', f'<@{candidate.user_id}>')} (`{candidate.user_id}`)\n"
        f"Confidence: **{candidate.confidence}** • Quiet after verify: **{days}**\n"
        f"Verified/resident since: {verified_at}\n"
        f"Post-verify activity: {post_verify_activity}\n"
        f"Source: `{source[:70]}`\n"
        f"Why: {candidate.short_reason(180)}",
        _SAFE_FIELD_LIMIT,
    )
    return name, value


def _page_count(report: InactiveScanReport) -> int:
    return max(1, ceil(len(report.candidates) / _PAGE_SIZE))


def _page_bounds(report: InactiveScanReport, page: int) -> tuple[int, int, int]:
    pages = _page_count(report)
    safe_page = max(0, min(int(page), pages - 1))
    start = safe_page * _PAGE_SIZE
    end = min(start + _PAGE_SIZE, len(report.candidates))
    return safe_page, start, end


def _build_activity_meter(report: InactiveScanReport) -> str:
    return _safe_field(
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
        return _safe_field("✅ Good enough data coverage for this scan." + extra)
    intro = (
        "Dank Shield could not read every optional server-history source yet. "
        "That does **not** mean members are inactive. It means low-confidence users are shown for manual review instead of hidden."
    )
    warnings = "\n".join(f"• {warning}" for warning in report.data_warnings[:3])
    return _safe_field(f"{intro}{extra}\n\n{warnings}")


def _build_report_embed(report: InactiveScanReport, *, page: int = 0) -> discord.Embed:
    page, start, end = _page_bounds(report, page)
    pages = _page_count(report)
    color = discord.Color.green() if report.data_confidence_label in {"Good", "Partial"} else discord.Color.orange()
    embed = discord.Embed(
        title="🧹 Verified Member Activity Review",
        description=(
            "Preview-only review console. Nobody is removed from the server.\n\n"
            "Dank Shield lists verified/resident members who verified, then had no tracked server activity afterward. "
            "It does **not** use online/offline/idle status."
        ),
        color=color,
        timestamp=report.scanned_at,
    )

    if not report.candidates:
        embed.add_field(
            name="Users Found",
            value="✅ No verified/resident users were found with missing post-verification activity under these settings.",
            inline=False,
        )
    else:
        embed.add_field(
            name=f"Users Found — Page {page + 1}/{pages}",
            value=_safe_field(f"Showing **{start + 1}-{end}** of **{len(report.candidates)}** users found for manual review."),
            inline=False,
        )
        for display_idx, candidate in enumerate(report.candidates[start:end], start=start + 1):
            name, value = _candidate_summary(candidate, display_idx)
            embed.add_field(name=name, value=value, inline=False)

    embed.add_field(name="Activity Health", value=_build_activity_meter(report), inline=False)
    embed.add_field(name="Scan Counts", value=_safe_field("\n".join(report_summary_lines(report)[5:])), inline=False)
    embed.add_field(
        name="Manual Review Options",
        value=_safe_field(
            "Use the dropdown below to inspect one user at a time. The detail card gives the member mention, ID, verification date source, post-verification activity, and manual next steps.\n\n"
            "This screen does not automatically remove anyone. That keeps review safe until the list is trusted."
        ),
        inline=False,
    )
    embed.add_field(
        name="Review Settings",
        value=_safe_field(
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
    embed.set_footer(text=f"Guild {report.guild_id} • /dank members scan • post-verification server activity only")
    return embed


def _build_user_detail_embed(candidate: InactiveMemberCandidate) -> discord.Embed:
    icon, label = _status_icon(candidate)
    verified_at = _fmt_ts(getattr(candidate, "verified_at", None))
    post_verify_activity = _fmt_ts(getattr(candidate, "post_verification_activity_at", None), fallback="none found")
    joined_at = _fmt_ts(getattr(candidate, "joined_at", None))
    source = str(getattr(candidate, "verification_source", "unknown") or "unknown")
    days = "unknown" if candidate.inactivity_days is None else f"{candidate.inactivity_days} day(s)"

    embed = discord.Embed(
        title=f"{icon} Manual Review: {candidate.display_name}",
        description="Use this card to manually inspect the member before taking any server action.",
        color=discord.Color.orange() if label in {"Review candidate", "Needs manual review"} else discord.Color.blurple(),
    )
    embed.add_field(
        name="Member",
        value=_safe_field(
            f"Mention: {getattr(candidate, 'mention', f'<@{candidate.user_id}>')}\n"
            f"User ID: `{candidate.user_id}`\n"
            f"Joined server: {joined_at}\n"
            f"Status: **{label}**\n"
            f"Confidence: **{candidate.confidence}**"
        ),
        inline=False,
    )
    embed.add_field(
        name="Verification Activity",
        value=_safe_field(
            f"Verified/resident: **{'Yes' if getattr(candidate, 'verified_or_resident', False) else 'Unknown'}**\n"
            f"Verified/resident since: {verified_at}\n"
            f"Verification date source: `{source[:120]}`\n"
            f"Post-verification activity: {post_verify_activity}\n"
            f"Quiet after verification: **{days}**"
        ),
        inline=False,
    )
    embed.add_field(name="Why This User Was Found", value=_safe_field(candidate.short_reason(700)), inline=False)
    embed.add_field(
        name="Manual Options",
        value=_safe_field(
            "1. Search/copy the User ID above in Discord if needed.\n"
            "2. Open the member profile and review roles, messages, tickets, and notes.\n"
            "3. If the result is wrong, treat it as a data-confidence issue, not a final decision.\n"
            "4. If the result is correct, use Discord's normal moderation tools manually for now.\n\n"
            "Automatic cleanup should only be added after this review list is accurate."
        ),
        inline=False,
    )
    return embed


class MemberSelect(discord.ui.Select):
    def __init__(self, parent: "MemberActivityReviewView") -> None:
        self.parent_view = parent
        report = parent.report
        page, start, end = _page_bounds(report, parent.page)
        options: list[discord.SelectOption] = []
        for display_idx, candidate in enumerate(report.candidates[start:end], start=start + 1):
            icon, label = _status_icon(candidate)
            days = "unknown" if candidate.inactivity_days is None else f"{candidate.inactivity_days}d"
            options.append(
                discord.SelectOption(
                    label=_trim(f"{display_idx}. {candidate.display_name}", 95),
                    description=_trim(f"{label} • quiet {days} • {candidate.confidence} confidence", 95),
                    value=str(candidate.user_id),
                    emoji=icon,
                )
            )
        if not options:
            options.append(discord.SelectOption(label="No users found", description="Nothing to inspect on this scan.", value="none", emoji="✅"))
        super().__init__(placeholder="Select a user to inspect manually", min_values=1, max_values=1, options=options, row=0, disabled=not report.candidates)

    async def callback(self, interaction: discord.Interaction) -> None:
        if not await _require_review_permission(interaction):
            return
        value = self.values[0] if self.values else "none"
        if value == "none":
            return await reply_once(interaction, {"content": "No users were found in this scan.", "ephemeral": True})
        candidate = self.parent_view.find_candidate(value)
        if candidate is None:
            return await reply_once(interaction, {"content": "That user is no longer available in this scan. Refresh and try again.", "ephemeral": True})
        await interaction.response.send_message(embed=_build_user_detail_embed(candidate), ephemeral=True, allowed_mentions=discord.AllowedMentions.none())


class MemberActivityReviewView(discord.ui.View):
    def __init__(self, report: InactiveScanReport, *, page: int = 0) -> None:
        super().__init__(timeout=900)
        self.report = report
        self.page = max(0, min(int(page), _page_count(report) - 1))
        self._rebuild_items()

    def _rebuild_items(self) -> None:
        self.clear_items()
        self.add_item(MemberSelect(self))
        previous_button = discord.ui.Button(label="Previous", emoji="⬅️", style=discord.ButtonStyle.secondary, disabled=self.page <= 0, row=1)
        next_button = discord.ui.Button(label="Next", emoji="➡️", style=discord.ButtonStyle.secondary, disabled=self.page >= _page_count(self.report) - 1, row=1)
        refresh_button = discord.ui.Button(label="Refresh Scan", emoji="🔄", style=discord.ButtonStyle.primary, row=1)
        safety_button = discord.ui.Button(label="Explain Safety", emoji="🛡️", style=discord.ButtonStyle.secondary, row=2)
        ids_button = discord.ui.Button(label="Show Page IDs", emoji="🆔", style=discord.ButtonStyle.secondary, row=2)

        previous_button.callback = self._previous_page  # type: ignore[assignment]
        next_button.callback = self._next_page  # type: ignore[assignment]
        refresh_button.callback = self._refresh_scan  # type: ignore[assignment]
        safety_button.callback = self._explain_safety  # type: ignore[assignment]
        ids_button.callback = self._show_page_ids  # type: ignore[assignment]

        self.add_item(previous_button)
        self.add_item(next_button)
        self.add_item(refresh_button)
        self.add_item(safety_button)
        self.add_item(ids_button)

    def find_candidate(self, user_id: str) -> Optional[InactiveMemberCandidate]:
        try:
            wanted = int(user_id)
        except Exception:
            return None
        for candidate in self.report.candidates:
            try:
                if int(candidate.user_id) == wanted:
                    return candidate
            except Exception:
                continue
        return None

    async def _replace_page(self, interaction: discord.Interaction, new_page: int) -> None:
        if not await _require_review_permission(interaction):
            return
        self.page = max(0, min(int(new_page), _page_count(self.report) - 1))
        self._rebuild_items()
        await interaction.response.edit_message(embed=_build_report_embed(self.report, page=self.page), view=self)

    async def _previous_page(self, interaction: discord.Interaction) -> None:
        await self._replace_page(interaction, self.page - 1)

    async def _next_page(self, interaction: discord.Interaction) -> None:
        await self._replace_page(interaction, self.page + 1)

    async def _refresh_scan(self, interaction: discord.Interaction) -> None:
        if not await _require_review_permission(interaction):
            return
        if interaction.guild is None:
            return
        await interaction.response.defer(ephemeral=True)
        report = await scan_inactive_members(interaction.guild, self.report.options)
        view = MemberActivityReviewView(report, page=0)
        await interaction.edit_original_response(embed=_build_report_embed(report, page=0), view=view)

    async def _explain_safety(self, interaction: discord.Interaction) -> None:
        text = (
            "🛡️ **Safety rules used by this scan**\n\n"
            "This scan checks **post-verification activity inside this server only**. It does **not** use online/offline/idle status.\n\n"
            "For verified/resident members, Dank Shield tries to find when the role was granted from its own records first. If that is missing, it can use Discord audit log as a fallback.\n\n"
            "Dank Shield protects the server owner, the bot itself, bot accounts by default, staff/admin-style roles, configured protected roles, and new members inside the grace period.\n\n"
            "Normal verified/member/resident roles are **not** treated as cleanup-protected by default. They are the group being reviewed."
        )
        await reply_once(interaction, {"content": text, "ephemeral": True})

    async def _show_page_ids(self, interaction: discord.Interaction) -> None:
        if not await _require_review_permission(interaction):
            return
        page, start, end = _page_bounds(self.report, self.page)
        candidates = self.report.candidates[start:end]
        if not candidates:
            return await reply_once(interaction, {"content": "No users on this page.", "ephemeral": True})
        lines = [f"{idx}. {c.display_name}: `{c.user_id}`" for idx, c in enumerate(candidates, start=start + 1)]
        await reply_once(interaction, {"content": _trim("🆔 **User IDs on this page**\n" + "\n".join(lines), 1900), "ephemeral": True})


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
        embed=_build_report_embed(report, page=0),
        view=MemberActivityReviewView(report, page=0),
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
        embed=_build_report_embed(report, page=0),
        view=MemberActivityReviewView(report, page=0),
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
