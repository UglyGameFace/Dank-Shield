from __future__ import annotations

"""Memory-bounded, fail-closed reconciliation of short restart gaps.

Only retained Discord messages are reconstructed during downtime. Every
Discord iterator has a strict limit plus one extra item for overflow
detection. Any overflow, missing permission, timeout, or API failure rejects
continuity and causes the tracker to begin a new proof window.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
import asyncio
import os
from typing import Any, AsyncIterator

import discord


@dataclass
class RestartReconciliationResult:
    scanned_channels: int
    scanned_messages: int
    replayed_members: int
    latest_by_user: dict[int, tuple[datetime, int]]


def _env_int(
    name: str,
    default: int,
    *,
    minimum: int,
    maximum: int,
) -> int:
    try:
        value = int(str(os.getenv(name, default)).strip())
    except Exception:
        value = int(default)

    return max(int(minimum), min(int(maximum), value))


def max_reconcile_gap_seconds() -> int:
    return _env_int(
        "DANK_ACTIVITY_RECONCILE_MAX_GAP_SECONDS",
        3600,
        minimum=180,
        maximum=86400,
    )


def reconcile_start_delay_seconds() -> int:
    return _env_int(
        "DANK_ACTIVITY_RECONCILE_START_DELAY_SECONDS",
        20,
        minimum=5,
        maximum=300,
    )


def reconcile_timeout_seconds() -> int:
    return _env_int(
        "DANK_ACTIVITY_RECONCILE_TIMEOUT_SECONDS",
        90,
        minimum=30,
        maximum=600,
    )


def _max_reconcile_channels() -> int:
    return _env_int(
        "DANK_ACTIVITY_RECONCILE_MAX_CHANNELS",
        100,
        minimum=10,
        maximum=250,
    )


def _max_threads_per_parent() -> int:
    return _env_int(
        "DANK_ACTIVITY_RECONCILE_MAX_THREADS_PER_PARENT",
        25,
        minimum=1,
        maximum=100,
    )


def _max_messages_per_channel() -> int:
    return _env_int(
        "DANK_ACTIVITY_RECONCILE_MAX_MESSAGES_PER_CHANNEL",
        250,
        minimum=25,
        maximum=2000,
    )


def _max_reconcile_messages() -> int:
    return _env_int(
        "DANK_ACTIVITY_RECONCILE_MAX_MESSAGES",
        5000,
        minimum=100,
        maximum=25000,
    )


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)

    return value.astimezone(timezone.utc)


def _permissions_for(
    channel: Any,
    member: discord.Member,
) -> Any:
    resolver = getattr(channel, "permissions_for", None)

    if not callable(resolver):
        return None

    try:
        return resolver(member)
    except Exception:
        return None


def _can_read_history(
    channel: Any,
    member: discord.Member,
) -> bool:
    permissions = _permissions_for(channel, member)

    if permissions is None:
        return False

    return bool(
        getattr(permissions, "view_channel", False)
        and getattr(
            permissions,
            "read_message_history",
            False,
        )
    )


def _private_threads_may_exist(
    channel: discord.TextChannel,
    guild: discord.Guild,
) -> bool:
    for role in list(getattr(guild, "roles", []) or []):
        try:
            if bool(getattr(role, "managed", False)):
                continue

            permissions = channel.permissions_for(role)

            if (
                bool(
                    getattr(
                        permissions,
                        "view_channel",
                        False,
                    )
                )
                and bool(
                    getattr(
                        permissions,
                        "create_private_threads",
                        False,
                    )
                )
                and bool(
                    getattr(
                        permissions,
                        "send_messages_in_threads",
                        False,
                    )
                )
            ):
                return True
        except Exception:
            continue

    return False


def audit_guild_activity_scope(
    guild: discord.Guild,
) -> str:
    """Return a bounded, actionable explanation when durable coverage is incomplete."""
    member = getattr(guild, "me", None)

    if not isinstance(member, discord.Member):
        return (
            "Could not resolve Dank Shield's guild member permissions. "
            "Inactivity cleanup stays review-only until permissions can be verified."
        )

    issues: list[str] = []

    for channel in list(getattr(guild, "channels", []) or []):
        has_history = callable(getattr(channel, "history", None))
        is_thread_parent = isinstance(channel, (discord.TextChannel, discord.ForumChannel))

        if (has_history or is_thread_parent) and not _can_read_history(channel, member):
            issues.append(
                f"#{getattr(channel, 'name', channel.id)} ({int(channel.id)}): "
                "grant View Channel + Read Message History"
            )

        if isinstance(channel, discord.TextChannel):
            permissions = _permissions_for(channel, member)
            manage_threads = bool(getattr(permissions, "manage_threads", False))
            if not manage_threads and _private_threads_may_exist(channel, guild):
                issues.append(
                    f"#{channel.name} ({int(channel.id)}): grant Manage Threads "
                    "or disable private-thread creation"
                )

    for thread in list(getattr(guild, "threads", []) or []):
        if not _can_read_history(thread, member):
            issues.append(
                f"thread {getattr(thread, 'name', thread.id)} ({int(thread.id)}): "
                "grant View Channel + Read Message History"
            )

    if not issues:
        return ""

    # Deduplicate while preserving Discord's visible order, then bound startup
    # and report text so one large server cannot flood logs/interactions.
    unique = list(dict.fromkeys(issues))
    shown = unique[:5]
    extra = len(unique) - len(shown)
    detail = "; ".join(shown)
    if extra > 0:
        detail += f"; +{extra} more channel/thread permission issue(s)"

    return (
        "Activity coverage is incomplete, so inactivity cleanup stays review-only. "
        "Fix Dank Shield's channel permissions: " + detail + "."
    )


def _thread_is_relevant(
    thread: discord.Thread,
    after: datetime,
) -> bool:
    timestamp = getattr(
        thread,
        "archive_timestamp",
        None,
    )

    if not isinstance(timestamp, datetime):
        return True

    return _aware(timestamp) > _aware(after)


async def _bounded_async_items(
    iterator: Any,
    *,
    limit: int,
    label: str,
) -> AsyncIterator[Any]:
    """Yield no more than limit objects and raise on the extra item."""
    seen = 0

    async for item in iterator:
        seen += 1

        if seen > int(limit):
            raise RuntimeError(
                f"{label} exceeded the safe limit of "
                f"{int(limit)}."
            )

        yield item


def _add_messageable(
    found: dict[int, Any],
    channel: Any,
    *,
    max_channels: int,
) -> None:
    channel_id = int(
        getattr(channel, "id", 0) or 0
    )

    if channel_id <= 0:
        raise RuntimeError(
            "Discord returned a messageable without an ID."
        )

    found[channel_id] = channel

    if len(found) > int(max_channels):
        raise RuntimeError(
            "Restart reconciliation discovered more than "
            f"{int(max_channels)} readable channels/threads."
        )


async def _collect_messageables(
    guild: discord.Guild,
    *,
    after: datetime,
) -> list[Any]:
    member = getattr(guild, "me", None)

    if not isinstance(member, discord.Member):
        raise RuntimeError(
            "Could not resolve Dank Shield's guild member "
            "permissions."
        )

    scope_error = audit_guild_activity_scope(guild)

    if scope_error:
        raise RuntimeError(scope_error)

    found: dict[int, Any] = {}
    channel_limit = _max_reconcile_channels()
    thread_limit = _max_threads_per_parent()

    for channel in list(
        getattr(guild, "channels", []) or []
    ):
        if callable(getattr(channel, "history", None)):
            _add_messageable(
                found,
                channel,
                max_channels=channel_limit,
            )

    for thread in list(
        getattr(guild, "threads", []) or []
    ):
        _add_messageable(
            found,
            thread,
            max_channels=channel_limit,
        )

    parents = [
        channel
        for channel in list(
            getattr(guild, "channels", []) or []
        )
        if isinstance(
            channel,
            (
                discord.TextChannel,
                discord.ForumChannel,
            ),
        )
    ]

    # Deliberately sequential: never create one task per channel or parent.
    for parent in parents:
        try:
            if isinstance(parent, discord.TextChannel):
                public_threads = parent.archived_threads(
                    limit=thread_limit + 1,
                    private=False,
                )

                async for thread in _bounded_async_items(
                    public_threads,
                    limit=thread_limit,
                    label=(
                        "Archived public threads in "
                        f"#{parent.name}"
                    ),
                ):
                    if not _thread_is_relevant(
                        thread,
                        after,
                    ):
                        break

                    _add_messageable(
                        found,
                        thread,
                        max_channels=channel_limit,
                    )

                permissions = parent.permissions_for(
                    member
                )

                if bool(
                    getattr(
                        permissions,
                        "manage_threads",
                        False,
                    )
                ):
                    private_threads = (
                        parent.archived_threads(
                            limit=thread_limit + 1,
                            private=True,
                            joined=False,
                        )
                    )

                    async for thread in _bounded_async_items(
                        private_threads,
                        limit=thread_limit,
                        label=(
                            "Archived private threads in "
                            f"#{parent.name}"
                        ),
                    ):
                        if not _thread_is_relevant(
                            thread,
                            after,
                        ):
                            break

                        _add_messageable(
                            found,
                            thread,
                            max_channels=channel_limit,
                        )

            elif isinstance(parent, discord.ForumChannel):
                forum_threads = parent.archived_threads(
                    limit=thread_limit + 1,
                )

                async for thread in _bounded_async_items(
                    forum_threads,
                    limit=thread_limit,
                    label=(
                        "Archived forum threads in "
                        f"#{parent.name}"
                    ),
                ):
                    if not _thread_is_relevant(
                        thread,
                        after,
                    ):
                        break

                    _add_messageable(
                        found,
                        thread,
                        max_channels=channel_limit,
                    )

        except Exception as exc:
            raise RuntimeError(
                "Could not safely enumerate archived threads "
                f"for {getattr(parent, 'name', parent.id)}: "
                f"{type(exc).__name__}: {str(exc)[:250]}"
            ) from exc

        # Give interaction traffic a chance between Discord API pages.
        await asyncio.sleep(0)

    for channel in found.values():
        if not _can_read_history(channel, member):
            raise RuntimeError(
                "Dank Shield cannot read reconciled history in "
                f"{getattr(channel, 'name', channel.id)} "
                f"({int(channel.id)})."
            )

    return list(found.values())


async def reconcile_restart_gap(
    guild: discord.Guild,
    *,
    after: datetime,
    before: datetime,
) -> RestartReconciliationResult:
    after = _aware(after)
    before = _aware(before)

    if before <= after:
        raise RuntimeError(
            "Restart reconciliation window is invalid."
        )

    channels = await _collect_messageables(
        guild,
        after=after,
    )

    global_limit = _max_reconcile_messages()
    per_channel_limit = _max_messages_per_channel()

    latest_by_user: dict[
        int,
        tuple[datetime, int],
    ] = {}

    scanned_messages = 0

    # Deliberately sequential. There is no gather(), task fanout, or
    # simultaneous channel-history buffering.
    for channel in channels:
        history = channel.history(
            limit=per_channel_limit + 1,
            after=after,
            before=before,
            oldest_first=True,
        )

        async for message in _bounded_async_items(
            history,
            limit=per_channel_limit,
            label=(
                "Restart messages in "
                f"{getattr(channel, 'name', channel.id)}"
            ),
        ):
            scanned_messages += 1

            if scanned_messages > global_limit:
                raise RuntimeError(
                    "Restart gap exceeded the global safe "
                    f"limit of {global_limit} messages."
                )

            author = getattr(message, "author", None)

            if author is None:
                continue

            if bool(getattr(author, "bot", False)):
                continue

            message_guild = getattr(
                message,
                "guild",
                None,
            )

            if (
                message_guild is None
                or int(message_guild.id)
                != int(guild.id)
            ):
                continue

            user_id = int(
                getattr(author, "id", 0) or 0
            )

            if user_id <= 0:
                continue

            occurred_at = getattr(
                message,
                "created_at",
                None,
            )

            if not isinstance(
                occurred_at,
                datetime,
            ):
                continue

            occurred_at = _aware(occurred_at)

            channel_id = int(
                getattr(
                    getattr(
                        message,
                        "channel",
                        None,
                    ),
                    "id",
                    0,
                )
                or 0
            )

            previous = latest_by_user.get(user_id)

            if (
                previous is None
                or occurred_at > previous[0]
            ):
                latest_by_user[user_id] = (
                    occurred_at,
                    channel_id,
                )

            if scanned_messages % 50 == 0:
                await asyncio.sleep(0)

        await asyncio.sleep(0)

    return RestartReconciliationResult(
        scanned_channels=len(channels),
        scanned_messages=scanned_messages,
        replayed_members=len(latest_by_user),
        latest_by_user=latest_by_user,
    )


__all__ = [
    "RestartReconciliationResult",
    "audit_guild_activity_scope",
    "max_reconcile_gap_seconds",
    "reconcile_restart_gap",
    "reconcile_start_delay_seconds",
    "reconcile_timeout_seconds",
]
