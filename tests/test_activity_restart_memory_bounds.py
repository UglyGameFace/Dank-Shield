from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any, Callable

import pytest

from stoney_verify.members_new import activity_reconciliation as reconciliation


class CountingAsyncIterator:
    """Lazy iterator that records exactly how many items were requested."""

    def __init__(
        self,
        total: int,
        *,
        factory: Callable[[int], Any] | None = None,
    ) -> None:
        self.total = int(total)
        self.factory = factory or (lambda index: index)
        self.pulls = 0

    def __aiter__(self) -> "CountingAsyncIterator":
        return self

    async def __anext__(self) -> Any:
        if self.pulls >= self.total:
            raise StopAsyncIteration

        index = self.pulls
        self.pulls += 1
        return self.factory(index)


class FakeChannel:
    def __init__(
        self,
        *,
        channel_id: int,
        name: str,
        messages: list[Any],
    ) -> None:
        self.id = int(channel_id)
        self.name = str(name)
        self.messages = list(messages)
        self.history_calls: list[dict[str, Any]] = []
        self.last_iterator: CountingAsyncIterator | None = None

    def history(self, **kwargs: Any) -> CountingAsyncIterator:
        self.history_calls.append(dict(kwargs))

        iterator = CountingAsyncIterator(
            len(self.messages),
            factory=lambda index: self.messages[index],
        )

        self.last_iterator = iterator
        return iterator


def _message(
    *,
    guild_id: int,
    channel_id: int,
    user_id: int,
    occurred_at: datetime,
) -> Any:
    return SimpleNamespace(
        author=SimpleNamespace(
            id=int(user_id),
            bot=False,
        ),
        guild=SimpleNamespace(
            id=int(guild_id),
        ),
        channel=SimpleNamespace(
            id=int(channel_id),
        ),
        created_at=occurred_at,
    )


def test_virtual_million_item_history_stops_at_limit_plus_one() -> None:
    """A huge remote history must never be materialized or fully consumed."""
    iterator = CountingAsyncIterator(1_000_000)

    async def exercise() -> list[int]:
        yielded: list[int] = []

        with pytest.raises(
            RuntimeError,
            match="safe limit of 5",
        ):
            async for item in reconciliation._bounded_async_items(
                iterator,
                limit=5,
                label="virtual million-item history",
            ):
                yielded.append(int(item))

        return yielded

    yielded = asyncio.run(exercise())

    assert yielded == [0, 1, 2, 3, 4]

    # Five accepted items plus exactly one overflow detector.
    assert iterator.pulls == 6


def test_per_channel_history_overflow_fails_closed() -> None:
    now = datetime.now(timezone.utc)
    guild_id = 123

    channel = FakeChannel(
        channel_id=456,
        name="overflow-channel",
        messages=[
            _message(
                guild_id=guild_id,
                channel_id=456,
                user_id=1000 + index,
                occurred_at=now - timedelta(seconds=10 - index),
            )
            for index in range(3)
        ],
    )

    original_collect = reconciliation._collect_messageables
    original_per_channel = reconciliation._max_messages_per_channel
    original_global = reconciliation._max_reconcile_messages

    async def fake_collect(
        guild: Any,
        *,
        after: datetime,
    ) -> list[Any]:
        _ = guild, after
        return [channel]

    async def exercise() -> None:
        reconciliation._collect_messageables = fake_collect
        reconciliation._max_messages_per_channel = lambda: 2
        reconciliation._max_reconcile_messages = lambda: 100

        try:
            with pytest.raises(
                RuntimeError,
                match="safe limit of 2",
            ):
                await reconciliation.reconcile_restart_gap(
                    SimpleNamespace(id=guild_id),
                    after=now - timedelta(minutes=1),
                    before=now,
                )
        finally:
            reconciliation._collect_messageables = original_collect
            reconciliation._max_messages_per_channel = original_per_channel
            reconciliation._max_reconcile_messages = original_global

    asyncio.run(exercise())

    assert channel.history_calls
    assert channel.history_calls[0]["limit"] == 3
    assert channel.last_iterator is not None

    # Two accepted messages and one overflow detector only.
    assert channel.last_iterator.pulls == 3


def test_global_history_overflow_stops_sequential_scan() -> None:
    now = datetime.now(timezone.utc)
    guild_id = 789

    first = FakeChannel(
        channel_id=101,
        name="first",
        messages=[
            _message(
                guild_id=guild_id,
                channel_id=101,
                user_id=2001,
                occurred_at=now - timedelta(seconds=20),
            ),
            _message(
                guild_id=guild_id,
                channel_id=101,
                user_id=2002,
                occurred_at=now - timedelta(seconds=19),
            ),
        ],
    )

    second = FakeChannel(
        channel_id=102,
        name="second",
        messages=[
            _message(
                guild_id=guild_id,
                channel_id=102,
                user_id=2003,
                occurred_at=now - timedelta(seconds=18),
            ),
            _message(
                guild_id=guild_id,
                channel_id=102,
                user_id=2004,
                occurred_at=now - timedelta(seconds=17),
            ),
        ],
    )

    original_collect = reconciliation._collect_messageables
    original_per_channel = reconciliation._max_messages_per_channel
    original_global = reconciliation._max_reconcile_messages

    async def fake_collect(
        guild: Any,
        *,
        after: datetime,
    ) -> list[Any]:
        _ = guild, after
        return [first, second]

    async def exercise() -> None:
        reconciliation._collect_messageables = fake_collect
        reconciliation._max_messages_per_channel = lambda: 2
        reconciliation._max_reconcile_messages = lambda: 3

        try:
            with pytest.raises(
                RuntimeError,
                match="global safe limit of 3",
            ):
                await reconciliation.reconcile_restart_gap(
                    SimpleNamespace(id=guild_id),
                    after=now - timedelta(minutes=1),
                    before=now,
                )
        finally:
            reconciliation._collect_messageables = original_collect
            reconciliation._max_messages_per_channel = original_per_channel
            reconciliation._max_reconcile_messages = original_global

    asyncio.run(exercise())

    assert first.last_iterator is not None
    assert second.last_iterator is not None

    assert first.last_iterator.pulls == 2
    assert second.last_iterator.pulls == 2


def test_source_has_no_unbounded_or_parallel_history_path() -> None:
    source = (
        "stoney_verify/members_new/activity_reconciliation.py"
    )

    with open(source, "r", encoding="utf-8") as handle:
        text = handle.read()

    assert "limit=None" not in text
    assert "asyncio.gather(" not in text
    assert "for channel in channels:" in text
    assert "per_channel_limit + 1" in text
    assert "thread_limit + 1" in text
