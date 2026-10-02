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
    assert discord_api_safety._recovery_rest_budget_per_30s() == 100
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
