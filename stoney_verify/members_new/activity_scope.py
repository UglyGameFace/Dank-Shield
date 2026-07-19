from __future__ import annotations

"""Shared activity-tracking permission coverage inspection.

This module is intentionally read-only. It tells tracking, inactivity review, and
setup diagnostics what Dank Shield can actually inspect; it never grants itself
permissions or mutates channel overwrites.
"""

from dataclasses import dataclass
from typing import Any

import discord


@dataclass(frozen=True)
class ActivityScopeProblem:
    channel_id: int
    channel_name: str
    channel_kind: str
    missing_permissions: tuple[str, ...]

    @property
    def display_name(self) -> str:
        return f"#{self.channel_name}" if self.channel_name else f"channel {self.channel_id}"

    @property
    def message(self) -> str:
        missing = ", ".join(self.missing_permissions) or "Unknown permission"
        return f"{self.display_name} (`{self.channel_id}`): missing {missing}"


@dataclass(frozen=True)
class ActivityScopeReport:
    total_channels: int
    accessible_channels: int
    problems: tuple[ActivityScopeProblem, ...]
    bot_member_resolved: bool = True

    @property
    def inaccessible_channels(self) -> int:
        return max(0, self.total_channels - self.accessible_channels)

    @property
    def coverage_percent(self) -> int:
        if self.total_channels <= 0:
            return 100 if self.bot_member_resolved else 0
        return max(0, min(100, int(round((self.accessible_channels / self.total_channels) * 100))))

    @property
    def complete(self) -> bool:
        return self.bot_member_resolved and not self.problems

    def summary(self, *, limit: int = 8) -> str:
        if not self.bot_member_resolved:
            return "Activity coverage is incomplete because Dank Shield could not resolve its bot member in this server."
        if not self.problems:
            return f"Activity coverage is complete: {self.accessible_channels}/{self.total_channels} inspectable channels (100%)."
        lines = [
            f"Activity coverage is incomplete: {self.accessible_channels}/{self.total_channels} inspectable channels ({self.coverage_percent}%)."
        ]
        for problem in self.problems[: max(1, int(limit))]:
            lines.append(problem.message)
        remaining = len(self.problems) - min(len(self.problems), max(1, int(limit)))
        if remaining > 0:
            lines.append(f"…and {remaining} more inaccessible channel(s).")
        return " ".join(lines)


def _safe_channel_id(channel: Any) -> int:
    try:
        return int(getattr(channel, "id", 0) or 0)
    except Exception:
        return 0


def _safe_channel_name(channel: Any) -> str:
    try:
        return str(getattr(channel, "name", "") or "").strip()
    except Exception:
        return ""


def _channel_kind(channel: Any) -> str:
    if isinstance(channel, discord.Thread):
        return "thread"
    if isinstance(channel, discord.ForumChannel):
        return "forum"
    if isinstance(channel, discord.TextChannel):
        return "text"
    return type(channel).__name__.lower()


def _base_message_channels(guild: discord.Guild) -> list[Any]:
    out: list[Any] = []
    for channel in list(getattr(guild, "channels", []) or []):
        if isinstance(channel, (discord.TextChannel, discord.ForumChannel)):
            out.append(channel)
    return out


def _active_threads(guild: discord.Guild) -> list[discord.Thread]:
    return [thread for thread in list(getattr(guild, "threads", []) or []) if isinstance(thread, discord.Thread)]


def _permissions_for(channel: Any, me: discord.Member) -> Any:
    try:
        return channel.permissions_for(me)
    except Exception:
        return None


def _missing_for_channel(channel: Any, me: discord.Member) -> tuple[str, ...]:
    perms = _permissions_for(channel, me)
    if perms is None:
        return ("View Channel", "Read Message History")

    missing: list[str] = []
    if not bool(getattr(perms, "view_channel", False)):
        missing.append("View Channel")
    if not bool(getattr(perms, "read_message_history", False)):
        missing.append("Read Message History")
    return tuple(missing)


def _private_threads_may_exist(channel: discord.TextChannel, guild: discord.Guild) -> bool:
    """Mirror the reconciliation safety rule for potentially hidden private threads."""

    for role in list(getattr(guild, "roles", []) or []):
        try:
            if bool(getattr(role, "managed", False)):
                continue
            permissions = channel.permissions_for(role)
            if (
                bool(getattr(permissions, "view_channel", False))
                and bool(getattr(permissions, "create_private_threads", False))
                and bool(getattr(permissions, "send_messages_in_threads", False))
            ):
                return True
        except Exception:
            continue
    return False


def _manage_threads_problem(channel: Any, me: discord.Member) -> ActivityScopeProblem | None:
    perms = _permissions_for(channel, me)
    if perms is None or bool(getattr(perms, "manage_threads", False)):
        return None
    return ActivityScopeProblem(
        channel_id=_safe_channel_id(channel),
        channel_name=_safe_channel_name(channel),
        channel_kind=_channel_kind(channel),
        missing_permissions=("Manage Threads",),
    )


def _private_thread_parent_problem(thread: discord.Thread, me: discord.Member) -> ActivityScopeProblem | None:
    try:
        if not bool(thread.is_private()):
            return None
    except Exception:
        return None

    parent = getattr(thread, "parent", None)
    if parent is None:
        return None
    return _manage_threads_problem(parent, me)


def audit_activity_scope(guild: discord.Guild) -> ActivityScopeReport:
    """Return deterministic, read-only channel coverage for authoritative activity tracking."""

    me = getattr(guild, "me", None)
    if not isinstance(me, discord.Member):
        return ActivityScopeReport(total_channels=0, accessible_channels=0, problems=tuple(), bot_member_resolved=False)

    channels: list[Any] = [*_base_message_channels(guild), *_active_threads(guild)]
    channels.sort(key=lambda channel: (_safe_channel_id(channel), _safe_channel_name(channel)))

    problems_by_key: dict[tuple[int, tuple[str, ...]], ActivityScopeProblem] = {}
    inaccessible_ids: set[int] = set()

    for channel in channels:
        cid = _safe_channel_id(channel)
        missing = _missing_for_channel(channel, me)
        if missing:
            problem = ActivityScopeProblem(
                channel_id=cid,
                channel_name=_safe_channel_name(channel),
                channel_kind=_channel_kind(channel),
                missing_permissions=missing,
            )
            problems_by_key[(cid, missing)] = problem
            inaccessible_ids.add(cid)

        if isinstance(channel, discord.TextChannel) and _private_threads_may_exist(channel, guild):
            parent_problem = _manage_threads_problem(channel, me)
            if parent_problem is not None:
                key = (parent_problem.channel_id, parent_problem.missing_permissions)
                problems_by_key[key] = parent_problem
                inaccessible_ids.add(cid)

        if isinstance(channel, discord.Thread):
            parent_problem = _private_thread_parent_problem(channel, me)
            if parent_problem is not None:
                key = (parent_problem.channel_id, parent_problem.missing_permissions)
                problems_by_key[key] = parent_problem
                inaccessible_ids.add(parent_problem.channel_id)
                inaccessible_ids.add(cid)

    problems = tuple(
        sorted(
            problems_by_key.values(),
            key=lambda problem: (problem.channel_id, problem.missing_permissions, problem.channel_name),
        )
    )
    total = len(channels)
    channel_ids = {_safe_channel_id(channel) for channel in channels}
    inaccessible_in_scope = {cid for cid in inaccessible_ids if cid > 0 and cid in channel_ids}
    accessible = max(0, total - len(inaccessible_in_scope))
    return ActivityScopeReport(
        total_channels=total,
        accessible_channels=accessible,
        problems=problems,
        bot_member_resolved=True,
    )


def format_activity_scope_problems(report: ActivityScopeReport, *, limit: int = 12) -> list[str]:
    if not report.bot_member_resolved:
        return ["Dank Shield bot member could not be resolved for permission checks."]
    rows = [problem.message for problem in report.problems[: max(1, int(limit))]]
    remaining = len(report.problems) - len(rows)
    if remaining > 0:
        rows.append(f"…and {remaining} more inaccessible channel(s).")
    return rows


__all__ = [
    "ActivityScopeProblem",
    "ActivityScopeReport",
    "audit_activity_scope",
    "format_activity_scope_problems",
]
