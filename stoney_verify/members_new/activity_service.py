from __future__ import annotations

"""Inactive member server-activity scoring for Dank Shield.

This service deliberately separates "scan and explain" from any future member
removal/cleanup action.

Accuracy rule:
- This service does NOT use Discord online/offline/idle presence.
- Users can appear offline, and presence is not a reliable activity signal.
- Scores are based only on activity Dank Shield can observe inside this server.
- Fresh recent Discord evidence is checked before showing inactive candidates,
  so stale DB history does not create obvious false positives.

Verified/resident rule:
- The main cleanup target is not "people who joined a while ago."
- The target is members who became verified/resident, then went quiet inside the
  server.
- Audit log is used only as a fallback to estimate when a role was granted.

Scan-lock rule:
- Staff can lock a member out of future activity scans after manual review.
- Locks are per guild.
- The preferred persistent store is `member_activity_scan_locks`.
- If that table is missing, locks still work in memory until restart and the
  report warns that persistence is not available.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
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


SCAN_LOCKS_TABLE = "member_activity_scan_locks"
_MEMORY_SCAN_LOCKS: dict[int, dict[int, dict[str, Any]]] = {}
_LAST_SCANS: dict[int, "InactiveScanReport"] = {}

# Bounded fresh evidence sweep. This is intentionally limited so public servers
# do not hammer Discord when scanning.
_RECENT_EVIDENCE_LOOKBACK_DAYS = 180
_RECENT_EVIDENCE_MAX_CHANNELS = 75
_RECENT_EVIDENCE_PER_CHANNEL_LIMIT = 750
_RECENT_EVIDENCE_MAX_MEMBERS_FOR_NAME_MATCH = 2000


@dataclass(frozen=True)
class InactiveScanOptions:
    inactive_days: int = 90
    grace_days: int = 14
    protect_bots: bool = True
    protect_staff: bool = True
    include_low_confidence: bool = True
    include_medium_confidence: bool = True
    include_high_confidence: bool = True
    max_candidates: int = 250
    verified_resident_focus: bool = True
    use_audit_log_fallback: bool = True
    skip_locked_users: bool = True


@dataclass
class MemberActivitySignal:
    source: str
    timestamp: Optional[datetime]
    confidence: str
    note: str


@dataclass
class ScanLockRecord:
    guild_id: int
    user_id: int
    reason: str = "Manual review lock"
    locked_by: Optional[int] = None
    locked_at: Optional[datetime] = None
    persisted: bool = False


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
    verified_or_resident: bool = False
    verified_at: Optional[datetime] = None
    verification_source: str = "unknown"
    post_verification_activity_at: Optional[datetime] = None
    days_since_verification: Optional[int] = None

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
    verified_resident_seen: int = 0
    verified_resident_without_post_activity: int = 0
    audit_log_times_found: int = 0
    locked_users_skipped: int = 0
    scan_lock_persistence: str = "unknown"

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
    def verified_vanished_percent(self) -> int:
        try:
            if self.verified_resident_seen <= 0:
                return 0
            return max(0, min(100, round((self.verified_resident_without_post_activity / self.verified_resident_seen) * 100)))
        except Exception:
            return 0

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
        if self.data_sources_attempted > 0 and self.data_sources_read >= self.data_sources_attempted:
            return "Good"
        if self.data_sources_read >= 1:
            return "Partial"
        return "Low"


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


def _norm(text: Any) -> str:
    return str(text or "").strip().lower()


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


def _memory_lock_records(guild_id: int) -> dict[int, dict[str, Any]]:
    return _MEMORY_SCAN_LOCKS.setdefault(int(guild_id), {})


def _select_scan_lock_rows(guild_id: int) -> tuple[list[dict[str, Any]], bool, str]:
    if get_supabase is None:
        return [], False, "Supabase unavailable; scan locks are memory-only until restart."
    sb = get_supabase()
    if sb is None:
        return [], False, "Supabase unavailable; scan locks are memory-only until restart."
    try:
        resp = sb.table(SCAN_LOCKS_TABLE).select("*").eq("guild_id", str(int(guild_id))).execute()
        rows = getattr(resp, "data", None) or []
        if not rows:
            try:
                resp = sb.table(SCAN_LOCKS_TABLE).select("*").eq("guild_id", int(guild_id)).execute()
                rows = getattr(resp, "data", None) or []
            except Exception:
                pass
        return [dict(r) for r in rows if isinstance(r, Mapping)], True, ""
    except Exception:
        return [], False, f"Optional `{SCAN_LOCKS_TABLE}` table was not readable. Scan locks will work until restart only."


def _write_scan_lock_row(guild_id: int, user_id: int, *, locked: bool, actor_id: Optional[int], reason: str) -> tuple[bool, str]:
    if get_supabase is None:
        return False, "Supabase unavailable; scan lock saved in memory only."
    sb = get_supabase()
    if sb is None:
        return False, "Supabase unavailable; scan lock saved in memory only."
    try:
        payload = {
            "guild_id": str(int(guild_id)),
            "user_id": str(int(user_id)),
            "active": bool(locked),
            "reason": str(reason or "Manual review lock")[:500],
            "locked_by": str(int(actor_id)) if actor_id else None,
            "locked_at": now_utc().isoformat(),
            "updated_at": now_utc().isoformat(),
        }
        sb.table(SCAN_LOCKS_TABLE).upsert(payload, on_conflict="guild_id,user_id").execute()
        return True, "Saved to Supabase."
    except Exception:
        return False, f"Optional `{SCAN_LOCKS_TABLE}` table was not writable. Scan lock saved in memory only."


async def get_scan_lock_records(guild_id: int) -> tuple[list[ScanLockRecord], str]:
    rows, persisted_ok, warning = await __import__("asyncio").to_thread(_select_scan_lock_rows, guild_id)
    records: dict[int, ScanLockRecord] = {}
    for row in rows:
        uid = _safe_int(row.get("user_id"), 0)
        if uid <= 0:
            continue
        active = row.get("active", True)
        if str(active).lower() in {"false", "0", "no", "off"}:
            continue
        records[uid] = ScanLockRecord(
            guild_id=int(guild_id),
            user_id=uid,
            reason=str(row.get("reason") or "Manual review lock"),
            locked_by=_safe_int(row.get("locked_by"), 0) or None,
            locked_at=_safe_dt(row.get("locked_at") or row.get("created_at") or row.get("updated_at")),
            persisted=True,
        )
    for uid, row in _memory_lock_records(guild_id).items():
        if uid not in records:
            records[uid] = ScanLockRecord(
                guild_id=int(guild_id),
                user_id=int(uid),
                reason=str(row.get("reason") or "Manual review lock"),
                locked_by=_safe_int(row.get("locked_by"), 0) or None,
                locked_at=_safe_dt(row.get("locked_at")),
                persisted=False,
            )
    persistence = "persistent" if persisted_ok else "memory-only"
    if warning:
        persistence = f"{persistence}; {warning}"
    return sorted(records.values(), key=lambda r: (r.locked_at or now_utc(), r.user_id), reverse=True), persistence


async def get_scan_locked_user_ids(guild_id: int) -> tuple[set[int], str]:
    records, persistence = await get_scan_lock_records(guild_id)
    return {int(r.user_id) for r in records}, persistence


async def set_scan_user_lock(
    guild_id: int,
    user_id: int,
    *,
    locked: bool,
    actor_id: Optional[int] = None,
    reason: str = "Manual review lock",
) -> tuple[bool, str]:
    guild_id = int(guild_id)
    user_id = int(user_id)
    memory = _memory_lock_records(guild_id)
    if locked:
        memory[user_id] = {
            "reason": str(reason or "Manual review lock"),
            "locked_by": str(actor_id) if actor_id else None,
            "locked_at": now_utc().isoformat(),
        }
    else:
        memory.pop(user_id, None)
    persisted, message = await __import__("asyncio").to_thread(
        _write_scan_lock_row,
        guild_id,
        user_id,
        locked=locked,
        actor_id=actor_id,
        reason=reason,
    )
    if persisted:
        return True, "Saved. This user will be skipped by future scans." if locked else "Unlocked. This user can appear in future scans again."
    return False, message


async def is_scan_user_locked(guild_id: int, user_id: int) -> bool:
    locked, _persistence = await get_scan_locked_user_ids(guild_id)
    return int(user_id) in locked


async def _load_role_sets(guild: discord.Guild) -> tuple[set[int], set[int]]:
    protected: set[int] = set()
    verified_resident: set[int] = set()
    try:
        if get_guild_config is None:
            return protected, verified_resident
        cfg = await get_guild_config(int(guild.id))  # type: ignore[misc]
        for key in (
            "staff_role_id",
            "vc_staff_role_id",
            "server_control_role_id",
            "bot_manager_role_id",
            "inactive_cleanup_protected_role_ids",
            "protected_role_ids",
        ):
            protected |= _role_ids_from_value(_cfg_value(cfg, key, None))
        for key in (
            "verified_role_id",
            "resident_role_id",
            "member_role_id",
        ):
            verified_resident |= _role_ids_from_value(_cfg_value(cfg, key, None))
    except Exception:
        pass
    return protected, verified_resident


def _member_role_ids(member: discord.Member) -> set[int]:
    try:
        return {int(role.id) for role in member.roles or [] if int(role.id) != int(member.guild.default_role.id)}
    except Exception:
        return set()


def _role_collection_ids(value: Any) -> set[int]:
    out: set[int] = set()
    try:
        for role in value or []:
            rid = _safe_int(getattr(role, "id", role), 0)
            if rid > 0:
                out.add(rid)
    except Exception:
        pass
    return out


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


def _bot_can_action(member: discord.Member) -> tuple[bool, str]:
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



def _signal_source_text(signal: MemberActivitySignal) -> str:
    try:
        return str(signal.source or "").strip().lower()
    except Exception:
        return ""


def _signal_is_direct_activity(signal: MemberActivitySignal) -> bool:
    """True when the signal directly proves the member did something."""
    source = _signal_source_text(signal)
    return any(
        key in source
        for key in (
            "recent discord message history",
            "ticket message",
            "message history",
        )
    )


def _signal_is_indirect_activity(signal: MemberActivitySignal) -> bool:
    """True when the signal is useful, but not as strong as direct authorship."""
    if _signal_is_verification_timestamp(signal):
        return False
    source = _signal_source_text(signal)
    return any(
        key in source
        for key in (
            "ticket",
            "activity feed",
            "member record",
            "mod-log",
            "audit",
        )
    )


def _verification_source_strength(source: str) -> int:
    """How trustworthy the verification/resident timestamp is.

    3 = exact DB timestamp
    2 = Discord audit-log role-added timestamp
    0 = join-date fallback / unknown
    """
    text = str(source or "").strip().lower()
    if text.startswith("db:"):
        return 3
    if "audit" in text and "fallback" in text:
        return 2
    if "mod-log" in text and "fallback" in text:
        return 2
    return 0


def _coverage_strength(*, sources_read: int, sources_attempted: int, recent_read: int) -> int:
    """Overall scan coverage strength.

    This intentionally does not require every optional DB table to be readable,
    because many public servers may not have every history table populated yet.
    """
    try:
        read = max(0, int(sources_read))
        attempted = max(1, int(sources_attempted))
        coverage = read / attempted
    except Exception:
        read = 0
        coverage = 0.0

    if recent_read > 0 and read >= 4 and coverage >= 0.75:
        return 3
    if recent_read > 0 and read >= 2 and coverage >= 0.40:
        return 2
    if recent_read > 0 or read >= 1:
        return 1
    return 0


def _activity_signal_strength(
    signals: list[MemberActivitySignal],
    *,
    verified_at: Optional[datetime],
    post_verification_activity_at: Optional[datetime],
) -> int:
    """Strength of activity evidence for this member.

    3 = direct authored/ticket-message activity
    2 = useful indirect DB/mod-log activity
    1 = weak signal exists
    0 = no signal
    """
    if not signals:
        return 0

    relevant = list(signals)
    if verified_at is not None:
        relevant = [s for s in signals if s.timestamp is not None and s.timestamp > verified_at] or list(signals)

    if any(_signal_is_direct_activity(s) and _confidence_rank(s.confidence) >= 3 for s in relevant):
        return 3
    if any(_signal_is_direct_activity(s) for s in relevant):
        return 3
    if any(_signal_is_indirect_activity(s) for s in relevant):
        return 2
    return 1


def _calibrated_candidate_confidence(
    *,
    signals: list[MemberActivitySignal],
    verified_at: Optional[datetime],
    verification_source: str,
    post_verification_activity_at: Optional[datetime],
    sources_read: int,
    sources_attempted: int,
    recent_read: int,
) -> str:
    """Classify confidence without unfairly dumping valid evidence into Low.

    Important distinction:
    - Confidence in a member being ACTIVE can be High from direct activity.
    - Confidence in a member being QUIET is usually Medium unless coverage and
      verification timing are excellent. Absence of evidence should almost never
      be called High.
    """
    verification_strength = _verification_source_strength(verification_source)
    coverage = _coverage_strength(
        sources_read=sources_read,
        sources_attempted=sources_attempted,
        recent_read=recent_read,
    )
    signal_strength = _activity_signal_strength(
        signals,
        verified_at=verified_at,
        post_verification_activity_at=post_verification_activity_at,
    )

    if post_verification_activity_at is not None:
        if signal_strength >= 3:
            return "High"
        if signal_strength >= 2:
            return "Medium"
        return "Low"

    # No post-verification activity found.
    # This is only actionable/reviewable if the verification timestamp is real.
    if verification_strength <= 0:
        return "Low"

    # Exact DB verification + strong readable coverage earns Medium.
    # Audit-log verification + decent coverage also earns Medium.
    if verification_strength >= 3 and coverage >= 2:
        return "Medium"
    if verification_strength >= 2 and coverage >= 2:
        return "Medium"

    # If we have an exact verification timestamp but weak coverage, do not
    # pretend it is useless, but keep it Low so default scans hide it.
    return "Low"


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


def _best_post_verification_activity(signals: list[MemberActivitySignal], verified_at: Optional[datetime]) -> Optional[datetime]:
    if verified_at is None:
        return None
    after = [
        s.timestamp
        for s in signals
        if s.timestamp is not None
        and s.timestamp > verified_at
        and not _signal_is_verification_timestamp(s)
    ]
    return max(after) if after else None


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


def _verification_times_from_rows(rows: Iterable[Mapping[str, Any]], *, user_keys: tuple[str, ...]) -> dict[int, tuple[datetime, str]]:
    out: dict[int, tuple[datetime, str]] = {}
    verification_time_keys = (
        "verified_at",
        "resident_at",
        "verification_completed_at",
        "verified_completed_at",
        "role_granted_at",
        "verified_role_added_at",
        "resident_role_added_at",
    )
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
        source_key = "verification record"
        for key in verification_time_keys:
            dt = _safe_dt(row.get(key))
            if dt and (best is None or dt > best):
                best = dt
                source_key = key
        if best is None:
            continue
        old = out.get(uid)
        if old is None or best > old[0]:
            out[uid] = (best, source_key)
    return out


def _table_display_name(table: str) -> str:
    return {
        "ticket_messages": "ticket message history",
        "tickets": "ticket history",
        "member_joins": "member tracking history",
        "activity_feed_events": "activity-feed history",
    }.get(str(table), str(table))


def _select_recent_rows(table: str, guild_id: int, *, limit: int = 5000) -> tuple[list[dict[str, Any]], bool, str]:
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


async def _load_known_activity_signals(guild_id: int) -> tuple[dict[int, list[MemberActivitySignal]], dict[int, tuple[datetime, str]], list[str], int, int]:
    warnings: list[str] = []
    merged: dict[int, list[MemberActivitySignal]] = {}
    verification_times: dict[int, tuple[datetime, str]] = {}

    table_specs = (
        ("ticket_messages", ("user_id", "author_id", "member_id", "discord_user_id"), ("created_at", "timestamp", "sent_at", "updated_at"), "ticket message", "High", "Had ticket-message activity recorded by Dank Shield."),
        ("tickets", ("user_id", "creator_id", "member_id", "opened_by_id"), ("last_activity_at", "updated_at", "created_at", "closed_at"), "ticket", "Medium", "Had ticket lifecycle activity recorded by Dank Shield."),
        ("member_joins", ("user_id", "member_id", "discord_user_id"), ("last_seen_at", "last_activity_at", "updated_at", "joined_at", "created_at"), "member record", "Medium", "Had member tracking data recorded by Dank Shield."),
        ("activity_feed_events", ("user_id", "actor_id", "member_id", "target_user_id"), ("updated_at", "created_at", "timestamp"), "activity feed", "Medium", "Had activity-feed events recorded by Dank Shield."),
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
        if table in {"member_joins", "activity_feed_events", "tickets"}:
            for uid, pair in _verification_times_from_rows(rows, user_keys=user_keys).items():
                old = verification_times.get(uid)
                if old is None or pair[0] > old[0]:
                    verification_times[uid] = pair

    if sources_read == 0:
        warnings.append("Data confidence is low: no optional server-activity history tables were readable. The scan is using join dates, role safety, and Discord hierarchy only.")
    elif sources_read < attempted:
        warnings.append(f"Data confidence is partial: {sources_read}/{attempted} optional server-activity sources were readable. Percentages may improve as tracking history fills in.")

    warnings.append("Confidence calibration: High requires direct member activity evidence; Medium is purge-safe review evidence; Low is shown for manual review but is not purge-safe. Mod-log embeds are scanned, not just plain message text.")

    return merged, verification_times, warnings, sources_read, attempted


def _channel_can_be_read(channel: object, me: Optional[discord.Member]) -> bool:
    try:
        if me is None or not isinstance(channel, discord.TextChannel):
            return False
        perms = channel.permissions_for(me)
        return bool(perms.view_channel and perms.read_message_history)
    except Exception:
        return False


def _is_modlog_like(channel: object) -> bool:
    name = _norm(getattr(channel, "name", ""))
    return any(part in name for part in ("mod-log", "modlog", "member-log", "activity-log", "audit", "logs"))



def _message_search_text(message: discord.Message) -> str:
    """Searchable text for mod-log matching.

    Discord mod logs are often embeds with empty message.content, so checking
    only content misses real evidence. This includes embed title, description,
    fields, footer, author, and raw embed dict values.
    """
    parts: list[str] = []
    for attr in ("content", "clean_content"):
        try:
            value = getattr(message, attr, None)
            if value:
                parts.append(str(value))
        except Exception:
            pass

    try:
        for embed in getattr(message, "embeds", []) or []:
            for attr in ("title", "description", "url"):
                value = getattr(embed, attr, None)
                if value:
                    parts.append(str(value))

            try:
                author = getattr(embed, "author", None)
                if author is not None:
                    value = getattr(author, "name", None)
                    if value:
                        parts.append(str(value))
            except Exception:
                pass

            try:
                footer = getattr(embed, "footer", None)
                if footer is not None:
                    value = getattr(footer, "text", None)
                    if value:
                        parts.append(str(value))
            except Exception:
                pass

            try:
                for field in getattr(embed, "fields", []) or []:
                    name = getattr(field, "name", None)
                    value = getattr(field, "value", None)
                    if name:
                        parts.append(str(name))
                    if value:
                        parts.append(str(value))
            except Exception:
                pass

            try:
                raw = embed.to_dict()
                if isinstance(raw, dict):
                    parts.append(str(raw))
            except Exception:
                pass
    except Exception:
        pass

    return _norm(" ".join(parts))


def _looks_like_verification_modlog(text: str) -> bool:
    """True when a mod-log line appears to be about verification/resident role state."""
    raw = str(text or "").lower()
    if not raw:
        return False
    has_verify_word = any(word in raw for word in ("verified", "verify", "verification", "resident"))
    has_role_word = any(word in raw for word in ("role", "grant", "granted", "added", "gave", "assigned"))
    return bool(has_verify_word and has_role_word)


def _signal_is_verification_timestamp(signal: MemberActivitySignal) -> bool:
    source = _signal_source_text(signal)
    return "verification timestamp" in source or "verification fallback" in source



def _member_name_tokens(member: discord.Member) -> set[str]:
    tokens: set[str] = {str(int(member.id)), f"<@{int(member.id)}>", f"<@!{int(member.id)}>"}
    for value in (
        getattr(member, "display_name", None),
        getattr(member, "global_name", None),
        getattr(member, "name", None),
        getattr(member, "nick", None),
    ):
        text = _norm(value)
        if text and len(text) >= 3:
            tokens.add(text)
    return tokens


def _merge_signal(target: dict[int, list[MemberActivitySignal]], uid: int, signal: MemberActivitySignal) -> None:
    if uid <= 0 or signal.timestamp is None:
        return
    existing = target.setdefault(int(uid), [])
    for old in existing:
        if old.source == signal.source and old.timestamp == signal.timestamp:
            return
    existing.append(signal)


def _signal_recent_enough(signal: MemberActivitySignal, *, now: datetime, lookback_days: int) -> bool:
    dt = _safe_dt(signal.timestamp)
    if dt is None:
        return False
    days = _days_since(dt, now)
    return days is not None and days <= int(lookback_days)


async def _load_recent_discord_evidence(
    guild: discord.Guild,
    members: list[discord.Member],
    *,
    verified_resident_role_ids: set[int],
    lookback_days: int = _RECENT_EVIDENCE_LOOKBACK_DAYS,
) -> tuple[dict[int, list[MemberActivitySignal]], list[str], int, int]:
    """Read recent Discord-visible evidence before scoring inactive members.

    Normal readable-channel messages count only when authored by the member.
    Mod-log-like channels can also match a member mention, ID, username,
    display name, or nickname. Mod-log matches are evidence, not presence.
    """
    warnings: list[str] = []
    evidence: dict[int, list[MemberActivitySignal]] = {}
    now = now_utc()
    safe_lookback = max(1, min(int(lookback_days or _RECENT_EVIDENCE_LOOKBACK_DAYS), _RECENT_EVIDENCE_LOOKBACK_DAYS))
    after = now - timedelta(days=safe_lookback)
    me = getattr(guild, "me", None)

    try:
        readable = [ch for ch in getattr(guild, "text_channels", []) or [] if _channel_can_be_read(ch, me)]
    except Exception:
        readable = []

    if not readable:
        return {}, ["Recent Discord evidence sweep could not read any text-channel history. Grant View Channel + Read Message History to improve scan accuracy."], 0, 1

    modlogs = [ch for ch in readable if _is_modlog_like(ch)]
    normal = [ch for ch in readable if ch not in modlogs]
    channels = (modlogs + normal)[:_RECENT_EVIDENCE_MAX_CHANNELS]
    attempted = len(channels)
    read = 0

    member_ids = {int(m.id) for m in members if not getattr(m, "bot", False)}
    review_members = [m for m in members if int(m.id) in member_ids]
    if verified_resident_role_ids:
        focused = [m for m in review_members if _member_role_ids(m).intersection(verified_resident_role_ids)]
        if focused:
            review_members = focused

    token_to_uid: dict[str, int] = {}
    ambiguous_tokens: set[str] = set()
    for member in review_members[:_RECENT_EVIDENCE_MAX_MEMBERS_FOR_NAME_MATCH]:
        for token in _member_name_tokens(member):
            if token in ambiguous_tokens:
                continue
            if token in token_to_uid and token_to_uid[token] != int(member.id):
                token_to_uid.pop(token, None)
                ambiguous_tokens.add(token)
                continue
            token_to_uid[token] = int(member.id)

    for channel in channels:
        is_modlog = _is_modlog_like(channel)
        try:
            async for message in channel.history(limit=_RECENT_EVIDENCE_PER_CHANNEL_LIMIT, after=after, oldest_first=False):
                msg_time = _safe_dt(getattr(message, "created_at", None)) or now
                author_id = _safe_int(getattr(getattr(message, "author", None), "id", 0), 0)

                # Direct authored message is the strongest recent activity proof.
                if author_id in member_ids:
                    _merge_signal(
                        evidence,
                        author_id,
                        MemberActivitySignal(
                            source="recent Discord message history",
                            timestamp=msg_time,
                            confidence="High",
                            note=f"Recent message authored in #{getattr(channel, 'name', 'unknown')}.",
                        ),
                    )

                if not is_modlog:
                    continue

                # Mod-log evidence is a fallback signal. It can catch moderation
                # touches or bot-generated records that stored DB history missed.
                for mention in getattr(message, "mentions", []) or []:
                    uid = _safe_int(getattr(mention, "id", 0), 0)
                    if uid in member_ids:
                        _merge_signal(
                            evidence,
                            uid,
                            MemberActivitySignal(
                                source="recent mod-log mention evidence",
                                timestamp=msg_time,
                                confidence="Medium",
                                note=f"Recent mod-log evidence mentioned this member in #{getattr(channel, 'name', 'unknown')}.",
                            ),
                        )

                content_text = _message_search_text(message)
                if not content_text:
                    continue

                is_verification_modlog = _looks_like_verification_modlog(content_text)

                for token, uid in token_to_uid.items():
                    if token and token in content_text:
                        if is_verification_modlog:
                            _merge_signal(
                                evidence,
                                uid,
                                MemberActivitySignal(
                                    source="recent mod-log verification timestamp",
                                    timestamp=msg_time,
                                    confidence="Medium",
                                    note=f"Recent mod-log verification/resident-role evidence matched this member in #{getattr(channel, 'name', 'unknown')}.",
                                ),
                            )
                        else:
                            _merge_signal(
                                evidence,
                                uid,
                                MemberActivitySignal(
                                    source="recent mod-log text evidence",
                                    timestamp=msg_time,
                                    confidence="Medium",
                                    note=f"Recent mod-log evidence matched this member in #{getattr(channel, 'name', 'unknown')}.",
                                ),
                            )
        except discord.Forbidden:
            warnings.append(f"Could not read recent history in #{getattr(channel, 'name', 'unknown')}; missing permission.")
            continue
        except Exception:
            continue

        read += 1

    if read:
        moved_count = sum(
            1
            for signals in evidence.values()
            if any(_signal_recent_enough(s, now=now, lookback_days=safe_lookback) for s in signals)
        )
        warnings.append(
            f"Recent Discord evidence sweep checked {read}/{attempted} readable channel(s) for the last {safe_lookback} day(s); found recent evidence for {moved_count} member(s)."
        )

    return evidence, warnings, read, attempted


async def _load_audit_role_grant_times(
    guild: discord.Guild,
    role_ids: set[int],
    *,
    enabled: bool = True,
    limit: int = 1000,
) -> tuple[dict[int, datetime], str]:
    if not enabled or not role_ids:
        return {}, ""
    try:
        me = guild.me
        if me is not None and not me.guild_permissions.view_audit_log:
            return {}, "Audit-log fallback was skipped because the bot is missing View Audit Log."
    except Exception:
        pass

    found: dict[int, datetime] = {}
    try:
        async for entry in guild.audit_logs(action=discord.AuditLogAction.member_role_update, limit=limit):
            target = getattr(entry, "target", None)
            uid = _safe_int(getattr(target, "id", 0), 0)
            if uid <= 0 or uid in found:
                continue

            before_ids: set[int] = set()
            after_ids: set[int] = set()
            changes = getattr(entry, "changes", None)
            try:
                before_ids = _role_collection_ids(getattr(getattr(changes, "before", None), "roles", None))
                after_ids = _role_collection_ids(getattr(getattr(changes, "after", None), "roles", None))
            except Exception:
                pass

            if after_ids.intersection(role_ids) and not before_ids.intersection(role_ids):
                dt = _safe_dt(getattr(entry, "created_at", None))
                if dt is not None:
                    found[uid] = dt
        return found, ""
    except Exception:
        return found, "Audit-log fallback could not be read. Use View Audit Log permission or rely on Dank Shield verification records."


def _candidate_for_member(
    member: discord.Member,
    *,
    joined_at: Optional[datetime],
    last_seen: Optional[datetime],
    inactivity_days: Optional[int],
    score: int,
    confidence: str,
    status: str,
    removable: bool,
    protected: bool,
    cannot_remove: bool,
    reasons: list[str],
    signals: list[MemberActivitySignal],
    verified_or_resident: bool,
    verified_at: Optional[datetime],
    verification_source: str,
    post_verification_activity_at: Optional[datetime],
    days_since_verification: Optional[int],
) -> InactiveMemberCandidate:
    return InactiveMemberCandidate(
        user_id=int(member.id),
        display_name=str(getattr(member, "display_name", None) or getattr(member, "name", None) or member.id),
        mention=f"<@{int(member.id)}>",
        joined_at=joined_at,
        last_seen_at=last_seen,
        inactivity_days=inactivity_days,
        activity_score=score,
        confidence=confidence,
        status=status,
        removable=bool(removable),
        protected=bool(protected),
        cannot_remove=bool(cannot_remove),
        reasons=reasons,
        signals=signals,
        verified_or_resident=bool(verified_or_resident),
        verified_at=verified_at,
        verification_source=verification_source,
        post_verification_activity_at=post_verification_activity_at,
        days_since_verification=days_since_verification,
    )


def _confidence_allowed(confidence: str, options: InactiveScanOptions) -> bool:
    c = str(confidence or "").lower()
    if c == "high":
        return bool(options.include_high_confidence)
    if c == "medium":
        return bool(options.include_medium_confidence)
    if c == "low":
        return bool(options.include_low_confidence)
    return True


async def scan_inactive_members(guild: discord.Guild, options: Optional[InactiveScanOptions] = None) -> InactiveScanReport:
    options = options or InactiveScanOptions()
    now = now_utc()
    protected_role_ids, verified_resident_role_ids = await _load_role_sets(guild)
    members = list(getattr(guild, "members", []) or [])

    locked_user_ids: set[int] = set()
    lock_persistence = "disabled"
    data_warnings: list[str] = []
    if options.skip_locked_users:
        locked_user_ids, lock_persistence = await get_scan_locked_user_ids(int(guild.id))
        if "memory-only" in lock_persistence:
            data_warnings.append(lock_persistence)

    activity_signals, verification_times, activity_warnings, sources_read, sources_attempted = await _load_known_activity_signals(int(guild.id))
    data_warnings.extend(activity_warnings)

    recent_signals, recent_warnings, recent_read, recent_attempted = await _load_recent_discord_evidence(
        guild,
        members,
        verified_resident_role_ids=verified_resident_role_ids,
        lookback_days=min(_RECENT_EVIDENCE_LOOKBACK_DAYS, max(14, int(options.inactive_days))),
    )
    data_warnings.extend(recent_warnings)
    for uid, signals in recent_signals.items():
        activity_signals.setdefault(int(uid), []).extend(signals)

    # Treat the bounded Discord evidence sweep as one additional source group.
    if recent_attempted > 0:
        sources_attempted += 1
        if recent_read > 0:
            sources_read += 1

    audit_times, audit_warning = await _load_audit_role_grant_times(
        guild,
        verified_resident_role_ids,
        enabled=options.use_audit_log_fallback,
    )
    if audit_warning:
        data_warnings.append(audit_warning)

    db_available = bool(activity_signals)

    candidates: list[InactiveMemberCandidate] = []
    protected: list[InactiveMemberCandidate] = []
    cannot_remove: list[InactiveMemberCandidate] = []
    active_enough_count = 0
    unknown_activity_count = 0
    verified_resident_seen = 0
    verified_resident_without_post_activity = 0
    locked_users_skipped = 0
    inactive_hidden_by_filter_count = 0

    for member in members:
        try:
            uid = int(member.id)
            if options.skip_locked_users and uid in locked_user_ids:
                locked_users_skipped += 1
                continue

            role_ids = _member_role_ids(member)
            is_verified_resident = bool(role_ids.intersection(verified_resident_role_ids)) if verified_resident_role_ids else False
            if options.verified_resident_focus and verified_resident_role_ids and not is_verified_resident:
                continue

            joined_at = _safe_dt(getattr(member, "joined_at", None))
            signals = list(activity_signals.get(uid, []))
            reasons: list[str] = []
            is_protected = False
            cannot = False

            if is_verified_resident:
                verified_resident_seen += 1

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

            bot_ok, bot_reason = _bot_can_action(member)
            if not bot_ok:
                cannot = True
                reasons.append(bot_reason)

            verified_at: Optional[datetime] = None
            verification_source = "unknown"
            if uid in verification_times:
                verified_at, verification_source = verification_times[uid]
                verification_source = f"DB:{verification_source}"
            elif uid in audit_times:
                verified_at = audit_times[uid]
                verification_source = "Discord audit log role-added fallback"
            else:
                modlog_verify_times = [
                    s.timestamp
                    for s in signals
                    if s.timestamp is not None and _signal_is_verification_timestamp(s)
                ]
                if modlog_verify_times:
                    verified_at = max(modlog_verify_times)
                    verification_source = "Discord mod-log role/verification fallback"
                elif is_verified_resident:
                    verified_at = joined_at
                    verification_source = "join-date fallback; exact verification date unknown"

            post_verify_activity = _best_post_verification_activity(signals, verified_at)
            last_seen = _best_last_seen(signals, joined_at)
            days_since_verification: Optional[int] = None

            if is_verified_resident and verified_at is not None:
                days_since_verification = _days_since(verified_at, now)
                if post_verify_activity is None:
                    verified_resident_without_post_activity += 1
                    last_seen_for_threshold = verified_at
                    if str(verification_source).startswith("join-date fallback"):
                        reasons.append(
                            "Exact verification date is unknown, so Dank Shield is using join-date fallback plus the recent evidence sweep. This user is reviewable/manual-only unless confidence becomes Medium/High from stronger evidence."
                        )
                    else:
                        reasons.append(
                            f"Verified/resident member with no tracked server activity after verification. Verification source: {verification_source}."
                        )
                else:
                    last_seen_for_threshold = post_verify_activity
                    recent_source = (
                        "recent Discord evidence"
                        if any(str(s.source).startswith("recent Discord") or "mod-log" in str(s.source) for s in signals)
                        else "stored history"
                    )
                    reasons.append(f"Verified/resident member has tracked server activity after verification from {recent_source}.")
            else:
                last_seen_for_threshold = last_seen

            inactivity_days = _days_since(last_seen_for_threshold, now)
            confidence = _calibrated_candidate_confidence(
                signals=signals,
                verified_at=verified_at,
                verification_source=verification_source,
                post_verification_activity_at=post_verify_activity,
                sources_read=sources_read,
                sources_attempted=sources_attempted,
                recent_read=recent_read,
            )

            score = _activity_score(inactivity_days, confidence)

            if not signals:
                unknown_activity_count += 1
                reasons.append("No message/ticket/activity history was found for this member.")

            # Core false-positive fix:
            # recent message/mod-log evidence becomes a signal and therefore
            # updates post_verify_activity / last_seen_for_threshold above.
            # Recent activity means the member is counted as active, not shown
            # as a purge/review candidate.
            if inactivity_days is not None and inactivity_days < int(options.inactive_days):
                active_enough_count += 1
                continue

            if inactivity_days is None:
                status = "Needs review"
                reasons.append("Dank Shield could not prove a reliable quiet-days value for this member.")
            elif confidence.lower() == "low":
                status = "Needs review"
                reasons.append("Low confidence: weak or incomplete evidence. Shown for manual review, but not purge-safe.")
            else:
                status = "Review candidate"
                reasons.append(f"Quiet for {inactivity_days} day(s), meeting the {options.inactive_days}-day threshold.")

            candidate = _candidate_for_member(
                member,
                joined_at=joined_at,
                last_seen=last_seen,
                inactivity_days=inactivity_days,
                score=score,
                confidence=confidence,
                status="Cannot action" if cannot else "Protected" if is_protected else status,
                removable=not cannot and not is_protected and confidence.lower() in {"medium", "high"} and status == "Review candidate",
                protected=is_protected,
                cannot_remove=cannot,
                reasons=reasons,
                signals=signals,
                verified_or_resident=is_verified_resident,
                verified_at=verified_at,
                verification_source=verification_source,
                post_verification_activity_at=post_verify_activity,
                days_since_verification=days_since_verification,
            )

            if cannot:
                cannot_remove.append(candidate)
            elif is_protected:
                protected.append(candidate)
            elif not _confidence_allowed(confidence, options):
                inactive_hidden_by_filter_count += 1
            else:
                candidates.append(candidate)
        except Exception:
            continue

    candidates.sort(
        key=lambda c: (
            c.inactivity_days if c.inactivity_days is not None else 999999,
            _confidence_rank(c.confidence),
        ),
        reverse=True,
    )
    candidates = candidates[: max(1, int(options.max_candidates))]

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
        verified_resident_seen=verified_resident_seen,
        verified_resident_without_post_activity=verified_resident_without_post_activity,
        audit_log_times_found=len(audit_times),
        locked_users_skipped=locked_users_skipped,
        scan_lock_persistence=lock_persistence,
    )
    remember_scan(report)
    return report


__all__ = [
    "InactiveMemberCandidate",
    "InactiveScanOptions",
    "InactiveScanReport",
    "MemberActivitySignal",
    "ScanLockRecord",
    "get_last_scan",
    "get_scan_lock_records",
    "get_scan_locked_user_ids",
    "is_scan_user_locked",
    "remember_scan",
    "scan_inactive_members",
    "set_scan_user_lock",
]
