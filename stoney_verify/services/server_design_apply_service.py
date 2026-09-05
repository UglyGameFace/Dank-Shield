from __future__ import annotations

"""Transactional rename execution for Dank Design.

Discord does not provide a multi-channel rename transaction. This service gives
Dank Design transaction-like behavior: preflight the complete batch before the
first edit, stop on the first unexpected failure, and compensate earlier edits
when a later edit fails. UI modules only render the result.
"""

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any

ProgressCallback = Callable[[int, int], Awaitable[None]]


@dataclass
class PreparedRename:
    channel_id: int
    channel: Any
    item: dict[str, Any]
    before: str
    after: str


@dataclass
class TransactionResult:
    ok: bool
    applied: list[PreparedRename]
    failure: str = ""
    restored_count: int = 0
    residual: list[PreparedRename] | None = None
    rollback_failures: list[str] | None = None

    def __post_init__(self) -> None:
        if self.residual is None:
            self.residual = []
        if self.rollback_failures is None:
            self.rollback_failures = []


def _text(value: Any) -> str:
    try:
        return str(value or "").strip()
    except Exception:
        return ""


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except Exception:
        return 0


async def fresh_channel_map(guild: Any) -> dict[int, Any]:
    """Return one fresh channel snapshot when Discord's fetch API is available."""

    fetch_all = getattr(guild, "fetch_channels", None)
    if callable(fetch_all):
        try:
            rows = await fetch_all()
            return {
                _int(getattr(channel, "id", 0)): channel
                for channel in list(rows or [])
                if _int(getattr(channel, "id", 0)) > 0
            }
        except Exception:
            pass

    out: dict[int, Any] = {}
    for channel in list(getattr(guild, "channels", []) or []) + list(getattr(guild, "categories", []) or []):
        channel_id = _int(getattr(channel, "id", 0))
        if channel_id > 0:
            out[channel_id] = channel
    return out


async def preflight_plan(
    guild: Any,
    items: list[Mapping[str, Any]],
    *,
    name_limit: int,
) -> tuple[list[PreparedRename], int, list[str]]:
    """Validate the complete changed set before the first Discord edit."""

    fresh = await fresh_channel_map(guild)
    ready: list[PreparedRename] = []
    skipped = 0
    errors: list[str] = []

    for raw in items:
        item = dict(raw)
        if item.get("status") != "changed":
            skipped += 1
            continue

        channel_id = _int(item.get("channel_id"))
        channel = fresh.get(channel_id)
        if channel is None:
            get_channel = getattr(guild, "get_channel", None)
            channel = get_channel(channel_id) if callable(get_channel) else None
        before = _text(item.get("before"))
        after = _text(item.get("after"))

        if channel is None:
            errors.append(f"Missing item that was previewed as `{before or channel_id}`.")
            continue
        if not before:
            errors.append(f"Preview row `{channel_id}` has no original name.")
            continue
        if not after:
            errors.append(f"`{before}` would become a blank name.")
            continue
        if len(after) > int(name_limit):
            errors.append(f"`{before}` now exceeds Discord's name limit.")
            continue

        current = _text(getattr(channel, "name", ""))
        if current != before:
            errors.append(f"`{before}` is now `{current}`.")
            continue

        ready.append(PreparedRename(channel_id=channel_id, channel=channel, item=item, before=before, after=after))

    return ready, skipped, errors


async def _resolve_fresh_channel(guild: Any, channel_id: int, fallback: Any) -> Any:
    fetch_one = getattr(guild, "fetch_channel", None)
    if callable(fetch_one):
        try:
            return await fetch_one(int(channel_id))
        except Exception:
            pass
    get_channel = getattr(guild, "get_channel", None)
    if callable(get_channel):
        try:
            cached = get_channel(int(channel_id))
            if cached is not None:
                return cached
        except Exception:
            pass
    return fallback


async def compensate_applied(
    guild: Any,
    applied: list[PreparedRename],
    *,
    user_id: int,
    delay_seconds: float,
) -> tuple[int, list[PreparedRename], list[str]]:
    """Restore rows changed by the current failed transaction attempt.

    A fresh channel object is resolved before compensation because discord.py 2.x
    channel edits return edited objects rather than mutating the original object
    in place.
    """

    restored = 0
    residual: list[PreparedRename] = []
    failures: list[str] = []

    for prepared in reversed(applied):
        channel = await _resolve_fresh_channel(guild, prepared.channel_id, prepared.channel)
        current = _text(getattr(channel, "name", ""))
        if current and current != prepared.after:
            prepared.channel = channel
            residual.append(prepared)
            failures.append(f"`{prepared.after}` changed again before automatic rollback.")
            continue
        try:
            edited = await channel.edit(
                name=prepared.before,
                reason=f"Dank Shield automatic rollback after failed design transaction by {int(user_id)}",
            )
            if edited is not None:
                prepared.channel = edited
            restored += 1
            if delay_seconds > 0:
                await asyncio.sleep(delay_seconds)
        except Exception as exc:
            prepared.channel = channel
            residual.append(prepared)
            failures.append(f"Could not restore `{prepared.after}`: {type(exc).__name__}")

    return restored, residual, failures


async def apply_prepared(
    guild: Any,
    ready: list[PreparedRename],
    *,
    user_id: int,
    delay_seconds: float,
    progress: ProgressCallback | None = None,
) -> TransactionResult:
    """Apply a fully preflighted batch and compensate on the first failure."""

    applied: list[PreparedRename] = []
    for prepared in ready:
        try:
            edited = await prepared.channel.edit(
                name=prepared.after,
                reason=f"Dank Shield reviewed Server Design apply by {int(user_id)}",
            )
            if edited is not None:
                prepared.channel = edited
            applied.append(prepared)
            if progress is not None:
                await progress(len(applied), len(ready))
            if delay_seconds > 0:
                await asyncio.sleep(delay_seconds)
        except Exception as exc:
            failure = f"Discord rejected `{prepared.before}` → `{prepared.after}`: {type(exc).__name__}."
            restored, residual, rollback_failures = await compensate_applied(
                guild,
                applied,
                user_id=user_id,
                delay_seconds=delay_seconds,
            )
            return TransactionResult(
                ok=False,
                applied=applied,
                failure=failure,
                restored_count=restored,
                residual=residual,
                rollback_failures=rollback_failures,
            )

    return TransactionResult(ok=True, applied=applied)


def snapshot_rows(applied: list[PreparedRename], *, user_id: int, timestamp: float) -> list[dict[str, Any]]:
    return [
        {
            **prepared.item,
            "old_name": prepared.before,
            "new_name": prepared.after,
            "admin_id": str(int(user_id)),
            "timestamp": float(timestamp),
            "action_type": "apply",
        }
        for prepared in applied
    ]


async def preflight_undo(
    guild: Any,
    snapshot_items: list[Mapping[str, Any]],
    *,
    name_limit: int,
) -> tuple[list[PreparedRename], list[str]]:
    """Prepare the inverse of one saved Apply snapshot."""

    inverse: list[dict[str, Any]] = []
    for raw in snapshot_items:
        item = dict(raw)
        item["before"] = _text(item.get("new_name") or item.get("after"))
        item["after"] = _text(item.get("old_name") or item.get("before"))
        item["status"] = "changed"
        inverse.append(item)
    ready, _skipped, errors = await preflight_plan(guild, inverse, name_limit=name_limit)
    return ready, errors


async def undo_prepared(
    guild: Any,
    ready: list[PreparedRename],
    *,
    user_id: int,
    delay_seconds: float,
) -> TransactionResult:
    """Undo an Apply snapshot and restore the pre-undo state if Undo fails."""

    restored_to_old: list[PreparedRename] = []
    for prepared in reversed(ready):
        try:
            edited = await prepared.channel.edit(
                name=prepared.after,
                reason=f"Dank Shield Undo Last Apply by {int(user_id)}",
            )
            if edited is not None:
                prepared.channel = edited
            restored_to_old.append(prepared)
            if delay_seconds > 0:
                await asyncio.sleep(delay_seconds)
        except Exception as exc:
            failure = f"Discord rejected Undo for `{prepared.before}` → `{prepared.after}`: {type(exc).__name__}."
            # Each successful Undo row already has before=new and after=old.
            # Generic compensation therefore does exactly what we need here:
            # verify the current name is old, then restore it to new.
            restored, residual, rollback_failures = await compensate_applied(
                guild,
                restored_to_old,
                user_id=user_id,
                delay_seconds=delay_seconds,
            )
            return TransactionResult(
                ok=False,
                applied=restored_to_old,
                failure=failure,
                restored_count=restored,
                residual=residual,
                rollback_failures=rollback_failures,
            )

    return TransactionResult(ok=True, applied=restored_to_old)


__all__ = [
    "PreparedRename",
    "TransactionResult",
    "apply_prepared",
    "compensate_applied",
    "fresh_channel_map",
    "preflight_plan",
    "preflight_undo",
    "snapshot_rows",
    "undo_prepared",
]
