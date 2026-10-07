from __future__ import annotations

import asyncio

from stoney_verify.startup_guards import discord_api_safety


def _reset_recovery_budget_state() -> None:
    discord_api_safety._RECOVERY_REST_LOOP = None
    discord_api_safety._RECOVERY_REST_LOCK = None
    discord_api_safety._RECOVERY_REST_RESERVED_AT.clear()
    discord_api_safety._RECOVERY_REST_WAITERS = 0
    discord_api_safety._MESSAGE_FETCH_LOOP = None
    discord_api_safety._MESSAGE_FETCH_LOCKS.clear()
    discord_api_safety._MESSAGE_FETCH_INFLIGHT.clear()
    discord_api_safety._MESSAGE_FETCH_COALESCED = 0


def test_recovery_request_weight_reserves_maximum_page_count() -> None:
    assert discord_api_safety.recovery_request_weight(1) == 1
    assert discord_api_safety.recovery_request_weight(100) == 1
    assert discord_api_safety.recovery_request_weight(101) == 2
    assert discord_api_safety.recovery_request_weight(251) == 3
    assert discord_api_safety.recovery_request_weight(1001, page_size=1000) == 2


def test_recovery_budget_waits_instead_of_bursting_past_window(monkeypatch) -> None:
    _reset_recovery_budget_state()
    now = [100.0]
    sleeps: list[float] = []

    monkeypatch.setattr(
        discord_api_safety,
        "_recovery_rest_budget_per_30s",
        lambda: 3,
    )
    monkeypatch.setattr(
        discord_api_safety,
        "_recovery_rest_now",
        lambda: now[0],
    )

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(float(seconds))
        now[0] += float(seconds)

    monkeypatch.setattr(
        discord_api_safety,
        "_recovery_rest_sleep",
        fake_sleep,
    )

    async def scenario() -> tuple[dict[str, int | float], dict[str, int | float]]:
        await discord_api_safety.reserve_recovery_discord_rest_requests(
            2,
            label="first",
        )
        first = discord_api_safety.recovery_discord_rest_budget_snapshot()

        # Only one slot remains. A second two-request reservation must wait for
        # the original 30-second window to expire rather than over-allocating.
        await discord_api_safety.reserve_recovery_discord_rest_requests(
            2,
            label="second",
        )
        second = discord_api_safety.recovery_discord_rest_budget_snapshot()
        return first, second

    try:
        first, second = asyncio.run(scenario())
        assert first["budget_per_30s"] == 3
        assert first["reserved_in_window"] == 2
        assert second["reserved_in_window"] == 2
        assert sleeps
        assert sleeps[0] >= discord_api_safety._RECOVERY_REST_WINDOW_SECONDS
    finally:
        _reset_recovery_budget_state()


def test_default_recovery_budget_keeps_large_headroom_below_discloud_limit(monkeypatch) -> None:
    monkeypatch.delenv("DANK_RECOVERY_DISCORD_REST_BUDGET_PER_30S", raising=False)
    assert discord_api_safety._recovery_rest_budget_per_30s() == 40
    assert discord_api_safety._recovery_rest_budget_per_30s() <= 60
    assert discord_api_safety._recovery_rest_budget_per_30s() < 300


class _FetchChannel:
    def __init__(self, channel_id: int, *, delay: float = 0.01) -> None:
        self.id = int(channel_id)
        self.delay = float(delay)
        self.calls: list[int] = []
        self.active = 0
        self.max_active = 0

    async def fetch_message(self, message_id: int):
        self.calls.append(int(message_id))
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            await asyncio.sleep(self.delay)
            return {"channel_id": self.id, "message_id": int(message_id)}
        finally:
            self.active -= 1


def test_safe_message_fetch_serializes_different_ids_in_one_channel() -> None:
    _reset_recovery_budget_state()
    channel = _FetchChannel(55)

    async def scenario():
        return await asyncio.gather(
            discord_api_safety.fetch_message_with_api_safety(
                channel,
                101,
                label="first",
            ),
            discord_api_safety.fetch_message_with_api_safety(
                channel,
                102,
                label="second",
            ),
        )

    try:
        results = asyncio.run(scenario())
        assert [item["message_id"] for item in results] == [101, 102]
        assert sorted(channel.calls) == [101, 102]
        assert channel.max_active == 1
        snapshot = discord_api_safety.message_fetch_safety_snapshot()
        assert snapshot["channels"] == 0
        assert snapshot["inflight"] == 0
    finally:
        _reset_recovery_budget_state()


def test_safe_message_fetch_does_not_globally_serialize_other_channels() -> None:
    _reset_recovery_budget_state()
    first = _FetchChannel(56, delay=0.03)
    second = _FetchChannel(57, delay=0.03)
    total_active = 0
    max_total_active = 0
    gate = asyncio.Lock()

    async def tracked_fetch(channel: _FetchChannel, message_id: int):
        nonlocal total_active, max_total_active
        async with gate:
            total_active += 1
            max_total_active = max(max_total_active, total_active)
        try:
            await asyncio.sleep(0.03)
            return {"channel_id": channel.id, "message_id": int(message_id)}
        finally:
            async with gate:
                total_active -= 1

    async def scenario():
        first.fetch_message = lambda message_id: tracked_fetch(first, message_id)
        second.fetch_message = lambda message_id: tracked_fetch(second, message_id)
        return await asyncio.gather(
            discord_api_safety.fetch_message_with_api_safety(first, 111),
            discord_api_safety.fetch_message_with_api_safety(second, 222),
        )

    try:
        results = asyncio.run(scenario())
        assert {item["channel_id"] for item in results} == {56, 57}
        assert max_total_active == 2
        snapshot = discord_api_safety.message_fetch_safety_snapshot()
        assert snapshot["channels"] == 0
        assert snapshot["inflight"] == 0
    finally:
        _reset_recovery_budget_state()


def test_safe_message_fetch_coalesces_same_inflight_message() -> None:
    _reset_recovery_budget_state()
    channel = _FetchChannel(66, delay=0.02)

    async def scenario():
        return await asyncio.gather(
            discord_api_safety.fetch_message_with_api_safety(
                channel,
                201,
                label="owner-a",
            ),
            discord_api_safety.fetch_message_with_api_safety(
                channel,
                201,
                label="owner-b",
            ),
        )

    try:
        first, second = asyncio.run(scenario())
        assert first == second
        assert channel.calls == [201]
        snapshot = discord_api_safety.message_fetch_safety_snapshot()
        assert snapshot["coalesced"] == 1
        assert snapshot["inflight"] == 0
        assert snapshot["channels"] == 0
    finally:
        _reset_recovery_budget_state()


def test_safe_message_fetch_recovery_reserves_existing_budget(monkeypatch) -> None:
    _reset_recovery_budget_state()
    channel = _FetchChannel(77, delay=0)
    reservations: list[tuple[int, str]] = []

    async def reserve(weight: int = 1, *, label: str = "recovery") -> None:
        reservations.append((int(weight), str(label)))

    monkeypatch.setattr(
        discord_api_safety,
        "reserve_recovery_discord_rest_requests",
        reserve,
    )

    try:
        asyncio.run(
            discord_api_safety.fetch_message_with_api_safety(
                channel,
                301,
                label="community sticky startup guild=1",
                recovery=True,
            )
        )
        assert reservations == [
            (
                1,
                "message fetch community sticky startup guild=1 channel=77",
            )
        ]
    finally:
        _reset_recovery_budget_state()


def test_recovery_budget_wait_does_not_block_live_same_channel(monkeypatch) -> None:
    _reset_recovery_budget_state()
    channel = _FetchChannel(78, delay=0)
    recovery_entered = asyncio.Event()
    release_recovery = asyncio.Event()
    call_order: list[int] = []

    async def reserve(*_args, **_kwargs) -> None:
        recovery_entered.set()
        await release_recovery.wait()

    async def fetch(message_id: int):
        call_order.append(int(message_id))
        return {"channel_id": channel.id, "message_id": int(message_id)}

    channel.fetch_message = fetch
    monkeypatch.setattr(
        discord_api_safety,
        "reserve_recovery_discord_rest_requests",
        reserve,
    )

    async def scenario():
        recovery_task = asyncio.create_task(
            discord_api_safety.fetch_message_with_api_safety(
                channel,
                351,
                label="startup",
                recovery=True,
            )
        )
        await recovery_entered.wait()

        live_result = await discord_api_safety.fetch_message_with_api_safety(
            channel,
            352,
            label="live",
            recovery=False,
        )
        assert live_result["message_id"] == 352
        assert call_order == [352]

        release_recovery.set()
        recovery_result = await recovery_task
        return recovery_result

    try:
        result = asyncio.run(scenario())
        assert result["message_id"] == 351
        assert call_order == [352, 351]
    finally:
        _reset_recovery_budget_state()


def test_safe_message_fetch_live_path_skips_recovery_budget(monkeypatch) -> None:
    _reset_recovery_budget_state()
    channel = _FetchChannel(88, delay=0)
    reservations: list[str] = []

    async def reserve(*_args, **kwargs) -> None:
        reservations.append(str(kwargs.get("label") or ""))

    monkeypatch.setattr(
        discord_api_safety,
        "reserve_recovery_discord_rest_requests",
        reserve,
    )

    try:
        asyncio.run(
            discord_api_safety.fetch_message_with_api_safety(
                channel,
                401,
                label="invite raw edit",
                recovery=False,
            )
        )
        assert reservations == []
    finally:
        _reset_recovery_budget_state()


def test_cancelled_coalesced_waiter_does_not_cancel_shared_fetch() -> None:
    _reset_recovery_budget_state()

    class _BlockingFetchChannel:
        def __init__(self) -> None:
            self.id = 99
            self.calls: list[int] = []
            self.started = asyncio.Event()
            self.release = asyncio.Event()

        async def fetch_message(self, message_id: int):
            self.calls.append(int(message_id))
            self.started.set()
            await self.release.wait()
            return {"channel_id": self.id, "message_id": int(message_id)}

    async def scenario():
        channel = _BlockingFetchChannel()
        first = asyncio.create_task(
            discord_api_safety.fetch_message_with_api_safety(
                channel,
                501,
                label="first waiter",
            )
        )
        await channel.started.wait()

        second = asyncio.create_task(
            discord_api_safety.fetch_message_with_api_safety(
                channel,
                501,
                label="second waiter",
            )
        )
        await asyncio.sleep(0)

        first.cancel()
        try:
            await first
        except asyncio.CancelledError:
            pass

        assert not second.done()
        channel.release.set()
        result = await second
        return channel, result

    try:
        channel, result = asyncio.run(scenario())
        assert result == {"channel_id": 99, "message_id": 501}
        assert channel.calls == [501]
        snapshot = discord_api_safety.message_fetch_safety_snapshot()
        assert snapshot["coalesced"] == 1
        assert snapshot["inflight"] == 0
        assert snapshot["channels"] == 0
    finally:
        _reset_recovery_budget_state()
