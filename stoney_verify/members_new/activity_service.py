from __future__ import annotations

"""Inactive member server-activity scoring for Dank Shield.

This service deliberately separates "scan and explain" from any future member
removal/cleanup action.

Accuracy rule:
- This service does NOT use Discord online/offline/idle presence.
- Users can appear offline, and presence is not a reliable activity signal.
- Scores are based only on activity Dank Shield can observe inside this server:
  ticket messages, ticket lifecycle rows, member/verification records, activity
  events, and server join timestamps as a fallback.

Plain-English rule:
- Normal verified/member roles are NOT cleanup-protected by default.
- Staff/admin/setup/protected roles are cleanup-protected by default.
- Missing database tables lower data confidence; they should not dump raw API
  errors into the user-facing dashboard.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Optional

import discord

try:
    from stoney_verify.guild_config import get_guild_config
except Exception:
    get_guild_config = None  # type: ignore

try:
    from stoney_verify.globals import get_supabase, now_utc
except Exception:
    get_supabase = None  # type: ignore

    def now_utc() -> datetime:  # type: ignore
        return datetime.now(timezone.utc)


@dataclass(frozen=True)
class InactiveScanOptions:
    inactive_days: int = 90
    grace_days: int = 14
    protect_bots: bool = True
    protect_staff: bool = True
    include_low_confidence: bool = False
    include_medium_confidence: bool = True
    include_high_confidence: bool = True
    max_candidates: int = 250


@dataclass
class MemberActivitySignal:
    source: str
    timestamp: Optional[datetime]
    confidence: str
    note: str


@dataclass
class InactiveMemberCandidate:
    user_id: int
    display_name: str
    mention: str
    joined_at: Optional[datetime]
    last_seen_at: Optional[datetime]
    inactivity_days: Optional[int]
    activity_score: int
    confidence: str
    status: str
    removable: bool
    protected: bool
    cannot_remove: bool
    reasons: list[str] = field(default_factory=list)
    signals: list[MemberActivitySignal] = field(default_factory=list)

    def short_reason(self, limit: int = 180) -> str:
        text = " ".join(self.reasons[:3]).strip() or "No reason recorded."
        return text if len(text) <= limit else text[: max(0, limit - 1)] + "…"


@dataclass
class InactiveScanReport:
    guild_id: int
    scanned_at: datetime
    options: InactiveScanOptions
    total_members_seen: int
    candidates: list[InactiveMemberCandidate]
    protected: list[InactiveMemberCandidate]
    cannot_remove: list[InactiveMemberCandidate]
    data_warnings: list[str]
    active_enough_count: int = 0
    inactive_hidden_by_filter_count: int = 0
    unknown_activity_count: int = 0
    data_sources_read: int = 0
    data_sources_attempted: int = 0

    @property
    def removable(self) -> list[InactiveMemberCandidate]:
        return [c for c in self.candidates if c.removable]

    @property
    def needs_review(self) -> list[InactiveMemberCandidate]:
        return [c for c in self.candidates if c.status == "Needs review"]

    @property
    def quiet_review_count(self) -> int:
        return len(self.candidates) + int(self.inactive_hidden_by_filter_count)

    @property
    def protected_or_blocked_count(self) -> int:
        return len(self.protected) + len(self.cannot_remove)

    def percent(self, count: int) -> int:
        try:
            if self.total_members_seen <= 0:
                return 0
            return max(0, min(100, round((int(count) / int(self.total_members_seen)) * 100)))
        except Exception:
            return 0

    @property
    def active_activity_percent(self) -> int:
        return self.percent(self.active_enough_count)

    @property
    def quiet_review_percent(self) -> int:
        return self.percent(self.quiet_review_count)

    @property
    def protected_or_blocked_percent(self) -> int:
        return self.percent(self.protected_or_blocked_count)

    @property
    def unknown_activity_percent(self) -> int:
        return self.percent(self.unknown_activity_count)

    @property
    def data_coverage_percent(self) -> int:
        try:
            if self.data_sources_attempted <= 0:
                return 0
            return max(0, min(100, round((self.data_sources_read / self.data_sources_attempted) * 100)))
        except Exception:
            return 0

    @property
    def data_confidence_label(self) -> str:
        if self.data_sources_read >= 3:
            return "Good"
        if self.data_sources_read >= 1:
            return "Partial"
        return "Low"


_LAST_SCANS: dict[int, InactiveScanReport] = {}


def remember_scan(report: InactiveScanReport) -> None:
    _LAST_SCANS[int(report.guild_id)] = report


def get_last_scan(guild_id: int) -> Optional[InactiveScanReport]:
    return _LAST_SCANS.get(int(guild_id))


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or isinstance(value, bool):
            return int(default)
        text = str(value).strip()
        return int(text) if text else int(default)
    except Exception:
        return int(default)


def _safe_dt(value: Any) -> Optional[datetime]:
    try:
        if value is None:
            return None
        if isinstance(value, datetime):
            dt = value
        else:
            raw = str(value).strip()
            if not raw:
                return None
            raw = raw.replace("Z", "+00:00")
            dt = datetime.fromisoformat(raw)
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def _days_since(dt: Optional[datetime], now: Optional[datetime] = None) -> Optional[int]:
    if dt is None:
        return None
    current = now or now_utc()
    try:
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        return max(0, int((current - dt).total_seconds() // 86400))
    except Exception:
        return None


def _cfg_value(cfg: Any, key: str, default: Any = None) -> Any:
    try:
        value = getattr(cfg, key, None)
        if value is not None:
            return value
    except Exception:
        pass
    try:
        if hasattr(cfg, "get"):
            value = cfg.get(key)
            if value is not None:
                return value
    except Exception:
        pass
    try:
        for bucket in ("settings", "config", "metadata", "meta"):
            nested = getattr(cfg, bucket, None)
            if isinstance(nested, Mapping) and nested.get(key) is not None:
                return nested.get(key)
            if hasattr(cfg, "get"):
                nested = cfg.get(bucket)
                if isinstance(nested, Mapping) and nested.get(key) is not None:
                    return nested.get(key)
    except Exception:
        pass
    return default


def _role_ids_from_value(value: Any) -> set[int]:
    out: set[int] = set()
    try:
        if value is None:
            return out
        if isinstance(value, (list, tuple, set)):
            raw_items = value
        else:
            raw = str(value).replace(";", ",").replace(" ", ",")
            raw_items = raw.split(",")
        for item in raw_items:
            rid = _safe_int(item, 0)
            if rid > 0:
                out.add(rid)
    except Exception:
        pass
    return out


async def _load_protected_role_ids(guild: discord.Guild) -> set[int]:
    """Load roles that should be protected from future cleanup actions.

    Do not include normal verified/member roles here. Those roles represent normal
    membership, not cleanup immunity. Including them made the first live scan say
    almost the whole server was protected/skipped.
    """
    protected: set[int] = set()
    try:
        if get_guild_config is None:
            return protected
        cfg = await get_guild_config(int(guild.id))  # type: ignore[misc]
        for key in (
            "staff_role_id",
            "vc_staff_role_id",
            "server_control_role_id",
            "bot_manager_role_id",
            "inactive_cleanup_protected_role_ids",
            "protected_role_ids",
        ):
            value = _cfg_value(cfg, key, None)
            protected |= _role_ids_from_value(value)
    except Exception:
        pass
    return protected


def _member_role_ids(member: discord.Member) -> set[int]:
    try:
        return {int(role.id) for role in member.roles or [] if int(role.id) != int(member.guild.default_role.id)}
    except Exception:
        return set()


def _is_staff_like(member: discord.Member, protected_role_ids: set[int]) -> bool:
    try:
        perms = member.guild_permissions
        if perms.administrator or perms.manage_guild or perms.manage_roles or perms.kick_members or perms.ban_members or perms.moderate_members:
            return True
    except Exception:
        pass
    try:
        return bool(_member_role_ids(member).intersection(protected_role_ids))
    except Exception:
        return False


def _bot_can_remove(member: discord.Member) -> tuple[bool, str]:
    try:
        guild = member.guild
        me = guild.me
        if me is None:
            return False, "Bot member could not be resolved."
        if int(member.id) == int(guild.owner_id):
            return False, "Server owner is protected."
        if int(member.id) == int(me.id):
            return False, "Dank Shield will never action itself."
        if not me.guild_permissions.kick_members:
            return False, "Bot is missing Kick Members permission for future cleanup actions."
        if guild.owner_id != me.id and member.top_role >= me.top_role:
            return False, "Member is above or equal to the bot role."
        return True, ""
    except Exception:
        return False, "Could not verify bot role hierarchy."


def _confidence_rank(value: str) -> int:
    v = str(value or "").lower()
    if v == "high":
        return 3
    if v == "medium":
        return 2
    if v == "low":
        return 1
    return 0


def _confidence_from_signals(signals: list[MemberActivitySignal], *, db_available: bool) -> str:
    if any(_confidence_rank(s.confidence) >= 3 for s in signals):
        return "High"
    if db_available and signals:
        return "Medium"
    return "Low"


def _best_last_seen(signals: list[MemberActivitySignal], fallback: Optional[datetime]) -> Optional[datetime]:
    timestamps = [s.timestamp for s in signals if s.timestamp is not None]
    if fallback is not None:
        timestamps.append(fallback)
    if not timestamps:
        return None
    return max(timestamps)


def _activity_score(days_inactive: Optional[int], confidence: str) -> int:
    if days_inactive is None:
        return 0
    if days_inactive < 7:
        base = 100
    elif days_inactive < 30:
        base = 75
    elif days_inactive < 60:
        base = 45
    elif days_inactive < 90:
        base = 25
    else:
        base = 5
    if confidence.lower() == "low":
        base = max(0, base - 15)
    return max(0, min(100, int(base)))


def _scan_passes_confidence_filter(candidate: InactiveMemberCandidate, options: InactiveScanOptions) -> bool:
    c = candidate.confidence.lower()
    if c == "high":
        return options.include_high_confidence
    if c == "medium":
        return options.include_medium_confidence
    if c == "low":
        return options.include_low_confidence
    return False


def _rows_by_user(rows: Iterable[Mapping[str, Any]], *, user_keys: tuple[str, ...], time_keys: tuple[str, ...], source: str, confidence: str, note: str) -> dict[int, MemberActivitySignal]:
    out: dict[int, MemberActivitySignal] = {}
    for row in rows or []:
        if not isinstance(row, Mapping):
            continue
        uid = 0
        for key in user_keys:
            uid = _safe_int(row.get(key), 0)
            if uid > 0:
                break
        if uid <= 0:
            continue
        best: Optional[datetime] = None
        for key in time_keys:
            dt = _safe_dt(row.get(key))
            if dt and (best is None or dt > best):
                best = dt
        if best is None:
            continue
        previous = out.get(uid)
        if previous is None or (previous.timestamp and best > previous.timestamp):
            out[uid] = MemberActivitySignal(source=source, timestamp=best, confidence=confidence, note=note)
    return out


def _table_display_name(table: str) -> str:
    return {
        "ticket_messages": "ticket message history",
        "tickets": "ticket history",
        "member_joins": "member tracking history",
        "activity_feed_events": "activity-feed history",
    }.get(str(table), str(table))


def _select_recent_rows(table: str, guild_id: int, *, limit: int = 5000) -> tuple[list[dict[str, Any]], bool, str]:
    """Read rows without assuming exact optional column names.

    The old version selected a hard-coded column list. If a server DB was missing
    one optional column, Supabase raised APIError and the dashboard printed ugly
    raw errors. select('*') keeps this resilient across schema variants.
    """
    if get_supabase is None:
        return [], False, "Supabase is unavailable, so optional server-history tables could not be checked."
    sb = get_supabase()
    if sb is None:
        return [], False, "Supabase is unavailable, so optional server-history tables could not be checked."

    def _try_query(guild_value: Any) -> list[dict[str, Any]]:
        resp = sb.table(table).select("*").eq("guild_id", guild_value).limit(int(limit)).execute()
        rows = getattr(resp, "data", None) or []
        return [dict(r) for r in rows if isinstance(r, Mapping)]

    try:
        rows = _try_query(str(int(guild_id)))
        if not rows:
            try:
                rows = _try_query(int(guild_id))
            except Exception:
                pass
        return rows, True, ""
    except Exception:
        return [], False, f"Optional {_table_display_name(table)} was not readable. The scan still works, but confidence is lower."


async def _load_known_activity_signals(guild_id: int) -> tuple[dict[int, list[MemberActivitySignal]], list[str], int, int]:
    warnings: list[str] = []
    merged: dict[int, list[MemberActivitySignal]] = {}

    table_specs = (
        (
            "ticket_messages",
            ("user_id", "author_id", "member_id", "discord_user_id"),
            ("created_at", "timestamp", "sent_at", "updated_at"),
            "ticket message",
            "High",
            "Had ticket-message activity recorded by Dank Shield.",
        ),
        (
            "tickets",
            ("user_id", "creator_id", "member_id", "opened_by_id"),
            ("last_activity_at", "updated_at", "created_at", "closed_at"),
            "ticket",
            "Medium",
            "Had ticket lifecycle activity recorded by Dank Shield.",
        ),
        (
            "member_joins",
            ("user_id", "member_id", "discord_user_id"),
            ("last_seen_at", "last_activity_at", "updated_at", "joined_at", "created_at"),
            "member record",
            "Medium",
            "Had member tracking data recorded by Dank Shield.",
        ),
        (
            "activity_feed_events",
            ("user_id", "actor_id", "member_id", "target_user_id"),
            ("updated_at", "created_at", "timestamp"),
            "activity feed",
            "Medium",
            "Had activity-feed events recorded by Dank Shield.",
        ),
    )

    sources_read = 0
    attempted = len(table_specs)
    for table, user_keys, time_keys, source, confidence, note in table_specs:
        rows, ok, warning = await __import__("asyncio").to_thread(_select_recent_rows, table, guild_id)
        if not ok:
            if warning:
                warnings.append(warning)
            continue
        sources_read += 1
        for uid, signal in _rows_by_user(rows, user_keys=user_keys, time_keys=time_keys, source=source, confidence=confidence, note=note).items():
            merged.setdefault(uid, []).append(signal)

    if sources_read == 0:
        warnings.append(
            "Data confidence is low: no optional server-activity history tables were readable. "
            "The scan is using join dates, role safety, and Discord hierarchy only."
        )
    elif sources_read < attempted:
        warnings.append(
            f"Data confidence is partial: {sources_read}/{attempted} optional server-activity sources were readable. "
            "Percentages may improve as tracking history fills in."
        )

    return merged, warnings, sources_read, attempted


async def scan_inactive_members(guild: discord.Guild, options: Optional[InactiveScanOptions] = None) -> InactiveScanReport:
    options = options or InactiveScanOptions()
    now = now_utc()
    protected_role_ids = await _load_protected_role_ids(guild)
    activity_signals, data_warnings, sources_read, sources_attempted = await _load_known_activity_signals(int(guild.id))
    db_available = bool(activity_signals)

    members = list(getattr(guild, "members", []) or [])
    candidates: list[InactiveMemberCandidate] = []
    protected: list[InactiveMemberCandidate] = []
    cannot_remove: list[InactiveMemberCandidate] = []
    active_enough_count = 0
    inactive_hidden_by_filter_count = 0
    unknown_activity_count = 0

    for member in members:
        try:
            uid = int(member.id)
            joined_at = _safe_dt(getattr(member, "joined_at", None))
            signals = list(activity_signals.get(uid, []))
            reasons: list[str] = []
            is_protected = False
            cannot = False

            if uid == int(guild.owner_id):
                is_protected = True
                reasons.append("Server owner is always protected.")
            if getattr(member, "bot", False) and options.protect_bots:
                is_protected = True
                reasons.append("Bot account is protected by default.")
            if options.protect_staff and _is_staff_like(member, protected_role_ids):
                is_protected = True
                reasons.append("Staff/admin/protected role is protected.")
            joined_days = _days_since(joined_at, now)
            if joined_days is not None and joined_days < int(options.grace_days):
                is_protected = True
                reasons.append(f"Joined {joined_days} day(s) ago, inside the {options.grace_days}-day new-member grace period.")

            bot_ok, bot_reason = _bot_can_remove(member)
            if not bot_ok:
                cannot = True
                reasons.append(bot_reason)

            # Fallback to join date only when no better server-activity signal exists.
            last_seen = _best_last_seen(signals, joined_at)
            inactivity_days = _days_since(last_seen, now)
            confidence = _confidence_from_signals(signals, db_available=db_available)
            score = _activity_score(inactivity_days, confidence)

            if not signals:
                unknown_activity_count += 1
                reasons.append("No message/ticket/activity history was found for this member; join date is being used as the fallback.")

            if inactivity_days is None:
                reasons.append("No reliable server-activity timestamp was available.")
            elif inactivity_days >= int(options.inactive_days):
                reasons.append(f"No tracked server activity for {inactivity_days}+ days; threshold is {options.inactive_days} days.")
            else:
                active_enough_count += 1
                reasons.append(f"Recent enough server activity: {inactivity_days} day(s) ago, below the inactive threshold.")

            if confidence.lower() == "low":
                reasons.append("Confidence is low because Dank Shield does not have enough server-history data for this member yet.")

            inactive_enough = inactivity_days is not None and inactivity_days >= int(options.inactive_days)
            if is_protected:
                status = "Protected"
                removable = False
            elif cannot:
                status = "Cannot action"
                removable = False
            elif inactive_enough:
                status = "Review candidate" if confidence.lower() in {"high", "medium"} else "Needs review"
                removable = status == "Review candidate"
            else:
                status = "Active enough"
                removable = False

            candidate = InactiveMemberCandidate(
                user_id=uid,
                display_name=str(getattr(member, "display_name", None) or getattr(member, "name", None) or member),
                mention=getattr(member, "mention", f"<@{uid}>"),
                joined_at=joined_at,
                last_seen_at=last_seen,
                inactivity_days=inactivity_days,
                activity_score=score,
                confidence=confidence,
                status=status,
                removable=removable,
                protected=is_protected,
                cannot_remove=cannot,
                reasons=reasons,
                signals=signals,
            )

            if status == "Protected":
                protected.append(candidate)
            elif status == "Cannot action":
                cannot_remove.append(candidate)
            elif inactive_enough:
                if _scan_passes_confidence_filter(candidate, options):
                    candidates.append(candidate)
                else:
                    inactive_hidden_by_filter_count += 1
        except Exception:
            unknown_activity_count += 1
            continue

    candidates.sort(key=lambda c: (0 if c.removable else 1, -(c.inactivity_days or 0), c.display_name.lower()))
    if options.max_candidates > 0:
        candidates = candidates[: int(options.max_candidates)]

    report = InactiveScanReport(
        guild_id=int(guild.id),
        scanned_at=now,
        options=options,
        total_members_seen=len(members),
        candidates=candidates,
        protected=protected,
        cannot_remove=cannot_remove,
        data_warnings=data_warnings,
        active_enough_count=active_enough_count,
        inactive_hidden_by_filter_count=inactive_hidden_by_filter_count,
        unknown_activity_count=unknown_activity_count,
        data_sources_read=sources_read,
        data_sources_attempted=sources_attempted,
    )
    remember_scan(report)
    return report


def format_dt(dt: Optional[datetime]) -> str:
    if dt is None:
        return "unknown"
    try:
        return f"<t:{int(dt.timestamp())}:R>"
    except Exception:
        return "unknown"


def report_summary_lines(report: InactiveScanReport) -> list[str]:
    return [
        f"Overall server activity: **{report.active_activity_percent}%** active/recent in this server",
        f"Quiet review pool: **{report.quiet_review_percent}%** of members",
        f"Cleanup safety locks: **{report.protected_or_blocked_count}** member(s), not {report.protected_or_blocked_percent}% skipped",
        f"Data confidence: **{report.data_confidence_label}** ({report.data_coverage_percent}% of optional history sources readable)",
        f"Members scanned: **{report.total_members_seen}**",
        f"Active/recent by server activity: **{report.active_enough_count}**",
        f"Quiet/inactive review candidates shown: **{len(report.candidates)}**",
        f"Needs manual review: **{len(report.needs_review)}**",
        f"Protected by safety rules: **{len(report.protected)}**",
        f"Cannot action because of Discord permissions/hierarchy: **{len(report.cannot_remove)}**",
    ]


__all__ = [
    "InactiveScanOptions",
    "InactiveMemberCandidate",
    "InactiveScanReport",
    "scan_inactive_members",
    "get_last_scan",
    "remember_scan",
    "format_dt",
    "report_summary_lines",
]
