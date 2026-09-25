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


def test_metric_registry_is_provider_backed_and_public_picker_safe() -> None:
    assert tuple(security_stats.SECURITY_STATS_METRICS) == (
        security_stats.DEFAULT_SECURITY_STATS_VISIBLE_KEYS
    )

    values = security_stats._metric_values(
        spam_guard_enabled=True,
        member_count=5,
        counts={
            "spam_blocked": 1,
            "invites_blocked": 2,
            "timeouts_issued": 3,
            "quarantines": 4,
        },
        ticket_counts={
            "open_tickets": 5,
            "claimed_tickets": 2,
            "closed_tickets": 9,
        },
    )
    assert set(values) == set(security_stats.SECURITY_STATS_METRICS)
    assert all(
        security_stats.security_stat_metric_description(key)
        for key in security_stats.SECURITY_STATS_METRICS
    )


def test_per_counter_sections_render_requested_ticket_wrappers_exactly() -> None:
    first = security_stats.security_stats_preferences(
        {
            security_stats.SECURITY_STATS_FORMAT_OVERRIDES_KEY: {
                "open_tickets": {
                    "icon": "[🎫]",
                    "label": "Open Tickets",
                    "separator": ": ",
                    "value_template": "[{value}]",
                }
            }
        }
    )
    assert security_stats.render_security_stat_name(
        first,
        "open_tickets",
        "0",
    ) == "[🎫] Open Tickets: [0]"

    second = security_stats.security_stats_preferences(
        {
            security_stats.SECURITY_STATS_FORMAT_OVERRIDES_KEY: {
                "open_tickets": {
                    "icon": "[🎫]",
                    "label": "Open Tickets",
                    "separator": ": ",
                    "value_template": "「{value}」",
                }
            }
        }
    )
    assert security_stats.render_security_stat_name(
        second,
        "open_tickets",
        "0",
    ) == "[🎫] Open Tickets: 「0」"
    assert security_stats.security_stat_name_prefix(
        second,
        "open_tickets",
    ) == "[🎫] Open Tickets: 「"


def test_custom_value_wrapper_keeps_unmodified_design_sections_inherited() -> None:
    prefs = security_stats.security_stats_preferences(
        {
            security_stats.SECURITY_STATS_INHERIT_DESIGN_KEY: True,
            "server_design_studio_options": {
                "theme_id": "gothic_clean",
                "strength": 4,
                "font": "fraktur",
                "separator_id": "pipe_spaced",
                "category_frame_id": "line",
            },
            security_stats.SECURITY_STATS_FORMAT_OVERRIDES_KEY: {
                "open_tickets": {
                    "value_template": "「{value}」",
                }
            },
        }
    )

    state = security_stats.security_stat_format_state(prefs, "open_tickets")
    assert state["custom_parts"] == ("value_template",)

    rendered = security_stats.render_security_stat_name(
        prefs,
        "open_tickets",
        "0",
    )
    assert rendered.endswith(": 「0」")
    assert rendered != "🎫 Open Tickets: 「0」"


def test_counter_format_requires_exactly_one_live_value_token() -> None:
    ok, message, cleaned = security_stats.validate_security_stat_format(
        "open_tickets",
        {
            "icon": "🎫",
            "label": "Open Tickets",
            "separator": ": ",
            "value_template": "「0」",
        },
    )
    assert ok is False
    assert "{value}" in message
    assert cleaned == {}

    ok, preview, cleaned = security_stats.validate_security_stat_format(
        "open_tickets",
        {
            "icon": "🎫",
            "label": "Open Tickets",
            "separator": ": ",
            "value_template": "「{value}」",
        },
    )
    assert ok is True
    assert preview == "🎫 Open Tickets: 「0」"
    assert cleaned["value_template"] == "「{value}」"


def test_design_sync_styles_stable_label_but_never_live_numeric_value() -> None:
    prefs = security_stats.security_stats_preferences(
        {
            security_stats.SECURITY_STATS_INHERIT_DESIGN_KEY: True,
            "server_design_studio_options": {
                "theme_id": "gothic_clean",
                "strength": 4,
                "font": "fraktur",
                "separator_id": "pipe_spaced",
                "category_frame_id": "line",
            },
        }
    )

    rendered = security_stats.render_security_stat_name(
        prefs,
        "open_tickets",
        "0",
    )
    assert rendered.endswith(": 0")
    assert rendered != "🎫 Open Tickets: 0"
    assert "0" == rendered[-1]
    assert len(rendered) <= 100

    category = security_stats.security_stats_category_display_name(prefs)
    assert category
    assert len(category) <= 100


def test_existing_stats_default_to_design_sync_off_until_owner_opts_in() -> None:
    prefs = security_stats.security_stats_preferences({})
    assert prefs["inherit_design"] is False
    assert security_stats.render_security_stat_name(
        prefs,
        "open_tickets",
        "0",
    ) == "🎫 Open Tickets: 0"


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
        "quarantines": "☣️ Quarantined: 4",
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
        "open_tickets": 5,
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
    assert "Server Stats are active" in note
    assert "9" in note
    assert "visible counters" in note
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
        "☣️ Quarantined: 4",
        "🎫 Open Tickets: 3",
        "🙋 Claimed Tickets: 2",
        "✅ Closed Tickets: 218",
    ]

    overwrite = guild.created_category_overwrites[guild.default_role]
    assert overwrite.view_channel is True
    assert overwrite.connect is False

    assert writes[0] == {
        security_stats.SECURITY_STATS_INHERIT_DESIGN_KEY: True
    }
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
            self.position = 0

        async def edit(self, *, name=None, position=None, reason: str):
            if name is not None:
                self.name = name
            if position is not None:
                self.position = position

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

    async def fake_display_names_for_guild(_guild, *, counts, preferences=None):
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
    assert "☣️ Quarantined: 4" in rendered
    assert "🔒 Quarantined: 4" not in rendered
    assert "🎫 Open Tickets: 1" in rendered
    assert "🙋 Claimed Tickets: 1" in rendered
    assert "✅ Closed Tickets: 5" in rendered
    assert writes
    latest_ids = writes[-1][security_stats.SECURITY_STATS_CHANNEL_IDS_KEY]
    assert set(latest_ids) == set(security_stats.STAT_CHANNEL_PREFIXES)


def test_server_stats_preferences_support_custom_category_visibility_labels_and_format() -> None:
    prefs = security_stats.security_stats_preferences(
        {
            security_stats.SECURITY_STATS_CATEGORY_NAME_KEY: "📈 SERVER NUMBERS",
            security_stats.SECURITY_STATS_VISIBLE_KEYS_KEY: [
                "members",
                "invites_blocked",
                "closed_tickets",
                "not-a-real-stat",
            ],
            security_stats.SECURITY_STATS_CUSTOM_LABELS_KEY: {
                "members": "🫂 Lobby Members",
                "invites_blocked": "🧱 Invites Nuked",
                "not-a-real-stat": "ignored",
            },
            security_stats.SECURITY_STATS_NUMBER_STYLE_KEY: "exact",
            security_stats.SECURITY_STATS_PLACEMENT_KEY: "bottom",
        }
    )

    assert prefs == {
        "category_name": "📈 SERVER NUMBERS",
        "visible_keys": ("members", "invites_blocked", "closed_tickets"),
        "labels": {
            "members": "🫂 Lobby Members",
            "invites_blocked": "🧱 Invites Nuked",
        },
        "formats": {},
        "inherit_design": False,
        "design_options": {},
        "number_style": "exact",
        "placement": "bottom",
    }

    names = security_stats._display_names(
        spam_guard_enabled=True,
        member_count=1284,
        counts={"invites_blocked": 12500},
        ticket_counts={"open_tickets": 2, "claimed_tickets": 1, "closed_tickets": 3456},
        preferences=prefs,
    )
    assert names["members"] == "🫂 Lobby Members: 1284"
    assert names["invites_blocked"] == "🧱 Invites Nuked: 12500"
    assert names["closed_tickets"] == "✅ Closed Tickets: 3456"


def test_enabled_missing_stats_category_self_heals_instead_of_silently_stopping(monkeypatch) -> None:
    calls = []

    async def fake_get_guild_config(guild_id: int, refresh: bool = False):
        assert guild_id == 4242
        assert refresh is True
        return {security_stats.SECURITY_STATS_ENABLED_KEY: True}

    async def fake_ensure(guild):
        calls.append(int(guild.id))
        return True, "repaired"

    monkeypatch.setattr(security_stats, "get_guild_config", fake_get_guild_config)
    monkeypatch.setattr(security_stats, "_find_owned_category", lambda _guild, _cfg: None)
    monkeypatch.setattr(security_stats, "ensure_security_stats_display", fake_ensure)

    guild = SimpleNamespace(id=4242)
    assert asyncio.run(security_stats.refresh_security_stats_display(guild, force=True)) is True
    assert calls == [4242]


def test_empty_visible_selection_falls_back_to_safe_full_default() -> None:
    prefs = security_stats.security_stats_preferences(
        {security_stats.SECURITY_STATS_VISIBLE_KEYS_KEY: []}
    )
    assert prefs["visible_keys"] == security_stats.DEFAULT_SECURITY_STATS_VISIBLE_KEYS



def test_hide_failure_stays_tracked_and_is_reported(monkeypatch) -> None:
    security_stats._DISPLAY_LOCKS.clear()

    class FakePermissions:
        manage_channels = True
        manage_roles = True
        administrator = False

    class FakeMember:
        guild_permissions = FakePermissions()

    class FakeRole:
        pass

    class FakeVoiceChannel:
        def __init__(self, channel_id: int, name: str, category_id: int):
            self.id = channel_id
            self.name = name
            self.category_id = category_id

        async def edit(self, *, name: str, reason: str):
            self.name = name

    class FakeCategoryChannel:
        def __init__(self):
            self.id = 660
            self.name = security_stats.SECURITY_STATS_CATEGORY_NAME
            self.position = 0
            self.voice_channels = []
            self.channels = self.voice_channels

        async def set_permissions(self, role, **kwargs):
            return None

        async def edit(self, *, name=None, position=None, reason: str):
            if name is not None:
                self.name = name
            if position is not None:
                self.position = position

    category = FakeCategoryChannel()
    status = FakeVoiceChannel(661, "🛡️ SpamGuard: ONLINE", category.id)
    hidden = FakeVoiceChannel(662, "👥 Members: 10", category.id)
    category.voice_channels.extend([status, hidden])

    class FakeGuild:
        id = 66
        member_count = 10
        me = FakeMember()
        default_role = FakeRole()
        categories = [category]

        @staticmethod
        def get_channel(channel_id: int):
            if int(channel_id) == category.id:
                return category
            if int(channel_id) == status.id:
                return status
            if int(channel_id) == hidden.id:
                return hidden
            return None

        async def create_voice_channel(self, name: str, *, category, reason: str):
            raise AssertionError("no new channel should be needed")

    state = {
        security_stats.SECURITY_STATS_ENABLED_KEY: True,
        security_stats.SECURITY_STATS_CATEGORY_ID_KEY: str(category.id),
        security_stats.SECURITY_STATS_CHANNEL_IDS_KEY: {
            "status": str(status.id),
            "members": str(hidden.id),
        },
        security_stats.SECURITY_STATS_VISIBLE_KEYS_KEY: ["status"],
    }
    writes = []

    async def fake_get_guild_config(guild_id: int, refresh: bool = False):
        return dict(state)

    async def fake_upsert_guild_config(guild_id: int, updates):
        writes.append(dict(updates))
        state.update(updates)
        return dict(state)

    async def fake_names(_guild, *, counts, preferences=None):
        return {
            key: (
                "🛡️ SpamGuard: ONLINE"
                if key == "status"
                else f"{security_stats.DEFAULT_SECURITY_STATS_LABELS[key]}: 0"
            )
            for key in security_stats.DEFAULT_SECURITY_STATS_VISIBLE_KEYS
        }

    async def fake_remove(channel, *, key: str):
        if channel is hidden and key == "members":
            return False
        return channel is None

    monkeypatch.setattr(security_stats.discord, "CategoryChannel", FakeCategoryChannel)
    monkeypatch.setattr(security_stats.discord, "VoiceChannel", FakeVoiceChannel)
    monkeypatch.setattr(security_stats, "get_guild_config", fake_get_guild_config)
    monkeypatch.setattr(security_stats, "upsert_guild_config", fake_upsert_guild_config)
    monkeypatch.setattr(security_stats, "_display_names_for_guild", fake_names)
    monkeypatch.setattr(security_stats, "_remove_hidden_stat_channel", fake_remove)

    ok, note = asyncio.run(security_stats.ensure_security_stats_display(FakeGuild()))

    assert ok is False
    assert "remain tracked" in note
    assert writes[-1][security_stats.SECURITY_STATS_CHANNEL_IDS_KEY] == {
        "status": str(status.id),
        "members": str(hidden.id),
    }



def test_disable_stats_preserves_category_when_it_contains_unowned_channels(monkeypatch) -> None:
    class FakeVoiceChannel:
        def __init__(self, channel_id: int, name: str, category_id: int):
            self.id = channel_id
            self.name = name
            self.category_id = category_id
            self.deleted = False

        async def delete(self, *, reason: str):
            self.deleted = True

    class FakeCategoryChannel:
        def __init__(self):
            self.id = 700
            self.name = security_stats.SECURITY_STATS_CATEGORY_NAME
            self.voice_channels = []
            self.channels = []
            self.deleted = False

        async def delete(self, *, reason: str):
            self.deleted = True

    category = FakeCategoryChannel()
    owned = FakeVoiceChannel(701, "👥 Members: 42", category.id)
    unowned = SimpleNamespace(id=799, name="owner-created-channel")
    category.voice_channels.append(owned)
    category.channels.extend([owned, unowned])

    class FakeGuild:
        id = 77

        def __init__(self):
            self.category = category

        def get_channel(self, channel_id: int):
            if int(channel_id) == category.id:
                return category
            if int(channel_id) == owned.id:
                return owned
            return None

    state = {
        security_stats.SECURITY_STATS_ENABLED_KEY: True,
        security_stats.SECURITY_STATS_CATEGORY_ID_KEY: str(category.id),
        security_stats.SECURITY_STATS_CHANNEL_IDS_KEY: {"members": str(owned.id)},
    }
    writes = []

    async def fake_get_guild_config(guild_id: int, refresh: bool = False):
        assert guild_id == 77
        return dict(state)

    async def fake_upsert_guild_config(guild_id: int, updates):
        writes.append(dict(updates))
        state.update(updates)
        return dict(state)

    cleared = []

    async def fake_clear_guild_config_keys(guild_id: int, keys, *, source: str, actor=None):
        cleared.append((guild_id, tuple(keys), source))
        for key in keys:
            state.pop(key, None)
        return dict(state)

    monkeypatch.setattr(security_stats.discord, "CategoryChannel", FakeCategoryChannel)
    monkeypatch.setattr(security_stats.discord, "VoiceChannel", FakeVoiceChannel)
    monkeypatch.setattr(security_stats, "get_guild_config", fake_get_guild_config)
    monkeypatch.setattr(security_stats, "upsert_guild_config", fake_upsert_guild_config)
    monkeypatch.setattr(security_stats, "clear_guild_config_keys", fake_clear_guild_config_keys)

    ok, _note = asyncio.run(
        security_stats.disable_security_stats_display(FakeGuild(), remove_channels=True)
    )

    assert ok is True
    assert owned.deleted is True
    assert category.deleted is False
    assert writes[-1][security_stats.SECURITY_STATS_ENABLED_KEY] is False
    assert writes[-1][security_stats.SECURITY_STATS_CHANNEL_IDS_KEY] == {}
    assert cleared == [
        (
            77,
            (security_stats.SECURITY_STATS_CATEGORY_ID_KEY,),
            "server_stats.disable",
        )
    ]



def test_category_name_alone_does_not_claim_unrelated_category(monkeypatch) -> None:
    class FakeCategoryChannel:
        def __init__(self, name: str):
            self.id = 550
            self.name = name
            self.voice_channels = []

    monkeypatch.setattr(security_stats.discord, "CategoryChannel", FakeCategoryChannel)

    for name in (
        security_stats.SECURITY_STATS_CATEGORY_NAME,
        "📈 SERVER NUMBERS",
    ):
        category = FakeCategoryChannel(name)

        class FakeGuild:
            categories = [category]

            @staticmethod
            def get_channel(_channel_id: int):
                return None

        cfg = {
            security_stats.SECURITY_STATS_CATEGORY_NAME_KEY: name,
            security_stats.SECURITY_STATS_CATEGORY_ID_KEY: "",
            security_stats.SECURITY_STATS_CHANNEL_IDS_KEY: {},
        }
        assert security_stats._find_owned_category(FakeGuild(), cfg) is None


def test_saved_stats_channel_parent_recovers_custom_category_ownership(monkeypatch) -> None:
    class FakeCategoryChannel:
        def __init__(self):
            self.id = 600
            self.name = "totally-custom-name"
            self.voice_channels = []

    class FakeVoiceChannel:
        def __init__(self, category):
            self.id = 601
            self.name = "anything"
            self.category = category
            self.category_id = category.id

    category = FakeCategoryChannel()
    channel = FakeVoiceChannel(category)
    category.voice_channels.append(channel)

    class FakeGuild:
        categories = [category]

        @staticmethod
        def get_channel(channel_id: int):
            return channel if int(channel_id) == channel.id else None

    monkeypatch.setattr(security_stats.discord, "CategoryChannel", FakeCategoryChannel)
    monkeypatch.setattr(security_stats.discord, "VoiceChannel", FakeVoiceChannel)

    cfg = {
        security_stats.SECURITY_STATS_CATEGORY_NAME_KEY: "another-name",
        security_stats.SECURITY_STATS_CATEGORY_ID_KEY: "",
        security_stats.SECURITY_STATS_CHANNEL_IDS_KEY: {"members": str(channel.id)},
    }
    assert security_stats._find_owned_category(FakeGuild(), cfg) is category


def test_disable_keeps_failed_cleanup_ids_for_safe_retry(monkeypatch) -> None:
    class FakeVoiceChannel:
        def __init__(self, category):
            self.id = 801
            self.name = "👥 Members: 42"
            self.category_id = category.id

    class FakeCategoryChannel:
        def __init__(self):
            self.id = 800
            self.name = security_stats.SECURITY_STATS_CATEGORY_NAME
            self.voice_channels = []
            self.channels = []
            self.deleted = False

        async def delete(self, *, reason: str):
            self.deleted = True

    category = FakeCategoryChannel()
    owned = FakeVoiceChannel(category)
    category.voice_channels.append(owned)
    category.channels.append(owned)

    class FakeGuild:
        id = 88
        categories = [category]

        @staticmethod
        def get_channel(channel_id: int):
            if int(channel_id) == category.id:
                return category
            if int(channel_id) == owned.id:
                return owned
            return None

    state = {
        security_stats.SECURITY_STATS_ENABLED_KEY: True,
        security_stats.SECURITY_STATS_CATEGORY_ID_KEY: str(category.id),
        security_stats.SECURITY_STATS_CHANNEL_IDS_KEY: {"members": str(owned.id)},
    }
    writes = []

    async def fake_get_guild_config(guild_id: int, refresh: bool = False):
        return dict(state)

    async def fake_upsert_guild_config(guild_id: int, updates):
        writes.append(dict(updates))
        state.update(updates)
        return dict(state)

    async def fake_remove(channel, *, key: str):
        assert channel is owned
        assert key == "members"
        return False

    monkeypatch.setattr(security_stats.discord, "CategoryChannel", FakeCategoryChannel)
    monkeypatch.setattr(security_stats.discord, "VoiceChannel", FakeVoiceChannel)
    monkeypatch.setattr(security_stats, "get_guild_config", fake_get_guild_config)
    monkeypatch.setattr(security_stats, "upsert_guild_config", fake_upsert_guild_config)
    monkeypatch.setattr(security_stats, "_remove_hidden_stat_channel", fake_remove)

    ok, note = asyncio.run(
        security_stats.disable_security_stats_display(FakeGuild(), remove_channels=True)
    )

    assert ok is False
    assert "safely retry" in note
    assert category.deleted is False
    assert writes[-1][security_stats.SECURITY_STATS_ENABLED_KEY] is False
    assert writes[-1][security_stats.SECURITY_STATS_CATEGORY_ID_KEY] == str(category.id)
    assert writes[-1][security_stats.SECURITY_STATS_CHANNEL_IDS_KEY] == {
        "members": str(owned.id)
    }


def test_disable_without_removal_preserves_tracking_ids(monkeypatch) -> None:
    class FakeCategoryChannel:
        id = 910
        name = security_stats.SECURITY_STATS_CATEGORY_NAME
        voice_channels = []
        channels = []

    category = FakeCategoryChannel()

    class FakeGuild:
        id = 91
        categories = [category]

        @staticmethod
        def get_channel(channel_id: int):
            return category if int(channel_id) == category.id else None

    state = {
        security_stats.SECURITY_STATS_ENABLED_KEY: True,
        security_stats.SECURITY_STATS_CATEGORY_ID_KEY: str(category.id),
        security_stats.SECURITY_STATS_CHANNEL_IDS_KEY: {"members": "911"},
    }
    writes = []

    async def fake_get_guild_config(guild_id: int, refresh: bool = False):
        return dict(state)

    async def fake_upsert_guild_config(guild_id: int, updates):
        writes.append(dict(updates))
        state.update(updates)
        return dict(state)

    monkeypatch.setattr(security_stats.discord, "CategoryChannel", FakeCategoryChannel)
    monkeypatch.setattr(security_stats, "get_guild_config", fake_get_guild_config)
    monkeypatch.setattr(security_stats, "upsert_guild_config", fake_upsert_guild_config)

    ok, note = asyncio.run(
        security_stats.disable_security_stats_display(FakeGuild(), remove_channels=False)
    )

    assert ok is True
    assert "left in place" in note
    assert writes[-1][security_stats.SECURITY_STATS_CATEGORY_ID_KEY] == str(category.id)
    assert writes[-1][security_stats.SECURITY_STATS_CHANNEL_IDS_KEY] == {"members": "911"}



def test_saved_server_design_requests_coalesced_stats_refresh_only_when_synced(
    monkeypatch,
) -> None:
    security_stats._ACTIVE_DISPLAY_GUILDS.discard(606)
    scheduled: list[int] = []
    guild = SimpleNamespace(id=606)

    async def synced_config(guild_id: int, refresh: bool = False):
        assert guild_id == 606
        assert refresh is True
        return {
            security_stats.SECURITY_STATS_ENABLED_KEY: True,
            security_stats.SECURITY_STATS_INHERIT_DESIGN_KEY: True,
        }

    monkeypatch.setattr(security_stats, "get_guild_config", synced_config)
    monkeypatch.setattr(
        security_stats.bot,
        "get_guild",
        lambda guild_id: guild if int(guild_id) == 606 else None,
    )
    monkeypatch.setattr(
        security_stats,
        "_schedule_security_stats_refresh",
        lambda guild_id: scheduled.append(int(guild_id)),
    )

    assert asyncio.run(
        security_stats.request_security_stats_design_refresh(606)
    ) is True
    assert scheduled == [606]
    assert 606 in security_stats._ACTIVE_DISPLAY_GUILDS

    async def unsynced_config(guild_id: int, refresh: bool = False):
        return {
            security_stats.SECURITY_STATS_ENABLED_KEY: True,
            security_stats.SECURITY_STATS_INHERIT_DESIGN_KEY: False,
        }

    monkeypatch.setattr(security_stats, "get_guild_config", unsynced_config)
    scheduled.clear()
    assert asyncio.run(
        security_stats.request_security_stats_design_refresh(606)
    ) is False
    assert scheduled == []


def test_enabled_security_event_schedules_coalesced_display_refresh(monkeypatch) -> None:
    security_stats._STATS_LOCKS.clear()
    security_stats._ACTIVE_DISPLAY_GUILDS.discard(909)
    scheduled = []
    state = {
        security_stats.SECURITY_STATS_ENABLED_KEY: True,
        security_stats.SECURITY_STATS_COUNTS_KEY: {
            "spam_blocked": 10,
            "invites_blocked": 0,
            "timeouts_issued": 0,
            "quarantines": 0,
        },
    }

    async def fake_get_guild_config(guild_id: int, refresh: bool = False):
        assert guild_id == 909
        return dict(state)

    async def fake_upsert_guild_config(guild_id: int, updates):
        state.update(updates)
        return dict(state)

    monkeypatch.setattr(security_stats, "get_guild_config", fake_get_guild_config)
    monkeypatch.setattr(security_stats, "upsert_guild_config", fake_upsert_guild_config)
    monkeypatch.setattr(
        security_stats,
        "_schedule_security_stats_refresh",
        lambda guild_id: scheduled.append(int(guild_id)),
    )

    result = asyncio.run(
        security_stats.record_security_event(909, spam_blocked=2)
    )

    assert result["spam_blocked"] == 12
    assert 909 in security_stats._ACTIVE_DISPLAY_GUILDS
    assert scheduled == [909]
    security_stats._ACTIVE_DISPLAY_GUILDS.discard(909)


def test_restart_discovery_batches_enabled_stats_guilds(monkeypatch) -> None:
    calls = []

    class FakeQuery:
        def __init__(self):
            self.values = []

        def select(self, columns: str):
            assert columns == "guild_id,settings"
            return self

        def in_(self, column: str, values):
            assert column == "guild_id"
            self.values = list(values)
            calls.append(list(values))
            return self

        def execute(self):
            first = self.values[0]
            return SimpleNamespace(
                data=[
                    {
                        "guild_id": first,
                        "settings": {
                            security_stats.SECURITY_STATS_ENABLED_KEY: True,
                        },
                    }
                ]
            )

    class FakeSupabase:
        def table(self, table_name: str):
            assert table_name == security_stats.GUILD_CONFIG_TABLE
            return FakeQuery()

    fake_bot = SimpleNamespace(
        guilds=[SimpleNamespace(id=guild_id) for guild_id in range(1, 402)]
    )
    security_stats._ACTIVE_DISPLAY_GUILDS.clear()
    monkeypatch.setattr(security_stats, "bot", fake_bot)
    monkeypatch.setattr(security_stats, "get_supabase", lambda: FakeSupabase())

    asyncio.run(security_stats._discover_persisted_stats_guilds())

    assert len(calls) == 3
    assert all(len(batch) <= security_stats._STATS_DISCOVERY_BATCH_SIZE for batch in calls)
    assert security_stats._ACTIVE_DISPLAY_GUILDS == {1, 201, 401}
    security_stats._ACTIVE_DISPLAY_GUILDS.clear()


def test_event_refresh_scheduler_coalesces_one_task_per_active_guild(monkeypatch) -> None:
    class FakeTask:
        def done(self) -> bool:
            return False

    created: list[str] = []

    def fake_create_task(coro, *, name: str):
        coro.close()
        created.append(name)
        return FakeTask()

    security_stats._ACTIVE_DISPLAY_GUILDS.clear()
    security_stats._EVENT_REFRESH_TASKS.clear()
    security_stats._ACTIVE_DISPLAY_GUILDS.add(909)
    monkeypatch.setattr(security_stats.asyncio, "create_task", fake_create_task)

    security_stats._schedule_security_stats_refresh(909)
    first = security_stats._EVENT_REFRESH_TASKS[909]
    security_stats._schedule_security_stats_refresh(909)

    assert security_stats._EVENT_REFRESH_TASKS[909] is first
    assert created == ["security-stats-refresh-909"]

    security_stats._EVENT_REFRESH_TASKS.clear()
    security_stats._ACTIVE_DISPLAY_GUILDS.clear()


def test_periodic_refresh_only_visits_active_server_stats_displays(monkeypatch) -> None:
    async def scenario() -> None:
        refreshed: list[int] = []

        guilds = {
            101: SimpleNamespace(id=101),
            202: SimpleNamespace(id=202),
            303: SimpleNamespace(id=303),
        }

        class FakeBot:
            def get_guild(self, guild_id: int):
                return guilds.get(int(guild_id))

        async def fake_refresh(guild, *, force: bool = False) -> bool:
            assert force is False
            refreshed.append(int(guild.id))
            return True

        security_stats._ACTIVE_DISPLAY_GUILDS.clear()
        security_stats._ACTIVE_DISPLAY_GUILDS.update({101, 303})
        monkeypatch.setattr(security_stats, "bot", FakeBot())
        monkeypatch.setattr(
            security_stats,
            "refresh_security_stats_display",
            fake_refresh,
        )

        await security_stats.refresh_all_security_stats_displays.coro()

        assert set(refreshed) == {101, 303}
        assert len(refreshed) == 2
        security_stats._ACTIVE_DISPLAY_GUILDS.clear()

    asyncio.run(scenario())
