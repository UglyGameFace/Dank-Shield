from __future__ import annotations

import asyncio

from stoney_verify.tickets_new.orphan_safety import cleanup_unpersisted_ticket_channel


class FakeChannel:
    def __init__(self, channel_id: int = 123) -> None:
        self.id = channel_id
        self.sent: list[dict] = []
        self.deleted: list[str] = []

    async def send(self, content: str, **kwargs) -> None:
        self.sent.append({"content": content, **kwargs})

    async def delete(self, *, reason: str) -> None:
        self.deleted.append(reason)


def test_cleanup_unpersisted_ticket_channel_keeps_channel_when_row_exists():
    async def run() -> None:
        channel = FakeChannel()

        async def row_exists(channel_id: int | str) -> bool:
            return True

        cleaned = await cleanup_unpersisted_ticket_channel(
            channel,
            owner_id=456,
            ticket_number=7,
            row_exists=row_exists,
        )

        assert cleaned is False
        assert channel.sent == []
        assert channel.deleted == []

    asyncio.run(run())


def test_cleanup_unpersisted_ticket_channel_warns_then_deletes_when_row_missing():
    async def run() -> None:
        channel = FakeChannel()

        async def row_exists(channel_id: int | str) -> bool:
            return False

        cleaned = await cleanup_unpersisted_ticket_channel(
            channel,
            owner_id=456,
            ticket_number=7,
            row_exists=row_exists,
        )

        assert cleaned is True
        assert len(channel.sent) == 1
        assert "Ticket creation failed" in channel.sent[0]["content"]
        assert len(channel.deleted) == 1
        assert "owner_id=456" in channel.deleted[0]
        assert "ticket_number=7" in channel.deleted[0]
        assert "channel_id=123" in channel.deleted[0]

    asyncio.run(run())
