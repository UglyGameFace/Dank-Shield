from __future__ import annotations

"""Fresh recent-activity evidence for member cleanup scans.

This service reduces false positives in `/dank members scan` and cleanup flows by
checking evidence that may not have made it into the stored Supabase activity
history yet.

It intentionally does not use Discord presence. Users can appear offline, so
presence is not treated as activity.
"""

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from typing import Iterable, Optional

import discord

from stoney_verify.members_new.activity_service import (
    InactiveMemberCandidate,
    InactiveScanReport,
    MemberActivitySignal,
    remember_scan,
)


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _safe_dt(value: object) -> Optional[datetime]:
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


def _days_since(dt: Optional[datetime], *, now: Optional[datetime] = None) -> Optional[int]:
    if dt is None:
        return None
    current = now or _now_utc()
    try:
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        return max(0, int((current - dt).total_seconds() // 86400))
    except Exception:
        return None


def _norm(text: object) -> str:
    return str(text or "").strip().lower()


def _candidate_tokens(candidate: InactiveMemberCandidate, member: Optional[discord.Member]) -> set[str]:
    tokens: set[str] = {str(int(candidate.user_id)), f"<@{int(candidate.user_id)}>", f"<@!{int(candidate.user_id)}>"}
    for value in (
        getattr(candidate, "display_name", None),
        getattr(member, "display_name", None),
        getattr(member, "global_name", None),
        getattr(member, "name", None),
        getattr(member, "nick", None),
    ):
        text = _norm(value)
        if text and len(text) >= 3:
            tokens.add(text)
    return tokens


def _message_matches_candidate(message: discord.Message, tokens: set[str], user_id: int) -> bool:
    try:
        if int(getattr(getattr(message, "author", None), "id", 0)) == int(user_id):
            return True
    except Exception:
        pass
    try:
        for mention in getattr(message, "mentions", []) or []:
            if int(getattr(mention, "id", 0)) == int(user_id):
                return True
    except Exception:
        pass
    content = _norm(getattr(message, "content", ""))
    if not content:
        return False
    for token in tokens:
        if token and token in content:
            return True
    return False


def _is_modlog_like(channel: discord.abc.GuildChannel) -> bool:
    name = _norm(getattr(channel, "name", ""))
    return any(part in name for part in ("mod-log", "modlog", "logs", "audit", "activity", "member-log"))


def _channel_can_be_read(channel: object, me: Optional[discord.Member]) -> bool:
    try:
        if me is None or not isinstance(channel, discord.TextChannel):
            return False
        perms = channel.permissions_for(me)
        return bool(perms.view_channel and perms.read_message_history)
    except Exception:
        return False


async def collect_recent_activity_evidence(
    guild: discord.Guild,
    candidates: Iterable[InactiveMemberCandidate],
    *,
    lookback_days: int = 14,
    per_channel_limit: int = 150,
    max_channels: int = 25,
) -> tuple[dict[int, MemberActivitySignal], list[str]]:
    """Return recent evidence signals by user id.

    Normal channel messages authored by a user count as high-confidence server
    activity. Mod-log text/mention/name matches count as medium-confidence
    evidence because they may be moderation noise rather than direct activity.
    """
    warnings: list[str] = []
    now = _now_utc()
    after = now - timedelta(days=max(1, min(int(lookback_days or 14), 60)))
    me = getattr(guild, "me", None)

    candidate_list = list(candidates or [])
    if not candidate_list:
        return {}, warnings

    members_by_id: dict[int, Optional[discord.Member]] = {}
    token_map: dict[int, set[str]] = {}
    for candidate in candidate_list:
        try:
            uid = int(candidate.user_id)
        except Exception:
            continue
        member = guild.get_member(uid)
        members_by_id[uid] = member
        token_map[uid] = _candidate_tokens(candidate, member)

    evidence: dict[int, MemberActivitySignal] = {}

    channels: list[discord.TextChannel] = []
    try:
        readable = [ch for ch in getattr(guild, "text_channels", []) or [] if _channel_can_be_read(ch, me)]
        modlogs = [ch for ch in readable if _is_modlog_like(ch)]
        normal = [ch for ch in readable if ch not in modlogs]
        channels = (modlogs + normal)[: max(1, min(int(max_channels or 25), 75))]
    except Exception:
        channels = []

    if not channels:
        return {}, ["Recent evidence sweep could not read any text-channel history. Grant View Channel + Read Message History to improve scan accuracy."]

    for channel in channels:
        is_modlog = _is_modlog_like(channel)
        try:
            async for message in channel.history(limit=max(10, min(int(per_channel_limit or 150), 500)), after=after, oldest_first=False):
                msg_time = _safe_dt(getattr(message, "created_at", None)) or now
                for uid, tokens in token_map.items():
                    if uid in evidence and evidence[uid].timestamp and evidence[uid].timestamp >= msg_time:
                        continue
                    if not _message_matches_candidate(message, tokens, uid):
                        continue
                    if is_modlog:
                        evidence[uid] = MemberActivitySignal(
                            source="recent mod-log evidence",
                            timestamp=msg_time,
                            confidence="Medium",
                            note=f"Recent mod-log/channel evidence matched this member in #{getattr(channel, 'name', 'unknown')}.",
                        )
                    else:
                        evidence[uid] = MemberActivitySignal(
                            source="recent message history",
                            timestamp=msg_time,
                            confidence="High",
                            note=f"Recent readable channel history found activity in #{getattr(channel, 'name', 'unknown')}.",
                        )
        except discord.Forbidden:
            warnings.append(f"Could not read recent history in #{getattr(channel, 'name', 'unknown')}; missing permission.")
        except Exception:
            continue

    return evidence, warnings


def _candidate_with_recent_evidence(
    candidate: InactiveMemberCandidate,
    signal: MemberActivitySignal,
    *,
    now: Optional[datetime] = None,
) -> InactiveMemberCandidate:
    current = now or _now_utc()
    signal_dt = _safe_dt(signal.timestamp)
    new_signals = list(getattr(candidate, "signals", []) or []) + [signal]
    inactivity_days = _days_since(signal_dt, now=current)
    reasons = list(getattr(candidate, "reasons", []) or [])
    reasons.insert(0, "Recent server evidence was found, so this member is not treated as long-inactive.")
    return replace(
        candidate,
        last_seen_at=signal_dt or candidate.last_seen_at,
        inactivity_days=inactivity_days,
        activity_score=max(int(getattr(candidate, "activity_score", 0) or 0), 75),
        confidence="High" if str(signal.confidence).lower() == "high" else candidate.confidence,
        status="Active/recent evidence",
        removable=False,
        reasons=reasons,
        signals=new_signals,
        post_verification_activity_at=signal_dt or candidate.post_verification_activity_at,
    )


async def apply_recent_activity_evidence(
    guild: discord.Guild,
    report: InactiveScanReport,
    *,
    lookback_days: Optional[int] = None,
    remember: bool = True,
) -> InactiveScanReport:
    """Filter recent-evidence false positives out of an inactive scan report."""
    if report is None:
        return report
    options = getattr(report, "options", None)
    inactive_days = int(getattr(options, "inactive_days", 90) or 90)
    sweep_days = max(7, min(int(lookback_days or max(14, min(inactive_days, 60))), 60))

    all_reviewed = list(report.candidates or []) + list(report.protected or []) + list(report.cannot_remove or [])
    evidence, warnings = await collect_recent_activity_evidence(guild, all_reviewed, lookback_days=sweep_days)
    if not evidence and not warnings:
        return report

    now = _now_utc()
    active_moved = 0

    def _filter(items: list[InactiveMemberCandidate]) -> list[InactiveMemberCandidate]:
        nonlocal active_moved
        kept: list[InactiveMemberCandidate] = []
        for candidate in items or []:
            signal = evidence.get(int(candidate.user_id))
            if signal is None:
                kept.append(candidate)
                continue
            days = _days_since(signal.timestamp, now=now)
            if days is not None and days < inactive_days:
                active_moved += 1
                continue
            kept.append(candidate)
        return kept

    new_warnings = list(report.data_warnings or [])
    for warning in warnings:
        if warning not in new_warnings:
            new_warnings.append(warning)
    if evidence:
        new_warnings.append(f"Recent evidence sweep checked readable channels/mod-log for the last {sweep_days} day(s) and removed {active_moved} recent false-positive candidate(s).")

    updated = replace(
        report,
        candidates=_filter(list(report.candidates or [])),
        protected=_filter(list(report.protected or [])),
        cannot_remove=_filter(list(report.cannot_remove or [])),
        data_warnings=new_warnings,
        active_enough_count=int(report.active_enough_count or 0) + active_moved,
        data_sources_read=int(report.data_sources_read or 0) + 1,
        data_sources_attempted=int(report.data_sources_attempted or 0) + 1,
    )
    if remember:
        remember_scan(updated)
    return updated


__all__ = [
    "apply_recent_activity_evidence",
    "collect_recent_activity_evidence",
]
