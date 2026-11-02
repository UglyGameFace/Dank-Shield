"""Bounded, short-lived cache for *staff-facing* member history reads.

Never cache live Discord membership, staff authorization, verdicts or decisions.
This process-local optimization is not authoritative or shared between workers.
"""
from __future__ import annotations

import asyncio
from collections import OrderedDict
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from threading import RLock
import time
from typing import Any, Awaitable, Callable


@dataclass
class _Entry:
    snapshot: dict[str, Any]
    created_monotonic: float
    created_at: str
    expires_at: float
    stale_until: float


@dataclass
class _Flight:
    task: asyncio.Task
    loop: asyncio.AbstractEventLoop
    invalidated: bool = False


class MemberIntelligenceSnapshotCache:
    """Exact-guild/target/parameters TTL cache with same-loop singleflight.

    Snapshot results have bounded recent rows; never put unbounded raw messages
    or third-party profiles here. Invalidation races cannot repopulate the cache.
    """

    def __init__(
        self, *, ttl_seconds: float = 25.0,
        negative_ttl_seconds: float = 5.0,
        stale_seconds: float = 120.0,
        max_entries: int = 128,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.ttl_seconds = ttl_seconds
        self.negative_ttl_seconds = negative_ttl_seconds
        self.stale_seconds = stale_seconds
        self.max_entries = max_entries
        self._clock = clock
        self._lock = RLock()
        self._entries: OrderedDict[tuple, _Entry] = OrderedDict()
        self._flights: dict[tuple, _Flight] = {}

    @staticmethod
    def _decorate(snapshot: dict[str, Any], state: str, *, age: float = 0,
                  cached_at: str | None = None,
                  unavailable: list[str] | None = None) -> dict[str, Any]:
        out = deepcopy(snapshot)
        out["cache_state"] = state
        out["cache_age_seconds"] = round(age, 1)
        out["cache_cached_at"] = cached_at
        if unavailable:
            out["cache_refresh_unavailable"] = list(unavailable)
        if state == "stale":
            out["cache_warning"] = (
                "Showing older retained evidence because the current database "
                "read failed. Not proof of current status or a clean history."
            )
        elif state == "superseded":
            out["cache_warning"] = "History changed during this read. Refresh evidence again."
        return out

    @staticmethod
    def _unavailable(snapshot: dict[str, Any]) -> list[str]:
        return [
            name for name, state in (snapshot.get("source_status") or {}).items()
            if state == "unavailable"
        ]

    @staticmethod
    def _empty(snapshot: dict[str, Any]) -> bool:
        member = snapshot.get("member") or {}
        return not (
            member.get("username") or member.get("joined_at")
            or snapshot.get("recent_joins") or snapshot.get("recent_events")
            or snapshot.get("recent_feed_events") or snapshot.get("recent_tickets")
            or snapshot.get("activity_ledger") or snapshot.get("open_ticket")
        )

    def invalidate(self, guild_id: int | str, user_id: int | str) -> None:
        """Drop one account in one guild and invalidate any in-flight results."""
        guild, user = str(guild_id), str(user_id)
        with self._lock:
            for key in list(self._entries):
                if key[:2] == (guild, user):
                    del self._entries[key]
            for key, flight in list(self._flights.items()):
                if key[:2] == (guild, user):
                    flight.invalidated = True
                    del self._flights[key]

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
            for flight in self._flights.values():
                flight.invalidated = True
            self._flights.clear()

    async def get_or_load(
        self,
        key: tuple,
        loader: Callable[[], Awaitable[dict[str, Any]]],
        *, force_refresh: bool = False,
    ) -> dict[str, Any]:
        now = self._clock()
        loop = asyncio.get_running_loop()
        with self._lock:
            entry = self._entries.get(key)
            if entry is not None and now >= entry.stale_until:
                del self._entries[key]
                entry = None
            if entry is not None and not force_refresh and now < entry.expires_at:
                self._entries.move_to_end(key)
                return self._decorate(
                    entry.snapshot, "hit",
                    age=max(0, now - entry.created_monotonic),
                    cached_at=entry.created_at,
                )
            flight = self._flights.get(key)
            if flight is not None and flight.loop is loop and not flight.invalidated:
                task = flight.task
            else:
                # Avoid sharing asyncio tasks across event loops (e.g. test
                # harnesses or process worker threads). Different loops can
                # independently load, but all results are guild/target scoped.
                task = loop.create_task(self._load(key, loader, entry))
                self._flights[key] = _Flight(task, loop)
        # One cancelled Discord interaction must not cancel another waiter's
        # read or leave the cache locked.
        return await asyncio.shield(task)

    async def _load(
        self,
        key: tuple,
        loader: Callable[[], Awaitable[dict[str, Any]]],
        stale: _Entry | None,
    ) -> dict[str, Any]:
        task = asyncio.current_task()
        try:
            try:
                snapshot = await loader()
            except Exception:
                now = self._clock()
                with self._lock:
                    current = self._flights.get(key)
                    invalidated = current is None or current.task is not task or current.invalidated
                if stale and not invalidated and now < stale.stale_until:
                    return self._decorate(
                        stale.snapshot, "stale", age=now - stale.created_monotonic,
                        cached_at=stale.created_at, unavailable=["current_read"],
                    )
                raise
            if not isinstance(snapshot, dict):
                raise TypeError("Member intelligence snapshot must be a dict")

            now = self._clock()
            unavailable = self._unavailable(snapshot)
            statuses = snapshot.get("source_status") or {}
            with self._lock:
                current = self._flights.get(key)
                invalidated = current is None or current.task is not task or current.invalidated

            if invalidated:
                return self._decorate(snapshot, "superseded")
            if unavailable:
                # Keep partially fresh evidence visible. Use stale evidence
                # only when EVERY source was unavailable, and label it stale.
                if (stale and now < stale.stale_until and statuses
                        and len(unavailable) == len(statuses)):
                    return self._decorate(
                        stale.snapshot, "stale",
                        age=now - stale.created_monotonic,
                        cached_at=stale.created_at, unavailable=unavailable,
                    )
                return self._decorate(snapshot, "partial", unavailable=unavailable)

            if statuses and snapshot.get("ok"):
                ttl = self.negative_ttl_seconds if self._empty(snapshot) else self.ttl_seconds
                cached_at = datetime.now(timezone.utc).isoformat()
                entry = _Entry(
                    snapshot=deepcopy(snapshot),
                    created_monotonic=now,
                    created_at=cached_at,
                    expires_at=now + ttl,
                    stale_until=now + max(ttl, self.stale_seconds),
                )
                with self._lock:
                    current = self._flights.get(key)
                    if current is not None and current.task is task and not current.invalidated:
                        for k, old in list(self._entries.items()):
                            if now >= old.stale_until:
                                del self._entries[k]
                        self._entries[key] = entry
                        self._entries.move_to_end(key)
                        while len(self._entries) > self.max_entries:
                            self._entries.popitem(last=False)
                return self._decorate(snapshot, "fresh", cached_at=cached_at)
            return self._decorate(snapshot, "uncached")
        finally:
            with self._lock:
                current = self._flights.get(key)
                if current is not None and current.task is task:
                    del self._flights[key]


STAFF_MEMBER_INTELLIGENCE_CACHE = MemberIntelligenceSnapshotCache()


def invalidate_member_intelligence(guild_id: int | str, user_id: int | str) -> None:
    STAFF_MEMBER_INTELLIGENCE_CACHE.invalidate(guild_id, user_id)
