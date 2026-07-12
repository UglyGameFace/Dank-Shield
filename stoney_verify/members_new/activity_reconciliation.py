from __future__ import annotations

"""Fail-closed reconciliation of short bot restart gaps.

Only durable Discord message history is replayed. Reactions remain useful
supplemental context, but they are not used to authorize member cleanup because
Discord cannot reconstruct their exact downtime history reliably.
"""

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
import os
from typing import Any

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
    # Large maintenance windows remain untrusted. The owner may raise this,
    # but the scanner still fails closed on permissions, volume, or API errors.
    return _env_int(
        "DANK_ACTIVITY_RECONCILE_MAX_GAP_SECONDS",
        3600,
        minimum=180,
        maximum=86400,
    )


def _max_reconcile_messages() -> int:
    return _env_int(
        "DANK_ACTIVITY_RECONCILE_MAX_MESSAGES",
        10000,
        minimum=100,
        maximum=100000,
    )


def _reconcile_concurrency() -> int:
    return _env_int(
        "DANK_ACTIVITY_RECONCILE_CHANNEL_CONCURRENCY",
        3,
        minimum=1,
        maximum=8,
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
        and getattr(permissions, "read_message_history", False)
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
                bool(getattr(permissions, "view_channel", False))
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


def audit_guild_activity_scope(guild: discord.Guild) -> str:
    """Return an error when complete durable activity coverage is impossible."""
    member = getattr(guild, "me", None)

    if not isinstance(member, discord.Member):
        return "Could not resolve Dank Shield's guild member permissions."

    for channel in list(getattr(guild, "channels", []) or []):
        has_history = callable(getattr(channel, "history", None))
        is_thread_parent = isinstance(
            channel,
            (discord.TextChannel, discord.ForumChannel),
        )

        if (has_history or is_thread_parent) and not _can_read_history(
            channel,
            member,
        ):
            return (
                "Activity coverage is incomplete because Dank Shield cannot "
                f"view/read history in #{getattr(channel, 'name', channel.id)} "
                f"({int(channel.id)})."
            )

        if isinstance(channel, discord.TextChannel):
            permissions = _permissions_for(channel, member)
            manage_threads = bool(
                getattr(permissions, "manage_threads", False)
            )

            if (
                not manage_threads
                and _private_threads_may_exist(channel, guild)
            ):
                return (
                    "Activity coverage is incomplete because members can use "
                    f"private threads in #{channel.name}, but Dank Shield "
                    "cannot enumerate all private threads. Grant Manage "
                    "Threads or disable private-thread creation there."
                )

    for thread in list(getattr(guild, "threads", []) or []):
        if not _can_read_history(thread, member):
            return (
                "Activity coverage is incomplete because Dank Shield cannot "
                f"read active thread {getattr(thread, 'name', thread.id)} "
                f"({int(thread.id)})."
            )

    return ""


def _thread_is_relevant(
    thread: discord.Thread,
    after: datetime,
) -> bool:
    timestamp = getattr(thread, "archive_timestamp", None)

    if not isinstance(timestamp, datetime):
        return True

    return _aware(timestamp) > _aware(after)


async def _collect_messageables(
    guild: discord.Guild,
    *,
    after: datetime,
) -> list[Any]:
    member = getattr(guild, "me", None)

    if not isinstance(member, discord.Member):
        raise RuntimeError(
            "Could not resolve Dank Shield's guild member permissions."
        )

    scope_error = audit_guild_activity_scope(guild)
    if scope_error:
        raise RuntimeError(scope_error)

    found: dict[int, Any] = {}

    for channel in list(getattr(guild, "channels", []) or []):
        if callable(getattr(channel, "history", None)):
            found[int(channel.id)] = channel

    for thread in list(getattr(guild, "threads", []) or []):
        found[int(thread.id)] = thread

    parents = [
        channel
        for channel in list(getattr(guild, "channels", []) or [])
        if isinstance(
            channel,
            (discord.TextChannel, discord.ForumChannel),
        )
    ]

    for parent in parents:
        try:
            if isinstance(parent, discord.TextChannel):
                async for thread in parent.archived_threads(
                    limit=None,
                    private=False,
                ):
                    if not _thread_is_relevant(thread, after):
                        break
                    found[int(thread.id)] = thread

                permissions = parent.permissions_for(member)

                if bool(
                    getattr(permissions, "manage_threads", False)
                ):
                    async for thread in parent.archived_threads(
                        limit=None,
                        private=True,
                        joined=False,
                    ):
                        if not _thread_is_relevant(thread, after):
                            break
                        found[int(thread.id)] = thread

            elif isinstance(parent, discord.ForumChannel):
                async for thread in parent.archived_threads(
                    limit=None,
                ):
                    if not _thread_is_relevant(thread, after):
                        break
                    found[int(thread.id)] = thread

        except Exception as exc:
            raise RuntimeError(
                "Could not enumerate archived threads for "
                f"{getattr(parent, 'name', parent.id)}: "
                f"{type(exc).__name__}: {str(exc)[:250]}"
            ) from exc

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

    semaphore = asyncio.Semaphore(
        _reconcile_concurrency()
    )
    message_limit = _max_reconcile_messages()

    latest_by_user: dict[int, tuple[datetime, int]] = {}
    scanned_messages = 0
    aborted = asyncio.Event()

    async def scan_channel(channel: Any) -> None:
        nonlocal scanned_messages

        async with semaphore:
            try:
                history = channel.history(
                    limit=None,
                    after=after,
                    before=before,
                    oldest_first=True,
                )

                async for message in history:
                    if aborted.is_set():
                        return

                    scanned_messages += 1

                    if scanned_messages > message_limit:
                        aborted.set()
                        raise RuntimeError(
                            "Restart gap exceeded the configured history "
                            f"limit of {message_limit} messages."
                        )

                    author = getattr(message, "author", None)
                    if author is None:
                        continue
                    if bool(getattr(author, "bot", False)):
                        continue

                    message_guild = getattr(message, "guild", None)
                    if (
                        message_guild is None
                        or int(message_guild.id) != int(guild.id)
                    ):
                        continue

                    user_id = int(getattr(author, "id", 0) or 0)
                    if user_id <= 0:
                        continue

                    occurred_at = getattr(
                        message,
                        "created_at",
                        None,
                    )
                    if not isinstance(occurred_at, datetime):
                        continue

                    occurred_at = _aware(occurred_at)
                    channel_id = int(
                        getattr(
                            getattr(message, "channel", None),
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

            except Exception:
                aborted.set()
                raise

    results = await asyncio.gather(
        *(scan_channel(channel) for channel in channels),
        return_exceptions=True,
    )

    failures = [
        result
        for result in results
        if isinstance(result, BaseException)
    ]

    if failures:
        first = failures[0]
        raise RuntimeError(
            "Restart history reconciliation failed: "
            f"{type(first).__name__}: {str(first)[:350]}"
        ) from first

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
]
