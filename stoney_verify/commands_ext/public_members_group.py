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

from datetime import datetime, timedelta, timezone
from math import ceil
from typing import Any, Mapping, Optional
from zoneinfo import ZoneInfo
import asyncio
import uuid

import discord
from discord import app_commands

from .common import reply_once
from .public_setup_group import dank_group
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

from stoney_verify.globals import get_supabase
from stoney_verify.members_new.activity_tracker import (
    get_activity_coverage_status,
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

_NOTICE_TABLE = "member_activity_notices"
_NOTICE_MEMORY: dict[str, dict[str, Any]] = {}
_NOTICE_WORKER_STARTED = False
_NOTICE_SEND_DELAY_SECONDS = 2.5
_NOTICE_WORKER_INTERVAL_SECONDS = 30
_NOTICE_DEFAULT_TZ = "America/New_York"
_NOTICE_STATUS_SCHEDULED = "scheduled"
_NOTICE_STATUS_DELIVERED = "delivered"
_NOTICE_STATUS_DM_BLOCKED = "dm_blocked"
_NOTICE_STATUS_FAILED = "failed"
_NOTICE_STATUS_RESPONDED_STAYING = "responded_staying"
_NOTICE_STATUS_OK_LEAVING = "ok_leaving"
_NOTICE_STATUS_DEADLINE_PASSED = "deadline_passed"
_NOTICE_PENDING_STATUSES = {
    _NOTICE_STATUS_SCHEDULED,
    _NOTICE_STATUS_DELIVERED,
}
_NOTICE_FINAL_STATUSES = {
    _NOTICE_STATUS_DM_BLOCKED,
    _NOTICE_STATUS_FAILED,
    _NOTICE_STATUS_RESPONDED_STAYING,
    _NOTICE_STATUS_OK_LEAVING,
    _NOTICE_STATUS_DEADLINE_PASSED,
}



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
    actionable = bool(getattr(report, "actionable", False))
    action_label = "✅ Actionable" if actionable else "🟡 Review-only"

    return _safe_field(
        f"**Mode:** {action_label}\n"
        f"**Review:** {len(report.candidates)} • **Purge-safe:** {len(getattr(report, 'removable', []) or [])} • **Manual-only:** {len([c for c in report.candidates if not getattr(c, 'removable', False)])} • **Pages:** {_page_count(report)}\n"
        f"**Verified quiet:** {_safe_int_attr(report, 'verified_resident_without_post_activity')}/{_safe_int_attr(report, 'verified_resident_seen')} ({_safe_int_attr(report, 'verified_vanished_percent')}%)\n"
        f"**Server activity:** {_safe_int_attr(report, 'active_activity_percent')}% active/recent • **Locked skipped:** {_safe_int_attr(report, 'locked_users_skipped')}\n"
        f"**Data:** {getattr(report, 'data_confidence_label', 'Unknown')} ({_safe_int_attr(report, 'data_coverage_percent')}%) • **Audit hits:** {_safe_int_attr(report, 'audit_log_times_found')}\n"
        f"**Tracker coverage:** {_safe_int_attr(report, 'coverage_observed_days')}/{_safe_int_attr(report, 'coverage_required_days')} day(s)",
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
            "Only activity directly performed by the member can reset inactivity. "
            "Staff actions, role changes, generic row updates, and log mentions are context only. "
            "If continuous authoritative coverage is incomplete, the entire scan is review-only "
            "and nobody can be removed."
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



# ============================================================
# Member Activity Notices
# ============================================================

def _utcnow() -> datetime:
    try:
        return now_utc()  # type: ignore[name-defined]
    except Exception:
        return datetime.now(timezone.utc)


def _coerce_utc(value: Any) -> Optional[datetime]:
    try:
        if value is None:
            return None
        if isinstance(value, datetime):
            dt = value
        else:
            raw = str(value).strip().replace("Z", "+00:00")
            if not raw:
                return None
            dt = datetime.fromisoformat(raw)
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def _parse_notice_datetime(raw_value: str, *, timezone_name: str, now: Optional[datetime] = None) -> datetime:
    """Parse staff-entered notice dates.

    Supported:
    - now
    - +2h / +3d / +45m
    - YYYY-MM-DD HH:MM
    - YYYY-MM-DD 7:30 PM
    - MM/DD/YYYY HH:MM
    - MM/DD/YYYY 7:30 PM

    Naive date-times are interpreted in the supplied IANA timezone, then stored
    as UTC. Discord timestamps in the DM render in each user's local timezone.
    """
    current = now or _utcnow()
    text = str(raw_value or "").strip()
    if not text or text.lower() == "now":
        return current

    lowered = text.lower().replace(" ", "")
    if lowered.startswith("+"):
        number = ""
        unit = ""
        for ch in lowered[1:]:
            if ch.isdigit():
                number += ch
            else:
                unit += ch
        amount = int(number or "0")
        if amount <= 0:
            raise ValueError("Relative time must be greater than zero.")
        if unit in {"m", "min", "mins", "minute", "minutes"}:
            return current + timedelta(minutes=amount)
        if unit in {"h", "hr", "hrs", "hour", "hours"}:
            return current + timedelta(hours=amount)
        if unit in {"d", "day", "days"}:
            return current + timedelta(days=amount)
        raise ValueError("Use +30m, +2h, or +3d for relative times.")

    try:
        tz = ZoneInfo(str(timezone_name or _NOTICE_DEFAULT_TZ).strip() or _NOTICE_DEFAULT_TZ)
    except Exception:
        tz = ZoneInfo("UTC")

    formats = (
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d %I:%M %p",
        "%m/%d/%Y %H:%M",
        "%m/%d/%Y %I:%M %p",
    )
    last_error: Optional[Exception] = None
    for fmt in formats:
        try:
            local_dt = datetime.strptime(text, fmt).replace(tzinfo=tz)
            return local_dt.astimezone(timezone.utc)
        except Exception as e:
            last_error = e
    raise ValueError("Use `now`, `+3d`, or a date like `2026-05-11 8:00 PM`.") from last_error


def _discord_timestamp(dt: Optional[datetime], style: str = "F") -> str:
    parsed = _coerce_utc(dt)
    if parsed is None:
        return "unknown"
    return f"<t:{int(parsed.timestamp())}:{style}>"


def _notice_id() -> str:
    return uuid.uuid4().hex


def _candidate_notice_scope(report: InactiveScanReport, scope: str, *, page: int = 0, selected: Optional[InactiveMemberCandidate] = None) -> list[InactiveMemberCandidate]:
    scope = str(scope or "review").lower()
    if selected is not None:
        return [selected]
    if scope == "purge_safe":
        return [c for c in report.candidates if getattr(c, "removable", False)]
    if scope == "manual_only":
        return [c for c in report.candidates if not getattr(c, "removable", False)]
    if scope == "current_page":
        _page, start, end = _page_bounds(report, page)
        return list(report.candidates[start:end])
    return list(report.candidates)


def _notice_scope_label(scope: str) -> str:
    return {
        "selected": "selected user",
        "current_page": "current page",
        "review": "full review list",
        "purge_safe": "purge-safe list",
        "manual_only": "manual-only list",
    }.get(str(scope or "review"), "review list")


def _build_notice_row(
    *,
    guild: discord.Guild,
    candidate: InactiveMemberCandidate,
    scope: str,
    send_at: datetime,
    deadline_at: datetime,
    created_by: int,
    note: str = "",
) -> dict[str, Any]:
    now = _utcnow()
    return {
        "notice_id": _notice_id(),
        "guild_id": str(int(guild.id)),
        "guild_name": str(getattr(guild, "name", "this server") or "this server")[:160],
        "user_id": str(int(candidate.user_id)),
        "user_display_name": str(candidate.display_name or candidate.user_id)[:160],
        "scope": str(scope or "review")[:40],
        "status": _NOTICE_STATUS_SCHEDULED,
        "send_at": send_at.astimezone(timezone.utc).isoformat(),
        "deadline_at": deadline_at.astimezone(timezone.utc).isoformat(),
        "created_by": str(int(created_by)),
        "created_at": now.isoformat(),
        "updated_at": now.isoformat(),
        "note": str(note or "")[:800],
        "confidence": str(getattr(candidate, "confidence", "") or "")[:40],
        "inactivity_days": int(candidate.inactivity_days) if candidate.inactivity_days is not None else None,
        "removable": bool(getattr(candidate, "removable", False)),
        "error": None,
        "dm_message_id": None,
    }


def _notice_table() -> Any:
    try:
        sb = get_supabase()
        if sb is None:
            return None
        return sb.table(_NOTICE_TABLE)
    except Exception:
        return None


def _memory_upsert_notice(row: dict[str, Any]) -> None:
    _NOTICE_MEMORY[str(row.get("notice_id"))] = dict(row)


def _memory_update_notice(notice_id: str, **fields: Any) -> None:
    row = _NOTICE_MEMORY.get(str(notice_id))
    if row is None:
        return
    row.update(fields)
    row["updated_at"] = _utcnow().isoformat()


def _upsert_notice_row(row: dict[str, Any]) -> tuple[bool, str]:
    table = _notice_table()
    _memory_upsert_notice(row)
    if table is None:
        return False, "Notice saved in memory only because Supabase is unavailable."
    try:
        table.upsert(row, on_conflict="notice_id").execute()
        return True, "Notice saved."
    except Exception:
        return False, f"Optional `{_NOTICE_TABLE}` table was not writable. Notice saved in memory only until restart."


def _update_notice_row(notice_id: str, **fields: Any) -> tuple[bool, str]:
    notice_id = str(notice_id)
    fields = dict(fields)
    fields["updated_at"] = _utcnow().isoformat()
    _memory_update_notice(notice_id, **fields)
    table = _notice_table()
    if table is None:
        return False, "Notice updated in memory only."
    try:
        table.update(fields).eq("notice_id", notice_id).execute()
        return True, "Notice updated."
    except Exception:
        return False, f"Optional `{_NOTICE_TABLE}` table was not writable. Notice update is memory-only until restart."


def _select_notice_rows(*, guild_id: Optional[int] = None, user_id: Optional[int] = None, limit: int = 500) -> tuple[list[dict[str, Any]], str]:
    rows: list[dict[str, Any]] = []
    table = _notice_table()
    warning = ""
    if table is not None:
        try:
            query = table.select("*")
            if guild_id is not None:
                query = query.eq("guild_id", str(int(guild_id)))
            if user_id is not None:
                query = query.eq("user_id", str(int(user_id)))
            resp = query.limit(int(limit)).execute()
            rows = [dict(r) for r in (getattr(resp, "data", None) or []) if isinstance(r, Mapping)]
        except Exception:
            warning = f"Optional `{_NOTICE_TABLE}` table was not readable. Showing memory-only notice results."

    # Merge memory fallback / newest local rows.
    seen = {str(r.get("notice_id")) for r in rows}
    for row in _NOTICE_MEMORY.values():
        if guild_id is not None and str(row.get("guild_id")) != str(int(guild_id)):
            continue
        if user_id is not None and str(row.get("user_id")) != str(int(user_id)):
            continue
        if str(row.get("notice_id")) in seen:
            continue
        rows.append(dict(row))

    rows.sort(key=lambda r: str(r.get("created_at") or ""), reverse=True)
    return rows[: int(limit)], warning


def _due_notice_rows(now: Optional[datetime] = None, *, limit: int = 25) -> tuple[list[dict[str, Any]], str]:
    current = now or _utcnow()
    rows, warning = _select_notice_rows(limit=1000)
    due: list[dict[str, Any]] = []
    for row in rows:
        status = str(row.get("status") or "")
        send_at = _coerce_utc(row.get("send_at"))
        if status == _NOTICE_STATUS_SCHEDULED and send_at is not None and send_at <= current:
            due.append(row)
    due.sort(key=lambda r: str(r.get("send_at") or ""))
    return due[: int(limit)], warning


def _latest_pending_notice_for_user(user_id: int) -> Optional[dict[str, Any]]:
    rows, _warning = _select_notice_rows(user_id=int(user_id), limit=100)
    current = _utcnow()
    pending: list[dict[str, Any]] = []
    for row in rows:
        status = str(row.get("status") or "")
        if status not in _NOTICE_PENDING_STATUSES:
            continue
        deadline = _coerce_utc(row.get("deadline_at"))
        if deadline is not None and deadline < current:
            continue
        pending.append(row)
    pending.sort(key=lambda r: str(r.get("created_at") or ""), reverse=True)
    return pending[0] if pending else None


def _notice_dm_embed(row: dict[str, Any]) -> discord.Embed:
    guild_name = str(row.get("guild_name") or "the server")
    user_name = str(row.get("user_display_name") or "there")
    deadline = _coerce_utc(row.get("deadline_at"))
    note = str(row.get("note") or "").strip()

    embed = discord.Embed(
        title=f"Quick check-in from {guild_name}",
        description=(
            f"Hey **{discord.utils.escape_markdown(user_name, as_needed=True)}** 👋\n\n"
            f"This is a quick check-in from the **{discord.utils.escape_markdown(guild_name, as_needed=True)}** staff team.\n\n"
            "You’re still verified here, but we haven’t seen much recent activity from your account in the server. "
            "No action has been taken yet — we’re just checking before cleaning up inactive members.\n\n"
            f"If you still want to stay in **{discord.utils.escape_markdown(guild_name, as_needed=True)}**, tap **I’m still active** before:\n\n"
            f"**{_discord_timestamp(deadline, 'F')}**\n"
            f"{_discord_timestamp(deadline, 'R')}\n\n"
            "✅ Tap **I’m still active** and you’ll be marked as staying."
        ),
        color=discord.Color.blurple(),
        timestamp=_utcnow(),
    )
    if note:
        embed.add_field(name="Note from staff", value=_safe_field(note, 600), inline=False)
    embed.set_footer(text=f"Sent by the {guild_name} team using Dank Shield.")
    return embed


class MemberActivityNoticeDMView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(
        label="I’m still active",
        emoji="✅",
        style=discord.ButtonStyle.success,
        custom_id="dank_member_notice_still_active",
    )
    async def still_active(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        try:
            notice = await asyncio.wait_for(
                asyncio.to_thread(_latest_pending_notice_for_user, int(interaction.user.id)),
                timeout=8.0,
            )
        except Exception as exc:
            print(f"⚠️ member_activity_notices pending notice lookup skipped: {exc!r}")
            notice = None
        if notice is None:
            return await interaction.response.send_message(
                "You’re all set — I don’t see an active cleanup notice for you anymore.",
                ephemeral=True,
            )

        guild_id = _safe_int_attr(type("Obj", (), {"guild_id": notice.get("guild_id")})(), "guild_id", 0)
        # Safer than _safe_int_attr on dict:
        try:
            guild_id = int(str(notice.get("guild_id")))
        except Exception:
            guild_id = 0

        await asyncio.to_thread(
            _update_notice_row,
            str(notice.get("notice_id")),
            status=_NOTICE_STATUS_RESPONDED_STAYING,
            responded_at=_utcnow().isoformat(),
            response="still_active",
        )
        if guild_id > 0:
            try:
                await set_scan_user_lock(
                    guild_id,
                    int(interaction.user.id),
                    locked=True,
                    actor_id=None,
                    reason="Member replied to activity notice: still active",
                )
            except Exception:
                pass

        guild_name = str(notice.get("guild_name") or "the server")
        await interaction.response.send_message(
            f"✅ You’re all set. The **{discord.utils.escape_markdown(guild_name, as_needed=True)}** team will see that you’re still active.",
            ephemeral=True,
        )

    @discord.ui.button(
        label="What is this?",
        emoji="❓",
        style=discord.ButtonStyle.secondary,
        custom_id="dank_member_notice_what_is_this",
    )
    async def what_is_this(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        try:
            notice = await asyncio.wait_for(
                asyncio.to_thread(_latest_pending_notice_for_user, int(interaction.user.id)),
                timeout=8.0,
            )
        except Exception as exc:
            print(f"⚠️ member_activity_notices pending notice lookup skipped: {exc!r}")
            notice = None
        guild_name = str((notice or {}).get("guild_name") or "that server")
        await interaction.response.send_message(
            "This message was sent because the server staff is checking inactive verified members before cleanup.\n\n"
            f"Dank Shield is the moderation tool helping the **{discord.utils.escape_markdown(guild_name, as_needed=True)}** staff team track responses. "
            "Tapping **I’m still active** tells staff not to include you in inactive cleanup.",
            ephemeral=True,
        )

    @discord.ui.button(
        label="I’m okay leaving",
        emoji="🚪",
        style=discord.ButtonStyle.secondary,
        custom_id="dank_member_notice_ok_leaving",
    )
    async def okay_leaving(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        try:
            notice = await asyncio.wait_for(
                asyncio.to_thread(_latest_pending_notice_for_user, int(interaction.user.id)),
                timeout=8.0,
            )
        except Exception as exc:
            print(f"⚠️ member_activity_notices pending notice lookup skipped: {exc!r}")
            notice = None
        if notice is not None:
            await asyncio.to_thread(
                _update_notice_row,
                str(notice.get("notice_id")),
                status=_NOTICE_STATUS_OK_LEAVING,
                responded_at=_utcnow().isoformat(),
                response="ok_leaving",
            )
        await interaction.response.send_message(
            "Thanks for letting the staff team know. No action was taken from this button alone.",
            ephemeral=True,
        )


async def _send_notice_row(bot: Any, row: dict[str, Any]) -> None:
    notice_id = str(row.get("notice_id") or "")
    if not notice_id:
        return

    try:
        guild_id = int(str(row.get("guild_id")))
        user_id = int(str(row.get("user_id")))
    except Exception:
        await asyncio.to_thread(_update_notice_row, notice_id, status=_NOTICE_STATUS_FAILED, error="Invalid guild_id/user_id")
        return

    guild = None
    try:
        guild = bot.get_guild(guild_id)
    except Exception:
        guild = None

    user: Optional[discord.abc.User] = None
    try:
        if guild is not None:
            user = guild.get_member(user_id)
        if user is None:
            user = await bot.fetch_user(user_id)
    except Exception as e:
        await asyncio.to_thread(_update_notice_row, notice_id, status=_NOTICE_STATUS_FAILED, error=f"Could not resolve user: {type(e).__name__}")
        return

    try:
        message = await user.send(
            embed=_notice_dm_embed(row),
            view=MemberActivityNoticeDMView(),
            allowed_mentions=discord.AllowedMentions.none(),
        )
        await asyncio.to_thread(
            _update_notice_row,
            notice_id,
            status=_NOTICE_STATUS_DELIVERED,
            sent_at=_utcnow().isoformat(),
            dm_message_id=str(getattr(message, "id", "")),
            error=None,
        )
    except discord.Forbidden:
        await asyncio.to_thread(
            _update_notice_row,
            notice_id,
            status=_NOTICE_STATUS_DM_BLOCKED,
            attempted_at=_utcnow().isoformat(),
            error="User has DMs closed or blocked the bot.",
        )
    except Exception as e:
        await asyncio.to_thread(
            _update_notice_row,
            notice_id,
            status=_NOTICE_STATUS_FAILED,
            attempted_at=_utcnow().isoformat(),
            error=f"{type(e).__name__}: {str(e)[:180]}",
        )


async def _expire_passed_notice_deadlines() -> None:
    now = _utcnow()
    try:
        rows, _warning = await asyncio.wait_for(
            asyncio.to_thread(_select_notice_rows, limit=1000),
            timeout=8.0,
        )
    except Exception as exc:
        print(f"⚠️ member_activity_notices deadline scan skipped: {exc!r}")
        return
    for row in rows:
        status = str(row.get("status") or "")
        if status not in {_NOTICE_STATUS_DELIVERED, _NOTICE_STATUS_SCHEDULED}:
            continue
        deadline = _coerce_utc(row.get("deadline_at"))
        if deadline is not None and deadline < now:
            await asyncio.to_thread(
            _update_notice_row,
            str(row.get("notice_id")),
            status=_NOTICE_STATUS_DEADLINE_PASSED,
        )


async def _process_due_member_notices(bot: Any, *, one_pass: bool = False) -> None:
    while True:
        try:
            await _expire_passed_notice_deadlines()
            try:
                rows, warning = await asyncio.wait_for(
                    asyncio.to_thread(_due_notice_rows, limit=20),
                    timeout=8.0,
                )
            except Exception as exc:
                print(f"⚠️ member_activity_notices due scan skipped: {exc!r}")
                rows, warning = [], "Due notice scan timed out or failed."
            if warning:
                print(f"⚠️ member activity notices: {warning}")
            for row in rows:
                await _send_notice_row(bot, row)
                await asyncio.sleep(_NOTICE_SEND_DELAY_SECONDS)
        except Exception as e:
            print(f"⚠️ member activity notice worker error: {repr(e)}")

        if one_pass:
            return
        await asyncio.sleep(_NOTICE_WORKER_INTERVAL_SECONDS)


def _start_member_notice_worker(bot: Any) -> None:
    """Attach the member notice worker safely.

    discord.py 2.x does not allow accessing bot.loop from a synchronous setup
    path before login. The worker is therefore attached as an on_ready listener
    and the actual asyncio task is created only after Discord has connected.
    """
    global _NOTICE_WORKER_STARTED
    if _NOTICE_WORKER_STARTED:
        return
    _NOTICE_WORKER_STARTED = True

    try:
        bot.add_view(MemberActivityNoticeDMView())
    except Exception:
        pass

    async def _runner() -> None:
        try:
            await bot.wait_until_ready()
        except Exception:
            pass
        await _process_due_member_notices(bot)

    async def _on_ready_member_activity_notices() -> None:
        try:
            existing = getattr(bot, "_member_activity_notice_worker_task", None)
            if existing is not None and not existing.done():
                return
            task = asyncio.create_task(_runner(), name="member_activity_notices_worker")
            setattr(bot, "_member_activity_notice_worker_task", task)
            print("📩 member_activity_notices worker started")
        except Exception as e:
            print(f"⚠️ member_activity_notices worker failed to start: {repr(e)}")

    try:
        bot.add_listener(_on_ready_member_activity_notices, "on_ready")
        print("📩 member_activity_notices worker listener attached")
    except Exception as e:
        print(f"⚠️ member_activity_notices worker listener failed to attach: {repr(e)}")


def _notice_results_embed(guild: discord.Guild) -> discord.Embed:
    rows, warning = _select_notice_rows(guild_id=int(guild.id), limit=500)
    counts: dict[str, int] = {}
    for row in rows:
        status = str(row.get("status") or "unknown")
        counts[status] = counts.get(status, 0) + 1

    def _count(name: str) -> int:
        return counts.get(name, 0)

    embed = discord.Embed(
        title="📊 Member Activity Notice Results",
        description="Latest notice delivery and response status for this server.",
        color=discord.Color.blurple(),
        timestamp=_utcnow(),
    )
    embed.add_field(
        name="Summary",
        value=_safe_field(
            f"Scheduled: **{_count(_NOTICE_STATUS_SCHEDULED)}**\n"
            f"Delivered: **{_count(_NOTICE_STATUS_DELIVERED)}**\n"
            f"Responded staying: **{_count(_NOTICE_STATUS_RESPONDED_STAYING)}**\n"
            f"Okay leaving: **{_count(_NOTICE_STATUS_OK_LEAVING)}**\n"
            f"DM blocked: **{_count(_NOTICE_STATUS_DM_BLOCKED)}**\n"
            f"Deadline passed: **{_count(_NOTICE_STATUS_DEADLINE_PASSED)}**\n"
            f"Failed: **{_count(_NOTICE_STATUS_FAILED)}**"
        ),
        inline=False,
    )
    recent = rows[:8]
    if recent:
        lines = []
        for row in recent:
            name = _trim(str(row.get("user_display_name") or row.get("user_id")), 40)
            status = str(row.get("status") or "unknown")
            deadline = _discord_timestamp(_coerce_utc(row.get("deadline_at")), "R")
            lines.append(f"• **{name}** — `{status}` — deadline {deadline}")
        embed.add_field(name="Recent Notices", value=_safe_field("\n".join(lines), 900), inline=False)
    if warning:
        embed.add_field(name="Storage Warning", value=_safe_field(warning), inline=False)
    return embed


class NoticeScheduleModal(discord.ui.Modal):
    def __init__(
        self,
        *,
        scope: str,
        report: InactiveScanReport,
        page: int = 0,
        selected: Optional[InactiveMemberCandidate] = None,
    ) -> None:
        self.scope = scope
        self.report = report
        self.page = int(page)
        self.selected = selected
        title = "Schedule Activity Notice"
        if selected is not None:
            title = "Notice This Member"
        super().__init__(title=title, timeout=600)

        self.send_at = discord.ui.TextInput(
            label="Send when?",
            placeholder="now, +2h, +3d, or 2026-05-11 7:00 PM",
            default="now",
            required=True,
            max_length=40,
        )
        self.deadline_at = discord.ui.TextInput(
            label="Response deadline",
            placeholder="+3d or 2026-05-14 8:00 PM",
            default="+3d",
            required=True,
            max_length=40,
        )
        self.timezone_name = discord.ui.TextInput(
            label="Timezone for typed dates",
            placeholder="America/New_York, UTC, America/Los_Angeles",
            default=_NOTICE_DEFAULT_TZ,
            required=True,
            max_length=64,
        )
        self.staff_note = discord.ui.TextInput(
            label="Optional staff note",
            placeholder="Optional short note shown in the DM",
            required=False,
            style=discord.TextStyle.paragraph,
            max_length=500,
        )
        self.add_item(self.send_at)
        self.add_item(self.deadline_at)
        self.add_item(self.timezone_name)
        self.add_item(self.staff_note)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not await _require_review_permission(interaction):
            return
        if interaction.guild is None:
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            tz_text = str(self.timezone_name.value or _NOTICE_DEFAULT_TZ).strip()
            now = _utcnow()
            send_at = _parse_notice_datetime(str(self.send_at.value), timezone_name=tz_text, now=now)
            deadline_at = _parse_notice_datetime(str(self.deadline_at.value), timezone_name=tz_text, now=send_at)
            if deadline_at <= send_at:
                await interaction.followup.send("❌ The response deadline must be after the send time.", ephemeral=True)
                return
        except Exception as e:
            await interaction.followup.send(f"❌ Could not understand the date/time: {e}", ephemeral=True)
            return

        targets = _candidate_notice_scope(self.report, self.scope, page=self.page, selected=self.selected)
        if not targets:
            await interaction.followup.send("No users matched that notice scope.", ephemeral=True)
            return

        saved = 0
        memory_only = 0
        for candidate in targets:
            row = _build_notice_row(
                guild=interaction.guild,
                candidate=candidate,
                scope="selected" if self.selected is not None else self.scope,
                send_at=send_at,
                deadline_at=deadline_at,
                created_by=int(interaction.user.id),
                note=str(self.staff_note.value or ""),
            )
            persisted, _message = _upsert_notice_row(row)
            saved += 1
            if not persisted:
                memory_only += 1

        # Wake the worker for immediate notices without making the interaction wait.
        try:
            interaction.client.loop.create_task(_process_due_member_notices(interaction.client, one_pass=True))
        except Exception:
            pass

        await interaction.followup.send(
            _trim(
                f"📩 **Member Activity Notices queued**\n\n"
                f"Scope: **{_notice_scope_label('selected' if self.selected is not None else self.scope)}**\n"
                f"Users queued: **{saved}**\n"
                f"Send time: {_discord_timestamp(send_at, 'F')} ({_discord_timestamp(send_at, 'R')})\n"
                f"Response deadline: {_discord_timestamp(deadline_at, 'F')} ({_discord_timestamp(deadline_at, 'R')})\n"
                + (f"\n⚠️ {memory_only} notice(s) are memory-only because the optional `{_NOTICE_TABLE}` table was unavailable." if memory_only else "")
            ),
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )


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
        notice_button = discord.ui.Button(
            label="Notice This User",
            emoji="📩",
            style=discord.ButtonStyle.primary,
        )
        lock_button.callback = self._toggle_lock  # type: ignore[assignment]
        notice_button.callback = self._notice_this_user  # type: ignore[assignment]
        self.add_item(lock_button)
        self.add_item(notice_button)

    async def _notice_this_user(self, interaction: discord.Interaction) -> None:
        if not await _require_review_permission(interaction):
            return
        if interaction.guild is None:
            return
        fake_report = InactiveScanReport(
            guild_id=int(interaction.guild.id),
            scanned_at=_utcnow(),
            options=InactiveScanOptions(),
            total_members_seen=1,
            candidates=[self.candidate],
            protected=[],
            cannot_remove=[],
            data_warnings=[],
        )
        await interaction.response.send_modal(
            NoticeScheduleModal(scope="selected", report=fake_report, page=0, selected=self.candidate)
        )

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
        notice_review_button = discord.ui.Button(label="Notice Review", emoji="📣", style=discord.ButtonStyle.primary, row=4)
        notice_purge_button = discord.ui.Button(label="Notice Purge-Safe", emoji="⚠️", style=discord.ButtonStyle.secondary, row=4)
        notice_manual_button = discord.ui.Button(label="Notice Manual", emoji="📩", style=discord.ButtonStyle.secondary, row=4)
        notice_results_button = discord.ui.Button(label="Notice Results", emoji="📊", style=discord.ButtonStyle.secondary, row=4)

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
        notice_review_button.callback = self._notice_review_list  # type: ignore[assignment]
        notice_purge_button.callback = self._notice_purge_safe_list  # type: ignore[assignment]
        notice_manual_button.callback = self._notice_manual_only_list  # type: ignore[assignment]
        notice_results_button.callback = self._show_notice_results  # type: ignore[assignment]

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
        self.add_item(notice_review_button)
        self.add_item(notice_purge_button)
        self.add_item(notice_manual_button)
        self.add_item(notice_results_button)

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

    async def _open_notice_modal(self, interaction: discord.Interaction, scope: str) -> None:
        if not await _require_review_permission(interaction):
            return
        if interaction.guild is None:
            return
        targets = _candidate_notice_scope(self.report, scope, page=self.page)
        if not targets:
            return await reply_once(
                interaction,
                {"content": f"No users matched **{_notice_scope_label(scope)}** for this scan.", "ephemeral": True},
            )
        await interaction.response.send_modal(NoticeScheduleModal(scope=scope, report=self.report, page=self.page))

    async def _notice_review_list(self, interaction: discord.Interaction) -> None:
        await self._open_notice_modal(interaction, "review")

    async def _notice_purge_safe_list(self, interaction: discord.Interaction) -> None:
        await self._open_notice_modal(interaction, "purge_safe")

    async def _notice_manual_only_list(self, interaction: discord.Interaction) -> None:
        await self._open_notice_modal(interaction, "manual_only")

    async def _show_notice_results(self, interaction: discord.Interaction) -> None:
        if not await _require_review_permission(interaction):
            return
        if interaction.guild is None:
            return
        await interaction.response.send_message(
            embed=await asyncio.wait_for(
                asyncio.to_thread(_notice_results_embed, interaction.guild),
                timeout=8.0,
            ),
            ephemeral=True,
        )

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

@members_group.command(
    name="coverage",
    description="Check whether inactive-member evidence is safe for cleanup.",
)
@app_commands.describe(
    required_days="Coverage window to verify. Default 90 days.",
)
async def members_coverage(
    interaction: discord.Interaction,
    required_days: int = 90,
) -> None:
    if not await _require_review_permission(interaction):
        return
    if interaction.guild is None:
        return

    await interaction.response.defer(
        ephemeral=True,
        thinking=True,
    )

    safe_days = max(7, min(int(required_days or 90), 730))

    status = await get_activity_coverage_status(
        int(interaction.guild.id),
        required_days=safe_days,
    )

    embed = discord.Embed(
        title="📡 Inactive-Member Evidence Coverage",
        description=(
            "This reports whether Dank Shield has enough continuous, "
            "directly observed activity data to authorize cleanup."
        ),
        color=(
            discord.Color.green()
            if status.actionable
            else discord.Color.orange()
        ),
        timestamp=discord.utils.utcnow(),
    )

    embed.add_field(
        name="Mode",
        value=(
            "✅ **Actionable**"
            if status.actionable
            else "🟡 **Review-only**"
        ),
        inline=False,
    )

    embed.add_field(
        name="Coverage",
        value=(
            f"Observed: **{status.observed_days} day(s)**\n"
            f"Required: **{status.required_days} day(s)**\n"
            f"Storage ready: **{'Yes' if status.storage_ready else 'No'}**"
        ),
        inline=False,
    )

    embed.add_field(
        name="Why",
        value=str(status.reason)[:1024],
        inline=False,
    )

    if status.continuous_since is not None:
        embed.add_field(
            name="Continuous Since",
            value=f"<t:{int(status.continuous_since.timestamp())}:F>",
            inline=False,
        )

    if status.last_heartbeat_at is not None:
        embed.add_field(
            name="Last Heartbeat",
            value=f"<t:{int(status.last_heartbeat_at.timestamp())}:R>",
            inline=False,
        )

    if status.last_error:
        embed.add_field(
            name="Last Tracker Error",
            value=str(status.last_error)[:1024],
            inline=False,
        )

    embed.set_footer(
        text=(
            "Restarts, stale heartbeats, or failed writes reset the "
            "continuous proof window."
        )
    )

    await interaction.followup.send(
        embed=embed,
        ephemeral=True,
        allowed_mentions=discord.AllowedMentions.none(),
    )


@members_group.command(name="scan", description="Review verified/resident activity using safe default thresholds.")
async def members_scan(interaction: discord.Interaction) -> None:
    await _run_activity_scan(interaction)


@members_group.command(name="scan-custom", description="Review member activity with custom thresholds.")
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


@members_group.command(name="notices", description="Show member activity notice delivery and response results.")
async def members_notices(interaction: discord.Interaction) -> None:
    if not await _require_review_permission(interaction):
        return
    if interaction.guild is None:
        return
    await interaction.response.send_message(
        embed=await asyncio.wait_for(
            asyncio.to_thread(_notice_results_embed, interaction.guild),
            timeout=8.0,
        ),
        ephemeral=True,
        allowed_mentions=discord.AllowedMentions.none(),
    )


@members_group.command(name="scan-last", description="Reopen the latest member activity scan since this restart.")
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
        if dank_group.get_command("members") is None:
            dank_group.add_command(members_group)
            print("✅ public_members_group: attached /dank members post-verification activity review commands")
        else:
            print("✅ public_members_group: /dank members already attached")
        _start_member_notice_worker(bot)
        _REGISTERED = True
    except Exception as e:
        print(f"⚠️ public_members_group failed attaching /dank members: {repr(e)}")
        raise


__all__ = [
    "members_group",
    "register_public_members_group_commands",
]
