from __future__ import annotations

"""Public /dank members server-activity review commands.

This module owns the review UI for verified/resident member activity scans.
The scan itself is intentionally conservative:
- It does not use online/offline/idle presence.
- It only uses activity Dank Shield can observe inside this guild.
- The main dashboard stays short enough for Discord mobile.
- Full explanations live behind select menus and buttons.
- Scan-locked users can be reviewed, unlocked, and relocked from the scan UI.
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
    ScanLockRecord,
    get_last_scan,
    get_scan_lock_records,
    is_scan_user_locked,
    scan_inactive_members,
    set_scan_user_lock,
)


members_group = app_commands.Group(
    name="members",
    description="Member server-activity review tools.",
)

_REGISTERED = False
_FIELD_LIMIT = 1024
_SAFE_FIELD_LIMIT = 950
_PAGE_SIZE = 4
_LOCKED_PAGE_SIZE = 6
_DEFAULT_INACTIVE_DAYS = 90
_DEFAULT_GRACE_DAYS = 14


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


def _safe_int_attr(obj: Any, name: str, default: int = 0) -> int:
    try:
        value = getattr(obj, name, default)
        if value is None:
            return int(default)
        return int(value)
    except Exception:
        return int(default)


def _status_icon(candidate: Any) -> tuple[str, str]:
    status = str(getattr(candidate, "status", "") or "")
    if status == "Review candidate":
        return "🟠", "Review"
    if status == "Needs review":
        return "🟡", "Manual"
    if status == "Insufficient data":
        return "⚪", "Insufficient"
    if status == "Insufficient data":
        return "⚪", "Insufficient"
    if status == "Protected":
        return "🛡️", "Protected"
    if status == "Cannot action":
        return "⛔", "Blocked"
    if status == "Active/recent evidence":
        return "🟢", "Recent"
    return "⚪", status or "Unknown"


def _page_count(report: InactiveScanReport) -> int:
    return max(1, ceil(len(report.candidates) / _PAGE_SIZE))


def _locked_page_count(records: list[ScanLockRecord]) -> int:
    return max(1, ceil(len(records) / _LOCKED_PAGE_SIZE))


def _page_bounds(report: InactiveScanReport, page: int) -> tuple[int, int, int]:
    pages = _page_count(report)
    safe_page = max(0, min(int(page), pages - 1))
    start = safe_page * _PAGE_SIZE
    end = min(start + _PAGE_SIZE, len(report.candidates))
    return safe_page, start, end


def _locked_page_bounds(records: list[ScanLockRecord], page: int) -> tuple[int, int, int]:
    pages = _locked_page_count(records)
    safe_page = max(0, min(int(page), pages - 1))
    start = safe_page * _LOCKED_PAGE_SIZE
    end = min(start + _LOCKED_PAGE_SIZE, len(records))
    return safe_page, start, end


def _candidate_display_name(candidate: InactiveMemberCandidate) -> str:
    for attr in ("display_name", "username", "global_name", "name"):
        try:
            value = getattr(candidate, attr, None)
            if value:
                cleaned = discord.utils.escape_markdown(str(value), as_needed=True).strip()
                if cleaned:
                    return _trim(cleaned, 42)
        except Exception:
            continue
    return "Unknown user"


def _member_display_name(guild: Optional[discord.Guild], user_id: int, fallback: str = "") -> str:
    try:
        member = guild.get_member(int(user_id)) if guild is not None else None
    except Exception:
        member = None

    for value in (
        getattr(member, "display_name", None),
        getattr(member, "global_name", None),
        getattr(member, "name", None),
        fallback,
    ):
        try:
            text = discord.utils.escape_markdown(str(value or ""), as_needed=True).strip()
            if text:
                return _trim(text, 42)
        except Exception:
            continue
    return f"Unknown user {user_id}"


def _short_candidate_line(candidate: InactiveMemberCandidate, idx: int) -> str:
    icon, label = _status_icon(candidate)
    days = "?d" if candidate.inactivity_days is None else f"{candidate.inactivity_days}d"
    confidence = str(getattr(candidate, "confidence", "?") or "?")
    name = _candidate_display_name(candidate)
    if str(getattr(candidate, "confidence", "") or "").lower() == "low":
        return f"`{idx}.` {icon} **{name}** • **{days} quiet** • Low • manual-only"
    if getattr(candidate, "removable", False):
        return f"`{idx}.` {icon} **{name}** • **{days} quiet** • {confidence} • purge-safe"
    return f"`{idx}.` {icon} **{name}** • **{days} quiet** • {confidence} • manual-review"


def _short_locked_line(guild: Optional[discord.Guild], record: ScanLockRecord, idx: int) -> str:
    name = _member_display_name(guild, int(record.user_id))
    locked_at = _fmt_ts(record.locked_at, fallback="unknown time")
    storage = "DB" if record.persisted else "memory"
    reason = _trim(str(record.reason or "Manual review lock"), 100)
    return f"`{idx}.` 🔒 **{name}** • {locked_at} • {storage}\nReason: {reason}"


def _build_status_snapshot(report: InactiveScanReport) -> str:
    return _safe_field(
        f"**Review:** {len(report.candidates)} • **Purge-safe:** {len(getattr(report, 'removable', []) or [])} • **Manual-only:** {len([c for c in report.candidates if not getattr(c, 'removable', False)])} • **Pages:** {_page_count(report)}\n"
        f"**Verified quiet:** {_safe_int_attr(report, 'verified_resident_without_post_activity')}/{_safe_int_attr(report, 'verified_resident_seen')} ({_safe_int_attr(report, 'verified_vanished_percent')}%)\n"
        f"**Server activity:** {_safe_int_attr(report, 'active_activity_percent')}% active/recent • **Locked skipped:** {_safe_int_attr(report, 'locked_users_skipped')}\n"
        f"**Data:** {getattr(report, 'data_confidence_label', 'Unknown')} ({_safe_int_attr(report, 'data_coverage_percent')}%) • **Audit hits:** {_safe_int_attr(report, 'audit_log_times_found')}",
        _SAFE_FIELD_LIMIT,
    )


def _build_page_users_text(report: InactiveScanReport, *, page: int) -> str:
    page, start, end = _page_bounds(report, page)
    if not report.candidates:
        return "✅ No verified/resident users matched this scan."
    lines = [_short_candidate_line(candidate, idx) for idx, candidate in enumerate(report.candidates[start:end], start=start + 1)]
    footer = f"\n\nShowing **{start + 1}-{end}** of **{len(report.candidates)}**. Select a user below for full details."
    return _safe_field("\n".join(lines) + footer, _SAFE_FIELD_LIMIT)


def _build_settings_line(report: InactiveScanReport) -> str:
    options = report.options
    return _safe_field(
        f"Quiet **{options.inactive_days}d** after verify • Grace **{options.grace_days}d** • "
        f"Audit fallback **{'on' if options.use_audit_log_fallback else 'off'}** • "
        f"Skip locked **{'on' if options.skip_locked_users else 'off'}**",
        _SAFE_FIELD_LIMIT,
    )


def _build_data_notes_embed(report: InactiveScanReport) -> discord.Embed:
    attempted = _safe_int_attr(report, "data_sources_attempted", 0)
    readable = _safe_int_attr(report, "data_sources_read", 0)
    if attempted <= 0:
        source_line = f"Optional source coverage: **{_safe_int_attr(report, 'data_coverage_percent')}%**"
    else:
        source_line = f"Optional sources readable: **{readable}/{attempted}** ({_safe_int_attr(report, 'data_coverage_percent')}%)"

    embed = discord.Embed(
        title="📊 Member Scan Data Notes",
        description="Detailed data/confidence notes for the latest member activity scan.",
        color=discord.Color.blurple(),
        timestamp=report.scanned_at,
    )
    embed.add_field(
        name="Data Sources",
        value=_safe_field(
            f"Confidence: **{getattr(report, 'data_confidence_label', 'Unknown')}**\n"
            f"{source_line}\n"
            f"Audit-log verification timestamps found: **{_safe_int_attr(report, 'audit_log_times_found')}**\n"
            f"Scan-lock storage: **{getattr(report, 'scan_lock_persistence', None) or 'unknown'}**"
        ),
        inline=False,
    )
    warnings = list(getattr(report, "data_warnings", []) or [])
    if warnings:
        embed.add_field(
            name="Warnings",
            value=_safe_field("\n".join(f"• {warning}" for warning in warnings[:8])),
            inline=False,
        )
    else:
        embed.add_field(name="Warnings", value="✅ No data warnings for this scan.", inline=False)
    embed.add_field(
        name="What This Means",
        value=_safe_field(
            "Confidence now uses calibrated rules: High requires direct member activity evidence; Medium requires reliable DB/audit/mod-log verification timing plus readable activity coverage; Low means weak proof and is shown as manual-only, not purge-safe. Mod-log embeds are scanned too. Low-confidence users are shown for manual review but are not purge-safe."
        ),
        inline=False,
    )
    return embed


def _build_report_embed(report: InactiveScanReport, *, page: int = 0) -> discord.Embed:
    page, _start, _end = _page_bounds(report, page)
    pages = _page_count(report)
    confidence = str(getattr(report, "data_confidence_label", "") or "")
    color = discord.Color.green() if confidence in {"Good", "Partial"} else discord.Color.orange()
    embed = discord.Embed(
        title="🧹 Verified Member Review",
        description=(
            "Preview only. No action is taken here.\n"
            "Reviews verified/resident members with no recent tracked activity after verification."
        ),
        color=color,
        timestamp=report.scanned_at,
    )
    embed.add_field(name="Status Snapshot", value=_build_status_snapshot(report), inline=False)
    embed.add_field(name=f"Users Found — Page {page + 1}/{pages}", value=_build_page_users_text(report, page=page), inline=False)
    embed.add_field(name="Controls", value="Select a user for details, lock/skip after review, manage locked users, or rescan with 30d/90d/180d.", inline=False)
    embed.add_field(name="Settings", value=_build_settings_line(report), inline=False)
    embed.set_footer(text=f"Guild {report.guild_id} • post-verification activity only • Data Notes has the long details")
    return embed


def _build_user_detail_embed(candidate: InactiveMemberCandidate, *, locked: bool = False) -> discord.Embed:
    icon, label = _status_icon(candidate)
    verified_at = _fmt_ts(getattr(candidate, "verified_at", None))
    post_verify_activity = _fmt_ts(getattr(candidate, "post_verification_activity_at", None), fallback="none found")
    joined_at = _fmt_ts(getattr(candidate, "joined_at", None))
    source = str(getattr(candidate, "verification_source", "unknown") or "unknown")
    days = "unknown" if candidate.inactivity_days is None else f"{candidate.inactivity_days} day(s)"

    embed = discord.Embed(
        title=f"{icon} Manual Review: {candidate.display_name}",
        description=(
            "Inspect this member before taking action.\n"
            f"Scan lock: **{'Locked / skipped in future scans' if locked else 'Not locked'}**"
        ),
        color=discord.Color.orange() if label in {"Review", "Manual"} else discord.Color.blurple(),
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
            "• **Lock / Skip in Scans**: hide this reviewed user from future scans.\n"
            "• **Locked Users** on the scan dashboard: review, unlock, and relock skipped users."
        ),
        inline=False,
    )
    return embed


def _build_locked_users_embed(
    records: list[ScanLockRecord],
    persistence: str,
    *,
    guild: Optional[discord.Guild],
    page: int = 0,
    selected_user_id: Optional[int] = None,
    unlocked_user_id: Optional[int] = None,
) -> discord.Embed:
    page, start, end = _locked_page_bounds(records, page)
    pages = _locked_page_count(records)

    embed = discord.Embed(
        title="🔒 Scan-Locked Members",
        description=(
            "These users are skipped by future `/dank members scan` results until unlocked.\n"
            "Use this screen to review locks, confirm unlocks, and relock a user if you change your mind."
        ),
        color=discord.Color.blurple(),
    )

    if not records:
        embed.add_field(name="Locked Users", value="✅ No users are locked/skipped from scans.", inline=False)
    else:
        visible = records[start:end]
        lines = [_short_locked_line(guild, record, idx) for idx, record in enumerate(visible, start=start + 1)]
        embed.add_field(
            name=f"Locked Users — Page {page + 1}/{pages}",
            value=_safe_field("\n\n".join(lines), _SAFE_FIELD_LIMIT),
            inline=False,
        )

    if selected_user_id is not None:
        name = _member_display_name(guild, selected_user_id)
        embed.add_field(
            name="Selected User",
            value=_safe_field(f"🔒 **{name}** (`{selected_user_id}`)\nPress **Confirm Unlock** to let this user appear in future scans again."),
            inline=False,
        )

    if unlocked_user_id is not None:
        name = _member_display_name(guild, unlocked_user_id)
        embed.add_field(
            name="Last Action",
            value=_safe_field(f"🔓 Unlocked **{name}** (`{unlocked_user_id}`).\nUse **Relock Last User** if this was a mistake."),
            inline=False,
        )

    embed.add_field(name="Storage", value=_safe_field(persistence), inline=False)
    embed.set_footer(text="Back to Scan returns to the current review page.")
    return embed


class UserDetailLockView(discord.ui.View):
    def __init__(self, candidate: InactiveMemberCandidate, *, locked: bool = False) -> None:
        super().__init__(timeout=600)
        self.candidate = candidate
        self.locked = bool(locked)
        self._rebuild()

    def _rebuild(self) -> None:
        self.clear_items()
        lock_button = discord.ui.Button(
            label="Unlock / Show in Scans" if self.locked else "Lock / Skip in Scans",
            emoji="🔓" if self.locked else "🔒",
            style=discord.ButtonStyle.success if self.locked else discord.ButtonStyle.secondary,
        )
        lock_button.callback = self._toggle_lock  # type: ignore[assignment]
        self.add_item(lock_button)

    async def _toggle_lock(self, interaction: discord.Interaction) -> None:
        if not await _require_review_permission(interaction):
            return
        if interaction.guild is None:
            return
        new_locked = not self.locked
        persisted, message = await set_scan_user_lock(
            int(interaction.guild.id),
            int(self.candidate.user_id),
            locked=new_locked,
            actor_id=int(interaction.user.id),
            reason="Locked from member activity scan after manual review" if new_locked else "Unlocked from member activity scan",
        )
        self.locked = new_locked
        self._rebuild()
        status = "🔒 Locked. This user will be skipped by future scans." if new_locked else "🔓 Unlocked. This user can appear in future scans again."
        if not persisted:
            status += f"\n⚠️ {message}"
        await interaction.response.edit_message(embed=_build_user_detail_embed(self.candidate, locked=self.locked), view=self)
        try:
            await interaction.followup.send(status, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())
        except Exception:
            pass


class LockedUserSelect(discord.ui.Select):
    def __init__(self, parent: "LockedUsersReviewView") -> None:
        self.parent_view = parent
        page, start, end = _locked_page_bounds(parent.records, parent.page)
        records = parent.records[start:end]

        options: list[discord.SelectOption] = []
        for idx, record in enumerate(records, start=start + 1):
            name = _member_display_name(parent.guild, int(record.user_id))
            locked_at = "unknown"
            try:
                if record.locked_at is not None:
                    locked_at = f"{int((parent.now_ts - record.locked_at.timestamp()) // 3600)}h ago"
            except Exception:
                locked_at = "locked"
            options.append(
                discord.SelectOption(
                    label=_trim(f"{idx}. {name}", 95),
                    description=_trim(f"{locked_at} • {record.reason}", 95),
                    value=str(record.user_id),
                    emoji="🔒",
                )
            )

        if not options:
            options.append(discord.SelectOption(label="No locked users", description="Nothing to unlock.", value="none", emoji="✅"))

        super().__init__(
            placeholder="Select a locked user to review",
            min_values=1,
            max_values=1,
            options=options,
            row=0,
            disabled=not records,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        if not await _require_review_permission(interaction):
            return
        value = self.values[0] if self.values else "none"
        if value == "none":
            return await reply_once(interaction, {"content": "No locked users to review.", "ephemeral": True})
        self.parent_view.selected_user_id = int(value)
        self.parent_view.last_unlocked_user_id = None
        self.parent_view.confirm_unlock_ready = True
        self.parent_view._rebuild_items()
        await interaction.response.edit_message(
            embed=_build_locked_users_embed(
                self.parent_view.records,
                self.parent_view.persistence,
                guild=interaction.guild,
                page=self.parent_view.page,
                selected_user_id=self.parent_view.selected_user_id,
            ),
            view=self.parent_view,
            allowed_mentions=discord.AllowedMentions.none(),
        )


class LockedUsersReviewView(discord.ui.View):
    def __init__(
        self,
        records: list[ScanLockRecord],
        persistence: str,
        *,
        guild: Optional[discord.Guild],
        scan_report: Optional[InactiveScanReport] = None,
        scan_page: int = 0,
        page: int = 0,
    ) -> None:
        super().__init__(timeout=900)
        self.records = list(records)
        self.persistence = str(persistence or "unknown")
        self.guild = guild
        self.scan_report = scan_report
        self.scan_page = int(scan_page)
        self.page = max(0, min(int(page), _locked_page_count(self.records) - 1))
        self.selected_user_id: Optional[int] = None
        self.last_unlocked_user_id: Optional[int] = None
        self.confirm_unlock_ready = False
        try:
            import time

            self.now_ts = float(time.time())
        except Exception:
            self.now_ts = 0.0
        self._rebuild_items()

    def _rebuild_items(self) -> None:
        self.clear_items()
        self.add_item(LockedUserSelect(self))

        previous_button = discord.ui.Button(
            label="Previous",
            emoji="⬅️",
            style=discord.ButtonStyle.secondary,
            disabled=self.page <= 0,
            row=1,
        )
        next_button = discord.ui.Button(
            label="Next",
            emoji="➡️",
            style=discord.ButtonStyle.secondary,
            disabled=self.page >= _locked_page_count(self.records) - 1,
            row=1,
        )
        refresh_button = discord.ui.Button(label="Refresh", emoji="🔄", style=discord.ButtonStyle.primary, row=1)

        confirm_button = discord.ui.Button(
            label="Confirm Unlock",
            emoji="🔓",
            style=discord.ButtonStyle.danger,
            disabled=not (self.confirm_unlock_ready and self.selected_user_id is not None),
            row=2,
        )
        relock_button = discord.ui.Button(
            label="Relock Last User",
            emoji="🔒",
            style=discord.ButtonStyle.secondary,
            disabled=self.last_unlocked_user_id is None,
            row=2,
        )
        back_button = discord.ui.Button(label="Back to Scan", emoji="↩️", style=discord.ButtonStyle.secondary, row=3)

        previous_button.callback = self._previous_page  # type: ignore[assignment]
        next_button.callback = self._next_page  # type: ignore[assignment]
        refresh_button.callback = self._refresh_locked  # type: ignore[assignment]
        confirm_button.callback = self._confirm_unlock  # type: ignore[assignment]
        relock_button.callback = self._relock_last_user  # type: ignore[assignment]
        back_button.callback = self._back_to_scan  # type: ignore[assignment]

        self.add_item(previous_button)
        self.add_item(next_button)
        self.add_item(refresh_button)
        self.add_item(confirm_button)
        self.add_item(relock_button)
        if self.scan_report is not None:
            self.add_item(back_button)

    async def _reload_records(self, interaction: discord.Interaction) -> tuple[list[ScanLockRecord], str]:
        if interaction.guild is None:
            return [], "unknown"
        records, persistence = await get_scan_lock_records(int(interaction.guild.id))
        self.records = list(records)
        self.persistence = persistence
        self.guild = interaction.guild
        self.page = max(0, min(self.page, _locked_page_count(self.records) - 1))
        return self.records, self.persistence

    async def _edit_self(self, interaction: discord.Interaction) -> None:
        self._rebuild_items()
        await interaction.response.edit_message(
            embed=_build_locked_users_embed(
                self.records,
                self.persistence,
                guild=interaction.guild,
                page=self.page,
                selected_user_id=self.selected_user_id,
                unlocked_user_id=self.last_unlocked_user_id,
            ),
            view=self,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    async def _replace_page(self, interaction: discord.Interaction, new_page: int) -> None:
        if not await _require_review_permission(interaction):
            return
        self.page = max(0, min(int(new_page), _locked_page_count(self.records) - 1))
        self.selected_user_id = None
        self.confirm_unlock_ready = False
        await self._edit_self(interaction)

    async def _previous_page(self, interaction: discord.Interaction) -> None:
        await self._replace_page(interaction, self.page - 1)

    async def _next_page(self, interaction: discord.Interaction) -> None:
        await self._replace_page(interaction, self.page + 1)

    async def _refresh_locked(self, interaction: discord.Interaction) -> None:
        if not await _require_review_permission(interaction):
            return
        await self._reload_records(interaction)
        self.selected_user_id = None
        self.confirm_unlock_ready = False
        await self._edit_self(interaction)

    async def _confirm_unlock(self, interaction: discord.Interaction) -> None:
        if not await _require_review_permission(interaction):
            return
        if interaction.guild is None:
            return
        if self.selected_user_id is None:
            return await reply_once(interaction, {"content": "Select a locked user first.", "ephemeral": True})

        user_id = int(self.selected_user_id)
        persisted, message = await set_scan_user_lock(
            int(interaction.guild.id),
            user_id,
            locked=False,
            actor_id=int(interaction.user.id),
            reason="Unlocked from locked-users review screen",
        )
        await self._reload_records(interaction)
        self.selected_user_id = None
        self.confirm_unlock_ready = False
        self.last_unlocked_user_id = user_id
        self._rebuild_items()

        note = f"🔓 Unlocked {_member_display_name(interaction.guild, user_id)}. They can appear in future scans again."
        if not persisted:
            note += f"\n⚠️ {message}"

        await interaction.response.edit_message(
            embed=_build_locked_users_embed(
                self.records,
                self.persistence,
                guild=interaction.guild,
                page=self.page,
                unlocked_user_id=user_id,
            ),
            view=self,
            allowed_mentions=discord.AllowedMentions.none(),
        )
        try:
            await interaction.followup.send(note, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())
        except Exception:
            pass

    async def _relock_last_user(self, interaction: discord.Interaction) -> None:
        if not await _require_review_permission(interaction):
            return
        if interaction.guild is None:
            return
        if self.last_unlocked_user_id is None:
            return await reply_once(interaction, {"content": "No recently unlocked user to relock.", "ephemeral": True})

        user_id = int(self.last_unlocked_user_id)
        persisted, message = await set_scan_user_lock(
            int(interaction.guild.id),
            user_id,
            locked=True,
            actor_id=int(interaction.user.id),
            reason="Relocked from locked-users review screen",
        )
        await self._reload_records(interaction)
        self.selected_user_id = user_id
        self.confirm_unlock_ready = False
        self.last_unlocked_user_id = None
        self._rebuild_items()

        note = f"🔒 Relocked {_member_display_name(interaction.guild, user_id)}. They will be skipped by future scans."
        if not persisted:
            note += f"\n⚠️ {message}"

        await interaction.response.edit_message(
            embed=_build_locked_users_embed(
                self.records,
                self.persistence,
                guild=interaction.guild,
                page=self.page,
                selected_user_id=user_id,
            ),
            view=self,
            allowed_mentions=discord.AllowedMentions.none(),
        )
        try:
            await interaction.followup.send(note, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())
        except Exception:
            pass

    async def _back_to_scan(self, interaction: discord.Interaction) -> None:
        if not await _require_review_permission(interaction):
            return
        if self.scan_report is None:
            return await reply_once(interaction, {"content": "No scan dashboard is attached to this locked-users view.", "ephemeral": True})
        view = MemberActivityReviewView(self.scan_report, page=self.scan_page)
        await interaction.response.edit_message(
            embed=_build_report_embed(self.scan_report, page=self.scan_page),
            view=view,
            allowed_mentions=discord.AllowedMentions.none(),
        )


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
        locked = False
        if interaction.guild is not None:
            locked = await is_scan_user_locked(int(interaction.guild.id), int(candidate.user_id))
        await interaction.response.send_message(
            embed=_build_user_detail_embed(candidate, locked=locked),
            view=UserDetailLockView(candidate, locked=locked),
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )


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
        refresh_button = discord.ui.Button(label="Refresh", emoji="🔄", style=discord.ButtonStyle.primary, row=1)
        data_button = discord.ui.Button(label="Data Notes", emoji="📊", style=discord.ButtonStyle.secondary, row=2)
        safety_button = discord.ui.Button(label="Safety", emoji="🛡️", style=discord.ButtonStyle.secondary, row=2)
        ids_button = discord.ui.Button(label="Page IDs", emoji="🆔", style=discord.ButtonStyle.secondary, row=2)
        locked_button = discord.ui.Button(label="Locked Users", emoji="🔒", style=discord.ButtonStyle.secondary, row=2)
        preset_30_button = discord.ui.Button(label="30d", emoji="⚡", style=discord.ButtonStyle.secondary, row=3)
        preset_90_button = discord.ui.Button(label="90d", emoji="🎯", style=discord.ButtonStyle.secondary, row=3)
        preset_180_button = discord.ui.Button(label="180d", emoji="🧊", style=discord.ButtonStyle.secondary, row=3)

        previous_button.callback = self._previous_page  # type: ignore[assignment]
        next_button.callback = self._next_page  # type: ignore[assignment]
        refresh_button.callback = self._refresh_scan  # type: ignore[assignment]
        data_button.callback = self._show_data_notes  # type: ignore[assignment]
        safety_button.callback = self._explain_safety  # type: ignore[assignment]
        ids_button.callback = self._show_page_ids  # type: ignore[assignment]
        locked_button.callback = self._show_locked_users  # type: ignore[assignment]
        preset_30_button.callback = self._preset_30_days  # type: ignore[assignment]
        preset_90_button.callback = self._preset_90_days  # type: ignore[assignment]
        preset_180_button.callback = self._preset_180_days  # type: ignore[assignment]

        self.add_item(previous_button)
        self.add_item(next_button)
        self.add_item(refresh_button)
        self.add_item(data_button)
        self.add_item(safety_button)
        self.add_item(ids_button)
        self.add_item(locked_button)
        self.add_item(preset_30_button)
        self.add_item(preset_90_button)
        self.add_item(preset_180_button)

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

    async def _rescan(self, interaction: discord.Interaction, *, inactive_days: Optional[int] = None) -> None:
        if not await _require_review_permission(interaction):
            return
        if interaction.guild is None:
            return
        await interaction.response.defer(ephemeral=True)
        base = self.report.options
        options = InactiveScanOptions(
            inactive_days=max(7, min(int(inactive_days if inactive_days is not None else base.inactive_days), 730)),
            grace_days=max(1, min(int(base.grace_days), 90)),
            protect_bots=bool(base.protect_bots),
            protect_staff=bool(base.protect_staff),
            include_low_confidence=bool(base.include_low_confidence),
            include_medium_confidence=bool(base.include_medium_confidence),
            include_high_confidence=bool(base.include_high_confidence),
            max_candidates=int(base.max_candidates),
            verified_resident_focus=bool(base.verified_resident_focus),
            use_audit_log_fallback=bool(base.use_audit_log_fallback),
            skip_locked_users=bool(base.skip_locked_users),
        )
        report = await scan_inactive_members(interaction.guild, options)
        view = MemberActivityReviewView(report, page=0)
        await interaction.edit_original_response(embed=_build_report_embed(report, page=0), view=view)

    async def _refresh_scan(self, interaction: discord.Interaction) -> None:
        await self._rescan(interaction)

    async def _preset_30_days(self, interaction: discord.Interaction) -> None:
        await self._rescan(interaction, inactive_days=30)

    async def _preset_90_days(self, interaction: discord.Interaction) -> None:
        await self._rescan(interaction, inactive_days=90)

    async def _preset_180_days(self, interaction: discord.Interaction) -> None:
        await self._rescan(interaction, inactive_days=180)

    async def _show_data_notes(self, interaction: discord.Interaction) -> None:
        if not await _require_review_permission(interaction):
            return
        await interaction.response.send_message(embed=_build_data_notes_embed(self.report), ephemeral=True)

    async def _show_locked_users(self, interaction: discord.Interaction) -> None:
        if not await _require_review_permission(interaction):
            return
        if interaction.guild is None:
            return
        records, persistence = await get_scan_lock_records(int(interaction.guild.id))
        view = LockedUsersReviewView(
            records,
            persistence,
            guild=interaction.guild,
            scan_report=self.report,
            scan_page=self.page,
            page=0,
        )
        await interaction.response.edit_message(
            embed=_build_locked_users_embed(records, persistence, guild=interaction.guild, page=0),
            view=view,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    async def _explain_safety(self, interaction: discord.Interaction) -> None:
        text = (
            "🛡️ **Safety rules used by this scan**\n\n"
            "This scan checks **post-verification activity inside this server only**. It does **not** use online/offline/idle status.\n\n"
            "For verified/resident members, Dank Shield tries to find when the role was granted from its own records first. If that is missing, it can use Discord audit log as a fallback.\n\n"
            "Normal verified/member/resident roles are **not** treated as protected by default. They are the group being reviewed.\n\n"
            "Use **Lock / Skip in Scans** after manually reviewing a user who should not appear again. Use **Locked Users** on this dashboard to review, unlock, or relock skipped users."
        )
        await reply_once(interaction, {"content": text, "ephemeral": True})

    async def _show_page_ids(self, interaction: discord.Interaction) -> None:
        if not await _require_review_permission(interaction):
            return
        _page, start, end = _page_bounds(self.report, self.page)
        candidates = self.report.candidates[start:end]
        if not candidates:
            return await reply_once(interaction, {"content": "No users on this page.", "ephemeral": True})
        lines = [f"{idx}. {c.display_name}: `{c.user_id}`" for idx, c in enumerate(candidates, start=start + 1)]
        await reply_once(interaction, {"content": _trim("🆔 **User IDs on this page**\n" + "\n".join(lines), 1900), "ephemeral": True})


async def _run_activity_scan(
    interaction: discord.Interaction,
    *,
    inactive_days: int = _DEFAULT_INACTIVE_DAYS,
    grace_days: int = _DEFAULT_GRACE_DAYS,
    include_low_confidence: bool = True,
    use_audit_log_fallback: bool = True,
    skip_locked_users: bool = True,
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
        skip_locked_users=bool(skip_locked_users),
    )
    report = await scan_inactive_members(interaction.guild, options)
    await interaction.followup.send(
        embed=_build_report_embed(report, page=0),
        view=MemberActivityReviewView(report, page=0),
        ephemeral=True,
        allowed_mentions=discord.AllowedMentions.none(),
    )


@members_group.command(name="inactive", description="Open the verified member activity review console.")
async def members_inactive(interaction: discord.Interaction) -> None:
    await _run_activity_scan(interaction)


@members_group.command(name="scan", description="Run the default verified member activity review.")
async def members_scan(interaction: discord.Interaction) -> None:
    await _run_activity_scan(interaction)


@members_group.command(name="advanced-scan", description="Run member review with custom thresholds.")
@app_commands.describe(
    inactive_days="Verified/resident members quiet this many days after verification are shown.",
    grace_days="Protect members newer than this many days.",
    include_low_confidence="Show weak/low-confidence manual-review users too. Default: true; low confidence is not purge-safe.",
    use_audit_log_fallback="Use Discord audit log to estimate when Verified/Resident was added. Default: true.",
    skip_locked_users="Hide users staff locked/skipped from scans. Default: true.",
)
async def members_advanced_scan(
    interaction: discord.Interaction,
    inactive_days: int = _DEFAULT_INACTIVE_DAYS,
    grace_days: int = _DEFAULT_GRACE_DAYS,
    include_low_confidence: bool = True,
    use_audit_log_fallback: bool = True,
    skip_locked_users: bool = True,
) -> None:
    await _run_activity_scan(
        interaction,
        inactive_days=inactive_days,
        grace_days=grace_days,
        include_low_confidence=include_low_confidence,
        use_audit_log_fallback=use_audit_log_fallback,
        skip_locked_users=skip_locked_users,
    )


@members_group.command(name="locked", description="View, unlock, and relock members skipped from activity scans.")
async def members_locked(interaction: discord.Interaction) -> None:
    if not await _require_review_permission(interaction):
        return
    if interaction.guild is None:
        return
    records, persistence = await get_scan_lock_records(int(interaction.guild.id))
    await interaction.response.send_message(
        embed=_build_locked_users_embed(records, persistence, guild=interaction.guild, page=0),
        view=LockedUsersReviewView(records, persistence, guild=interaction.guild),
        ephemeral=True,
        allowed_mentions=discord.AllowedMentions.none(),
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


__all__ = [
    "members_group",
    "register_public_members_group_commands",
]
