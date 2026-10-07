from __future__ import annotations

import asyncio

from stoney_verify import startup_recovery_coordinator as coordinator


def test_quiet_period_reports_remaining_time(monkeypatch) -> None:
    monkeypatch.setattr(coordinator, "_PROCESS_STARTED_AT", 100.0)
    monkeypatch.setattr(coordinator, "_STARTUP_RECOVERY_QUIET_SECONDS", 120)
    monkeypatch.setattr(coordinator.time, "monotonic", lambda: 150.0)

    assert coordinator.startup_recovery_quiet_remaining_seconds() == 70.0


def test_quiet_period_waits_only_for_remaining_window(monkeypatch) -> None:
    monkeypatch.setattr(coordinator, "_PROCESS_STARTED_AT", 100.0)
    monkeypatch.setattr(coordinator, "_STARTUP_RECOVERY_QUIET_SECONDS", 120)
    monkeypatch.setattr(coordinator, "_QUIET_WAIT_LOGGED", False)
    monkeypatch.setattr(coordinator.time, "monotonic", lambda: 150.0)

    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(float(seconds))

    monkeypatch.setattr(coordinator.asyncio, "sleep", fake_sleep)

    asyncio.run(coordinator.wait_for_startup_recovery_quiet_period())

    assert sleeps == [70.0]


def test_startup_recovery_slot_waits_before_acquiring_capacity(monkeypatch) -> None:
    order: list[str] = []

    async def fake_wait() -> None:
        order.append("quiet")

    monkeypatch.setattr(
        coordinator,
        "wait_for_startup_recovery_quiet_period",
        fake_wait,
    )
    monkeypatch.setattr(coordinator, "_LOOP", None)
    monkeypatch.setattr(coordinator, "_GLOBAL_SEMAPHORE", None)
    coordinator._GUILD_SLOTS.clear()
    coordinator._CURRENT.clear()

    async def scenario() -> None:
        async with coordinator.startup_recovery_slot(123, "test"):
            order.append("slot")

    asyncio.run(scenario())

    assert order == ["quiet", "slot"]
