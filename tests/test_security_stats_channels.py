from __future__ import annotations

import asyncio

from stoney_verify import security_stats


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


def test_event_writes_are_guild_scoped_and_persisted(monkeypatch) -> None:
    security_stats._STATS_LOCKS.clear()
    state = {
        security_stats.SECURITY_STATS_COUNTS_KEY: {
            "spam_blocked": 4,
            "invites_blocked": 2,
            "timeouts_issued": 1,
            "quarantines": 0,
        }
    }
    writes = []

    async def fake_get_guild_config(guild_id: int, refresh: bool = False):
        assert guild_id == 321
        assert refresh is True
        return dict(state)

    async def fake_upsert_guild_config(guild_id: int, updates):
        assert guild_id == 321
        writes.append(dict(updates))
        state.update(updates)
        return state

    monkeypatch.setattr(security_stats, "get_guild_config", fake_get_guild_config)
    monkeypatch.setattr(security_stats, "upsert_guild_config", fake_upsert_guild_config)

    result = asyncio.run(
        security_stats.record_security_event(
            321,
            spam_blocked=3,
            invites_blocked=1,
            timeouts_issued=2,
        )
    )

    assert result == {
        "spam_blocked": 7,
        "invites_blocked": 3,
        "timeouts_issued": 3,
        "quarantines": 0,
    }
    assert writes == [{security_stats.SECURITY_STATS_COUNTS_KEY: result}]


def test_real_discord_display_creates_visible_locked_voice_channels(monkeypatch) -> None:
    from stoney_verify import spam_guard

    security_stats._DISPLAY_LOCKS.clear()
    security_stats._LAST_REFRESH_AT.clear()

    class FakePermissions:
        manage_channels = True
        manage_roles = True
        administrator = False

    class FakeMember:
        guild_permissions = FakePermissions()

    class FakeRole:
        pass

    class FakeCategoryChannel:
        def __init__(self, channel_id: int, name: str):
            self.id = channel_id
            self.name = name
            self.voice_channels = []
            self.position = None
            self.permission_updates = []

        async def edit(self, *, position: int, reason: str):
            self.position = position

        async def set_permissions(self, role, **kwargs):
            self.permission_updates.append((role, kwargs))

    class FakeVoiceChannel:
        def __init__(self, channel_id: int, name: str, category_id: int):
            self.id = channel_id
            self.name = name
            self.category_id = category_id
            self.edits = []

        async def edit(self, *, name: str, reason: str):
            self.name = name
            self.edits.append((name, reason))

    class FakeGuild:
        def __init__(self):
            self.id = 777
            self.me = FakeMember()
            self.default_role = FakeRole()
            self.categories = []
            self._channels = {}
            self.created_category_overwrites = None
            self._next_id = 1000

        def get_channel(self, channel_id: int):
            return self._channels.get(int(channel_id))

        async def create_category(self, name: str, *, overwrites, reason: str):
            self.created_category_overwrites = overwrites
            category = FakeCategoryChannel(self._next_id, name)
            self._next_id += 1
            self.categories.append(category)
            self._channels[category.id] = category
            return category

        async def create_voice_channel(self, name: str, *, category, reason: str):
            channel = FakeVoiceChannel(self._next_id, name, category.id)
            self._next_id += 1
            category.voice_channels.append(channel)
            self._channels[channel.id] = channel
            return channel

    state = {
        security_stats.SECURITY_STATS_COUNTS_KEY: {
            "spam_blocked": 1284,
            "invites_blocked": 93,
            "timeouts_issued": 38,
            "quarantines": 4,
        }
    }
    writes = []

    async def fake_get_guild_config(guild_id: int, refresh: bool = False):
        assert guild_id == 777
        return dict(state)

    async def fake_upsert_guild_config(guild_id: int, updates):
        assert guild_id == 777
        writes.append(dict(updates))
        state.update(updates)
        return state

    async def fake_get_spam_settings(guild_id: int):
        assert guild_id == 777
        return {"enabled": True}

    monkeypatch.setattr(security_stats.discord, "CategoryChannel", FakeCategoryChannel)
    monkeypatch.setattr(security_stats.discord, "VoiceChannel", FakeVoiceChannel)
    monkeypatch.setattr(security_stats, "get_guild_config", fake_get_guild_config)
    monkeypatch.setattr(security_stats, "upsert_guild_config", fake_upsert_guild_config)
    monkeypatch.setattr(spam_guard, "get_spam_settings", fake_get_spam_settings)

    guild = FakeGuild()
    ok, note = asyncio.run(security_stats.ensure_security_stats_display(guild))

    assert ok is True
    assert "visible but locked" in note
    assert len(guild.categories) == 1
    category = guild.categories[0]
    assert category.name == security_stats.SECURITY_STATS_CATEGORY_NAME
    assert category.position == 0
    assert [channel.name for channel in category.voice_channels] == [
        "🛡️ SpamGuard: ONLINE",
        "🚫 Spam Blocked: 1.28K",
        "🔗 Invites Blocked: 93",
        "⏱️ Timeouts Issued: 38",
        "🔒 Quarantined: 4",
    ]

    overwrite = guild.created_category_overwrites[guild.default_role]
    assert overwrite.view_channel is True
    assert overwrite.connect is False

    saved = writes[-1]
    assert saved[security_stats.SECURITY_STATS_ENABLED_KEY] is True
    assert saved[security_stats.SECURITY_STATS_CATEGORY_ID_KEY] == str(category.id)
    assert set(saved[security_stats.SECURITY_STATS_CHANNEL_IDS_KEY]) == {
        "status",
        "spam_blocked",
        "invites_blocked",
        "timeouts_issued",
        "quarantines",
    }
