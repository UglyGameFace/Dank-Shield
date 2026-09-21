from __future__ import annotations

import asyncio

from stoney_verify.startup_guards import discord_api_safety


def _reset_recovery_budget_state() -> None:
    discord_api_safety._RECOVERY_REST_LOOP = None
    discord_api_safety._RECOVERY_REST_LOCK = None
    discord_api_safety._RECOVERY_REST_RESERVED_AT.clear()
    discord_api_safety._RECOVERY_REST_WAITERS = 0


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
