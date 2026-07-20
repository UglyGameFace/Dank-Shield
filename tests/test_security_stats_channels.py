from __future__ import annotations

import asyncio
from types import SimpleNamespace

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


def test_display_uses_only_auditable_protection_and_live_state_stats() -> None:
    names = security_stats._display_names(
        spam_guard_enabled=True,
        member_count=65,
        counts={
            "spam_blocked": 1284,
            "invites_blocked": 93,
            "timeouts_issued": 38,
            "quarantines": 4,
        },
        ticket_counts={
            "open_tickets": 3,
            "claimed_tickets": 2,
            "closed_tickets": 218,
        },
    )
    assert names == {
        "status": "🛡️ SpamGuard: ONLINE",
        "members": "👥 Members: 65",
        "spam_blocked": "🚫 Spam Blocked: 1.28K",
        "invites_blocked": "🔗 Invites Blocked: 93",
        "timeouts_issued": "⏱️ Timeouts Issued: 38",
        "quarantines": "🔒 Quarantined: 4",
        "open_tickets": "🎫 Open Tickets: 3",
        "claimed_tickets": "🙋 Claimed Tickets: 2",
        "closed_tickets": "✅ Closed Tickets: 218",
    }
    joined = "\n".join(names.values()).lower()
    assert "bots stopped" not in joined
    assert "raids prevented" not in joined
    assert "users protected" not in joined


def test_unavailable_authoritative_live_counts_render_as_na_not_false_zero() -> None:
    names = security_stats._display_names(
        spam_guard_enabled=True,
        counts={},
        member_count=None,
        ticket_counts=None,
    )

    assert names["members"] == "👥 Members: N/A"
    assert names["open_tickets"] == "🎫 Open Tickets: N/A"
    assert names["claimed_tickets"] == "🙋 Claimed Tickets: N/A"
    assert names["closed_tickets"] == "✅ Closed Tickets: N/A"


def test_member_count_prefers_discord_total_and_only_uses_complete_cache_fallback() -> None:
    assert security_stats._guild_member_count(SimpleNamespace(member_count=65)) == 65
    assert (
        security_stats._guild_member_count(
            SimpleNamespace(member_count=None, chunked=True, members=[1, 2, 3])
        )
        == 3
    )
    assert (
        security_stats._guild_member_count(
            SimpleNamespace(member_count=None, chunked=False, members=[1, 2, 3])
        )
        is None
    )


def test_ticket_status_query_uses_authoritative_stored_lifecycle_state(monkeypatch) -> None:
    rows = [
        {"status": "open", "claimed_by": None, "assigned_to": None},
        {"status": "open", "claimed_by": "55", "assigned_to": None},
        {"status": "claimed", "claimed_by": "66", "assigned_to": "66"},
        {"status": "closed", "claimed_by": None, "assigned_to": None},
        {"status": "closed", "claimed_by": "77", "assigned_to": "77"},
        {"status": "active", "claimed_by": None, "assigned_to": None},
        {"status": "reopened", "claimed_by": None, "assigned_to": "88"},
        {"status": "deleted", "claimed_by": None, "assigned_to": None},
    ]

    class FakeQuery:
        def __init__(self):
            self.guild_id = None

        def select(self, columns):
            assert columns == "status,claimed_by,assigned_to"
            return self

        def eq(self, key, value):
            assert key == "guild_id"
            self.guild_id = value
            return self

        def execute(self):
            assert self.guild_id == "777"
            return SimpleNamespace(data=rows)

    class FakeSupabase:
        def table(self, name):
            assert name == "tickets"
            return FakeQuery()

    monkeypatch.setattr(security_stats, "get_supabase", lambda: FakeSupabase())

    assert security_stats._query_ticket_status_counts_sync(777) == {
        "open_tickets": 2,
        "claimed_tickets": 3,
        "closed_tickets": 2,
    }


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
            self.member_count = 65
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

    async def fake_spam_guard_enabled(guild_id: int):
        assert guild_id == 777
        return True

    async def fake_ticket_status_counts(guild_id: int):
        assert guild_id == 777
        return {
            "open_tickets": 3,
            "claimed_tickets": 2,
            "closed_tickets": 218,
        }

    monkeypatch.setattr(security_stats.discord, "CategoryChannel", FakeCategoryChannel)
    monkeypatch.setattr(security_stats.discord, "VoiceChannel", FakeVoiceChannel)
    monkeypatch.setattr(security_stats, "get_guild_config", fake_get_guild_config)
    monkeypatch.setattr(security_stats, "upsert_guild_config", fake_upsert_guild_config)
    monkeypatch.setattr(security_stats, "_spam_guard_enabled", fake_spam_guard_enabled)
    monkeypatch.setattr(security_stats, "_ticket_status_counts", fake_ticket_status_counts)

    guild = FakeGuild()
    ok, note = asyncio.run(security_stats.ensure_security_stats_display(guild))

    assert ok is True
    assert "Live Dank Shield stats" in note
    assert "visible but locked" in note
    assert len(guild.categories) == 1
    category = guild.categories[0]
    assert category.name == security_stats.SECURITY_STATS_CATEGORY_NAME
    assert category.position == 0
    assert [channel.name for channel in category.voice_channels] == [
        "🛡️ SpamGuard: ONLINE",
        "👥 Members: 65",
        "🚫 Spam Blocked: 1.28K",
        "🔗 Invites Blocked: 93",
        "⏱️ Timeouts Issued: 38",
        "🔒 Quarantined: 4",
        "🎫 Open Tickets: 3",
        "🙋 Claimed Tickets: 2",
        "✅ Closed Tickets: 218",
    ]

    overwrite = guild.created_category_overwrites[guild.default_role]
    assert overwrite.view_channel is True
    assert overwrite.connect is False

    saved = writes[-1]
    assert saved[security_stats.SECURITY_STATS_ENABLED_KEY] is True
    assert saved[security_stats.SECURITY_STATS_CATEGORY_ID_KEY] == str(category.id)
    assert set(saved[security_stats.SECURITY_STATS_CHANNEL_IDS_KEY]) == set(
        security_stats.STAT_CHANNEL_PREFIXES
    )


def test_refresh_repairs_new_stats_channels_for_existing_opted_in_display(monkeypatch) -> None:
    security_stats._DISPLAY_LOCKS.clear()
    security_stats._LAST_REFRESH_AT.clear()

    class FakeCategoryChannel:
        def __init__(self):
            self.id = 900
            self.name = security_stats.SECURITY_STATS_CATEGORY_NAME
            self.voice_channels = []

    class FakeVoiceChannel:
        def __init__(self, channel_id: int, name: str, category_id: int):
            self.id = channel_id
            self.name = name
            self.category_id = category_id

        async def edit(self, *, name: str, reason: str):
            self.name = name

    class FakeGuild:
        def __init__(self):
            self.id = 888
            self.member_count = 10
            self.category = FakeCategoryChannel()
            self.categories = [self.category]
            self._channels = {self.category.id: self.category}
            self._next_id = 1000

        def get_channel(self, channel_id: int):
            return self._channels.get(int(channel_id))

        async def create_voice_channel(self, name: str, *, category, reason: str):
            channel = FakeVoiceChannel(self._next_id, name, category.id)
            self._next_id += 1
            category.voice_channels.append(channel)
            self._channels[channel.id] = channel
            return channel

    guild = FakeGuild()
    old_names = {
        "status": "🛡️ SpamGuard: ONLINE",
        "spam_blocked": "🚫 Spam Blocked: 1",
        "invites_blocked": "🔗 Invites Blocked: 2",
        "timeouts_issued": "⏱️ Timeouts Issued: 3",
        "quarantines": "🔒 Quarantined: 4",
    }
    saved_ids = {}
    for index, (key, name) in enumerate(old_names.items(), start=910):
        channel = FakeVoiceChannel(index, name, guild.category.id)
        guild.category.voice_channels.append(channel)
        guild._channels[index] = channel
        saved_ids[key] = str(index)

    state = {
        security_stats.SECURITY_STATS_ENABLED_KEY: True,
        security_stats.SECURITY_STATS_CATEGORY_ID_KEY: str(guild.category.id),
        security_stats.SECURITY_STATS_CHANNEL_IDS_KEY: saved_ids,
        security_stats.SECURITY_STATS_COUNTS_KEY: {
            "spam_blocked": 1,
            "invites_blocked": 2,
            "timeouts_issued": 3,
            "quarantines": 4,
        },
    }
    writes = []

    async def fake_get_guild_config(guild_id: int, refresh: bool = False):
        assert guild_id == 888
        return dict(state)

    async def fake_upsert_guild_config(guild_id: int, updates):
        writes.append(dict(updates))
        state.update(updates)
        return state

    async def fake_display_names_for_guild(_guild, *, counts):
        return security_stats._display_names(
            spam_guard_enabled=True,
            member_count=10,
            counts=counts,
            ticket_counts={
                "open_tickets": 1,
                "claimed_tickets": 1,
                "closed_tickets": 5,
            },
        )

    monkeypatch.setattr(security_stats.discord, "CategoryChannel", FakeCategoryChannel)
    monkeypatch.setattr(security_stats.discord, "VoiceChannel", FakeVoiceChannel)
    monkeypatch.setattr(security_stats, "get_guild_config", fake_get_guild_config)
    monkeypatch.setattr(security_stats, "upsert_guild_config", fake_upsert_guild_config)
    monkeypatch.setattr(
        security_stats,
        "_display_names_for_guild",
        fake_display_names_for_guild,
    )

    changed = asyncio.run(
        security_stats.refresh_security_stats_display(guild, force=True)
    )

    assert changed is True
    rendered = {channel.name for channel in guild.category.voice_channels}
    assert "👥 Members: 10" in rendered
    assert "🎫 Open Tickets: 1" in rendered
    assert "🙋 Claimed Tickets: 1" in rendered
    assert "✅ Closed Tickets: 5" in rendered
    assert writes
    latest_ids = writes[-1][security_stats.SECURITY_STATS_CHANNEL_IDS_KEY]
    assert set(latest_ids) == set(security_stats.STAT_CHANNEL_PREFIXES)
