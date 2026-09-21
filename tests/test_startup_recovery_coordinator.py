from __future__ import annotations

import asyncio

from stoney_verify import startup_recovery_coordinator as coordinator


def test_same_guild_recovery_never_overlaps_and_slot_state_is_released() -> None:
    async def run() -> None:
        active = 0
        max_active = 0

        async def worker(label: str) -> None:
            nonlocal active, max_active
            async with coordinator.startup_recovery_slot(123, label):
                active += 1
                max_active = max(max_active, active)
                await asyncio.sleep(0.01)
                active -= 1

        await asyncio.gather(worker("one"), worker("two"), worker("three"))

        assert max_active == 1
        snapshot = coordinator.startup_recovery_snapshot()
        assert snapshot["guild_slots"] == 0
        assert snapshot["current"] == {}

    asyncio.run(run())


def test_different_guilds_respect_process_wide_concurrency_cap() -> None:
    async def run() -> None:
        active = 0
        max_active = 0

        async def worker(guild_id: int) -> None:
            nonlocal active, max_active
            async with coordinator.startup_recovery_slot(guild_id, f"guild-{guild_id}"):
                active += 1
                max_active = max(max_active, active)
                await asyncio.sleep(0.02)
                active -= 1

        await asyncio.gather(*(worker(guild_id) for guild_id in range(1, 7)))

        assert max_active <= coordinator._MAX_CONCURRENT  # noqa: SLF001
        assert max_active >= 1
        snapshot = coordinator.startup_recovery_snapshot()
        assert snapshot["running"] == 0
        assert snapshot["waiting"] == 0
        assert snapshot["guild_slots"] == 0

    asyncio.run(run())
