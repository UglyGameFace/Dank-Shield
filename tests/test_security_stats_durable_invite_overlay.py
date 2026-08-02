from __future__ import annotations

import asyncio
from types import SimpleNamespace

from stoney_verify import durable_invite_stats, security_stats


def test_visible_invite_counter_uses_durable_ledger_as_authoritative_floor(monkeypatch) -> None:
    async def fake_spam_guard_enabled(guild_id: int):
        assert guild_id == 77
        return True

    async def fake_ticket_status_counts(guild_id: int):
        assert guild_id == 77
        return None

    async def fake_durable_count(guild_id: int):
        assert guild_id == 77
        return 9

    monkeypatch.setattr(security_stats, "_spam_guard_enabled", fake_spam_guard_enabled)
    monkeypatch.setattr(security_stats, "_ticket_status_counts", fake_ticket_status_counts)
    monkeypatch.setattr(durable_invite_stats, "read_invites_blocked", fake_durable_count)

    guild = SimpleNamespace(id=77, member_count=5, chunked=False, text_channels=[])
    names = asyncio.run(
        security_stats._display_names_for_guild(
            guild,
            counts={
                "spam_blocked": 4,
                "invites_blocked": 2,
                "timeouts_issued": 1,
                "quarantines": 0,
            },
        )
    )

    assert names["invites_blocked"] == "🔗 Invites Blocked: 9"
    assert names["spam_blocked"] == "🚫 Spam Blocked: 4"
