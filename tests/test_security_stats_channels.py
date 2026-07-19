from __future__ import annotations

import asyncio
from pathlib import Path

from stoney_verify import security_stats


SOURCE = Path("stoney_verify/security_stats.py").read_text(encoding="utf-8")


def test_compact_stat_formatting_keeps_channel_names_readable() -> None:
    assert security_stats.format_security_stat_count(0) == "0"
    assert security_stats.format_security_stat_count(999) == "999"
    assert security_stats.format_security_stat_count(1_284) == "1.28K"
    assert security_stats.format_security_stat_count(1_730) == "1.73K"
    assert security_stats.format_security_stat_count(15_500) == "15.5K"
    assert security_stats.format_security_stat_count(1_250_000) == "1.25M"


def test_normalization_rejects_negative_or_invalid_counters() -> None:
    normalized = security_stats.normalize_security_stats(
        {
            "spam_blocked": "12",
            "invites_blocked": -9,
            "timeouts_issued": None,
            "quarantines": "3",
            "made_up_metric": 999,
        }
    )
    assert normalized == {
        "spam_blocked": 12,
        "invites_blocked": 0,
        "timeouts_issued": 0,
        "quarantines": 3,
    }


def test_display_uses_only_auditable_protection_stats() -> None:
    names = security_stats._display_names(
        spam_guard_enabled=True,
        counts={
            "spam_blocked": 1284,
            "invites_blocked": 93,
            "timeouts_issued": 38,
            "quarantines": 4,
        },
    )
    assert names == {
        "status": "🛡️ SpamGuard: ONLINE",
        "spam_blocked": "🚫 Spam Blocked: 1.28K",
        "invites_blocked": "🔗 Invites Blocked: 93",
        "timeouts_issued": "⏱️ Timeouts Issued: 38",
        "quarantines": "🔒 Quarantined: 4",
    }
    joined = "\n".join(names.values()).lower()
    assert "bots stopped" not in joined
    assert "raids prevented" not in joined
    assert "users protected" not in joined


def test_completed_spamguard_actions_translate_to_real_counters(monkeypatch) -> None:
    calls = []

    async def fake_record(guild_id: int, **kwargs):
        calls.append((guild_id, kwargs))
        return kwargs

    monkeypatch.setattr(security_stats, "record_security_event", fake_record)

    asyncio.run(
        security_stats.record_spam_guard_action(
            123,
            deleted_messages=7,
            action_taken="timeout:30m",
            quarantine_case=None,
        )
    )
    asyncio.run(
        security_stats.record_spam_guard_action(
            123,
            deleted_messages=2,
            action_taken="quarantine:456",
            quarantine_case={"timeout_applied": True},
        )
    )

    assert calls[0] == (
        123,
        {
            "spam_blocked": 7,
            "timeouts_issued": 1,
            "quarantines": 0,
        },
    )
    assert calls[1] == (
        123,
        {
            "spam_blocked": 2,
            "timeouts_issued": 1,
            "quarantines": 1,
        },
    )


def test_real_discord_display_is_locked_voice_channels_not_an_image() -> None:
    assert "create_voice_channel" in SOURCE
    assert "PermissionOverwrite(view_channel=True, connect=False)" in SOURCE
    assert "SECURITY_STATS_CATEGORY_NAME" in SOURCE
    assert "tasks.loop(minutes=10)" in SOURCE


def test_event_writes_are_guild_scoped_and_persisted() -> None:
    assert "async with _lock_for(_STATS_LOCKS, gid)" in SOURCE
    assert "get_guild_config(gid, refresh=True)" in SOURCE
    assert "upsert_guild_config(gid, {SECURITY_STATS_COUNTS_KEY: counts})" in SOURCE
