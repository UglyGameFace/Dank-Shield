from __future__ import annotations

"""Shared per-guild lock for Discord naming mutations.

Dank Design applies multi-channel rename transactions while Search-Safe Naming
can mutate roles/channels from reviewed repairs or gateway enforcement. Both
systems must share one guild-level mutation boundary so their Discord PATCHes
cannot interleave and invalidate each other's preflight assumptions.
"""

import asyncio
import weakref
from typing import Any


GUILD_NAMING_LOCKS: weakref.WeakValueDictionary[int, asyncio.Lock] = (
    weakref.WeakValueDictionary()
)


def _guild_id(value: Any) -> int:
    try:
        return int(value or 0)
    except Exception:
        return 0


def guild_naming_lock(guild_id: Any) -> asyncio.Lock:
    gid = _guild_id(guild_id)
    if gid <= 0:
        raise ValueError("guild_id must be a positive Discord snowflake")

    lock = GUILD_NAMING_LOCKS.get(gid)
    if lock is None:
        lock = asyncio.Lock()
        GUILD_NAMING_LOCKS[gid] = lock
    return lock


__all__ = ["GUILD_NAMING_LOCKS", "guild_naming_lock"]
