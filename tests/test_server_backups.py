from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import discord
import pytest

from stoney_verify import config_history
from stoney_verify.services import server_backup_service as backups

GUILD_ID = 1514374173517152418
VERIFIED_ROLE_ID = 1514374903938154526
BOT_ROLE_ID = 1514374903938154999
CATEGORY_ID = 1514374910000000001
GENERAL_ID = 1514374910000000002
NEWS_ID = 1514374910000000003
VOICE_ID = 1514374910000000004
FORUM_ID = 1514374910000000005
STAGE_ID = 1514374910000000006
MEMBER_ID = 1514374910000000099
CAPTURED_AT = datetime(2026, 11, 18, 12, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Fake guild structure (duck-typed; real discord enums/overwrites where the
# serializer reads library objects)
# ---------------------------------------------------------------------------


class _Obj(SimpleNamespace):
    """SimpleNamespace with identity hash: overwrite dicts key on targets,
    and real discord.py targets are hashable."""

    __hash__ = object.__hash__


def _role(role_id, name, *, position=0, permissions=0, managed=False, default=False, color=0):
    return _Obj(
        id=role_id,
        name=name,
        colour=discord.Colour(color),
        hoist=False,
        mentionable=False,
        position=position,
        permissions=discord.Permissions(permissions),
        managed=managed,
        is_default=lambda default=default: default,
    )


def _fake_guild():
    everyone = _role(GUILD_ID, "@everyone", position=0, permissions=104324673, default=True)
    verified = _role(VERIFIED_ROLE_ID, "Verified", position=12, permissions=1071698660929, color=3066993)
    verified.hoist = True
    bot_role = _role(BOT_ROLE_ID, "Dank Shield", position=20, permissions=8, managed=True)
    member = _Obj(id=MEMBER_ID)  # no hoist/permissions -> member target

    category = SimpleNamespace(
        id=CATEGORY_ID,
        name="🌿 Lobby",
        type=discord.ChannelType.category,
        position=0,
        nsfw=False,
        overwrites={everyone: discord.PermissionOverwrite(view_channel=False)},
    )
    general = SimpleNamespace(
        id=GENERAL_ID,
        name="general",
        type=discord.ChannelType.text,
        category=category,
        position=1,
        topic="Main chat",
        nsfw=False,
        slowmode_delay=0,
        overwrites={
            verified: discord.PermissionOverwrite(view_channel=True, send_messages=False),
            member: discord.PermissionOverwrite(view_channel=True),
        },
    )
    news = SimpleNamespace(
        id=NEWS_ID,
        name="announcements",
        type=discord.ChannelType.news,
        category=category,
        position=2,
        topic=None,
        nsfw=False,
        slowmode_delay=0,
        overwrites={},
    )
    voice = SimpleNamespace(
        id=VOICE_ID,
        name="Lounge Voice",
        type=discord.ChannelType.voice,
        category=category,
        position=3,
        nsfw=False,
        slowmode_delay=0,
        bitrate=64000,
        user_limit=0,
        rtc_region=None,
        video_quality_mode=discord.VideoQualityMode.auto,
        overwrites={},
    )
    forum = SimpleNamespace(
        id=FORUM_ID,
        name="recipes",
        type=discord.ChannelType.forum,
        category=category,
        position=4,
        topic="Posting guidelines…",
        nsfw=False,
        slowmode_delay=0,
        available_tags=[
            SimpleNamespace(name="Vodka", moderated=False, emoji=SimpleNamespace(id=None, name="🍸")),
            SimpleNamespace(name="House", moderated=True, emoji=SimpleNamespace(id=12345, name="house")),
        ],
        default_sort_order=discord.ForumOrderType.creation_date,
        default_layout=discord.ForumLayoutType.list_view,
        default_thread_slowmode_delay=30,
        default_reaction_emoji=SimpleNamespace(id=None, name="🍹"),
        default_auto_archive_duration=1440,
        overwrites={},
    )
    stage = SimpleNamespace(
        id=STAGE_ID,
        name="Live Talk",
        type=discord.ChannelType.stage_voice,
        category=None,
        position=5,
        topic="Weekly live",
        nsfw=False,
        bitrate=64000,
        user_limit=0,
        rtc_region=None,
        video_quality_mode=None,
        overwrites={},
    )
    guild = SimpleNamespace(
        id=GUILD_ID,
        name="The 420 Lobby",
        description=None,
        preferred_locale="en-US",
        icon=SimpleNamespace(key="a1b2c3"),
        banner=None,
        features=["COMMUNITY"],
        verification_level=discord.VerificationLevel.medium,
        explicit_content_filter=discord.ContentFilter.all_members,
        default_notifications=discord.NotificationLevel.only_mentions,
        afk_timeout=300,
        afk_channel=voice,
        system_channel=general,
        rules_channel=None,
        public_updates_channel=None,
        roles=[everyone, verified, bot_role],
        categories=[category],
        channels=[category, general, news, voice, forum, stage],
    )
    return guild


def _snapshot(**overrides):
    document = {
        "schema_version": 1,
        "kind": "dank_server_structure_backup",
        "captured_at": CAPTURED_AT.isoformat(),
        "captured_by": "42",
        "guild": {"source_id": str(GUILD_ID), "name": "The 420 Lobby"},
        "roles": [],
        "categories": [],
        "channels": [],
        "omitted": {"member_overwrites": 0, "stage_channels": 0},
        "dank_config": {"config_history_version_id": None, "note": "test"},
    }
    document.update(overrides)
    return document


# ---------------------------------------------------------------------------
# Serializer
# ---------------------------------------------------------------------------


def test_serializer_matches_the_spec_schema():
    snapshot = backups.serialize_guild_structure(
        _fake_guild(),
        actor_id=42,
        config_history_version_id=77,
        captured_at=CAPTURED_AT,
    )
    assert snapshot["schema_version"] == 1
    assert snapshot["kind"] == "dank_server_structure_backup"
    assert snapshot["captured_at"] == CAPTURED_AT.isoformat()
    assert snapshot["captured_by"] == "42"

    guild = snapshot["guild"]
    assert guild["source_id"] == str(GUILD_ID)
    assert guild["name"] == "The 420 Lobby"
    assert guild["description"] is None
    assert guild["preferred_locale"] == "en-US"
    assert guild["icon_hash"] == "a1b2c3"
    assert guild["banner_hash"] is None
    assert guild["features"] == ["COMMUNITY"]
    assert guild["verification_level"] == 2
    assert guild["explicit_content_filter"] == 2
    assert guild["default_notifications"] == 1
    assert guild["afk_timeout"] == 300
    assert guild["afk_channel_key"] == f"chan:{VOICE_ID}"
    assert guild["system_channel_key"] == f"chan:{GENERAL_ID}"
    assert guild["rules_channel_key"] is None
    assert guild["public_updates_channel_key"] is None

    roles = {row["key"]: row for row in snapshot["roles"]}
    assert roles["role:@everyone"]["is_default"] is True
    assert roles["role:@everyone"]["source_id"] == str(GUILD_ID)
    verified = roles[f"role:{VERIFIED_ROLE_ID}"]
    assert verified["name"] == "Verified"
    assert verified["color"] == 3066993
    assert verified["hoist"] is True
    assert verified["position"] == 12
    # Permissions round-trip losslessly through the raw bitfield string.
    assert discord.Permissions(int(verified["permissions"])) == discord.Permissions(1071698660929)
    assert roles[f"role:{BOT_ROLE_ID}"]["managed"] is True

    [category] = snapshot["categories"]
    assert category["key"] == f"cat:{CATEGORY_ID}"
    # Permission names are discord.py's canonical flag names (view_channel's
    # flag is read_messages) — the same names permission_repair_core emits.
    assert category["overwrites"] == [
        {"target_type": "role", "target_key": "role:@everyone", "allow": [], "deny": ["read_messages"]}
    ]

    channels = {row["key"]: row for row in snapshot["channels"]}
    # The category object in the channel list is not serialized as a channel.
    assert f"cat:{CATEGORY_ID}" not in channels
    general = channels[f"chan:{GENERAL_ID}"]
    assert general["type"] == "text"
    assert general["category_key"] == f"cat:{CATEGORY_ID}"
    assert general["topic"] == "Main chat"
    assert general["forum"] is None
    # The member overwrite is counted, never stored.
    assert general["overwrites"] == [
        {
            "target_type": "role",
            "target_key": f"role:{VERIFIED_ROLE_ID}",
            "allow": ["read_messages"],
            "deny": ["send_messages"],
        }
    ]
    assert channels[f"chan:{NEWS_ID}"]["type"] == "news"
    voice = channels[f"chan:{VOICE_ID}"]
    assert voice["type"] == "voice"
    assert voice["bitrate"] == 64000
    assert voice["video_quality_mode"] == 1
    assert voice["topic"] is None

    forum = channels[f"chan:{FORUM_ID}"]
    assert forum["type"] == "forum"
    assert forum["topic"] == "Posting guidelines…"
    assert forum["forum"]["available_tags"] == [
        {"name": "Vodka", "moderated": False, "emoji": "🍸"},
        # Custom-emoji ids are guild-scoped and non-portable -> dropped.
        {"name": "House", "moderated": True, "emoji": None},
    ]
    assert forum["forum"]["default_sort_order"] == 1
    assert forum["forum"]["default_forum_layout"] == 1
    assert forum["forum"]["default_thread_slowmode_delay"] == 30
    assert forum["forum"]["default_reaction_emoji"] == "🍹"
    assert forum["forum"]["default_auto_archive_duration"] == 1440

    stage = channels[f"chan:{STAGE_ID}"]
    assert stage["type"] == "stage"
    assert stage["category_key"] is None

    assert snapshot["omitted"] == {"member_overwrites": 1, "stage_channels": 1}
    assert snapshot["dank_config"]["config_history_version_id"] == 77
    assert "Config History" in snapshot["dank_config"]["note"]

    # The whole document survives a JSON round trip unchanged.
    assert json.loads(json.dumps(snapshot)) == snapshot


def test_capture_snapshot_uses_fresh_channels_and_links_config_version(monkeypatch):
    guild = _fake_guild()
    fresh = [guild.channels[1]]  # only the general channel, freshly fetched

    async def fetch_channels():
        return list(fresh)

    guild.fetch_channels = fetch_channels

    async def fake_versions(_guild_id, _limit):
        return [{"version_id": 55}]

    monkeypatch.setattr(config_history, "list_config_versions", fake_versions)
    snapshot = asyncio.run(backups.capture_snapshot(guild, actor_id=7))
    assert [row["key"] for row in snapshot["channels"]] == [f"chan:{GENERAL_ID}"]
    assert snapshot["dank_config"]["config_history_version_id"] == 55
    assert snapshot["captured_by"] == "7"


def test_capture_snapshot_config_link_is_best_effort(monkeypatch):
    guild = _fake_guild()

    async def broken_versions(_guild_id, _limit):
        raise RuntimeError("supabase unavailable")

    monkeypatch.setattr(config_history, "list_config_versions", broken_versions)
    snapshot = asyncio.run(backups.capture_snapshot(guild, actor_id=7))
    assert snapshot["dank_config"]["config_history_version_id"] is None
    assert len(snapshot["channels"]) == 5


# ---------------------------------------------------------------------------
# Storage (fake Postgres, modeled on tests/test_dank_membership.py)
# ---------------------------------------------------------------------------


class _FakeCursor:
    def __init__(self, connection):
        self.connection = connection
        self._rows: list[tuple] = []
        self.rowcount = 0

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    @staticmethod
    def _unwrap(value):
        return getattr(value, "obj", value)

    def _metadata(self, row):
        return (
            row["backup_id"],
            row["guild_id"],
            row["created_by"],
            row["label"],
            row["created_at"],
            row["schema_version"],
            row["snapshot_bytes"],
            row["restore_state"],
        )

    def _sorted(self, guild_id):
        rows = [row for row in self.connection.rows if row["guild_id"] == guild_id]
        return sorted(rows, key=lambda row: (row["created_at"], row["backup_id"]), reverse=True)

    def execute(self, sql, params=None):
        normalized = " ".join(sql.split()).lower()
        store = self.connection.rows
        if "limit 0" in normalized:
            self._rows = []
        elif "has_table_privilege" in normalized:
            self._rows = [(True,)]
        elif normalized.startswith("insert into public.dank_server_backups"):
            guild_id, created_by, label, schema_version, snapshot, snapshot_bytes = params
            self.connection.next_id += 1
            backup_id = self.connection.next_id
            created_at = datetime(2026, 11, 18, tzinfo=timezone.utc) + timedelta(seconds=backup_id)
            store.append(
                {
                    "backup_id": backup_id,
                    "guild_id": guild_id,
                    "created_by": created_by,
                    "label": label,
                    "created_at": created_at,
                    "schema_version": schema_version,
                    "snapshot": self._unwrap(snapshot),
                    "snapshot_bytes": snapshot_bytes,
                    "restore_state": None,
                }
            )
            self._rows = [(backup_id, created_at)]
            self.rowcount = 1
        elif normalized.startswith("select backup_id, restore_state from"):
            self._rows = [
                (row["backup_id"], row["restore_state"]) for row in self._sorted(params[0])
            ]
        elif normalized.startswith("delete from public.dank_server_backups") and "any(" in normalized:
            guild_id, backup_ids = params
            before = len(store)
            store[:] = [
                row
                for row in store
                if not (row["guild_id"] == guild_id and row["backup_id"] in set(backup_ids))
            ]
            self.rowcount = before - len(store)
        elif normalized.startswith("select backup_id, guild_id") and "snapshot from" in normalized:
            guild_id, backup_id = params
            match = [
                row
                for row in store
                if row["guild_id"] == guild_id and row["backup_id"] == backup_id
            ]
            self._rows = [self._metadata(match[0]) + (match[0]["snapshot"],)] if match else []
        elif normalized.startswith("select backup_id, guild_id"):
            guild_id, limit = params
            self._rows = [self._metadata(row) for row in self._sorted(guild_id)[:limit]]
        elif normalized.startswith("delete from public.dank_server_backups"):
            guild_id, backup_id = params
            before = len(store)
            store[:] = [
                row
                for row in store
                if not (row["guild_id"] == guild_id and row["backup_id"] == backup_id)
            ]
            self.rowcount = before - len(store)
        elif normalized.startswith("update public.dank_server_backups"):
            payload, guild_id, backup_id = params
            self.rowcount = 0
            for row in store:
                if row["guild_id"] == guild_id and row["backup_id"] == backup_id:
                    row["restore_state"] = self._unwrap(payload)
                    self.rowcount = 1
        else:  # pragma: no cover - guards against silent SQL drift
            raise AssertionError(f"unexpected SQL: {normalized}")

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


class _FakeConnection:
    def __init__(self):
        self.rows: list[dict] = []
        self.next_id = 0

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def cursor(self):
        return _FakeCursor(self)


def _backup_db(monkeypatch) -> _FakeConnection:
    connection = _FakeConnection()
    monkeypatch.setattr(backups, "_connect", lambda: connection)
    return connection


def test_backups_ready_sync(monkeypatch):
    _backup_db(monkeypatch)
    assert backups.backups_ready_sync() is True

    def broken():
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(backups, "_connect", broken)
    assert backups.backups_ready_sync() is False


def test_create_get_list_delete_round_trip(monkeypatch):
    connection = _backup_db(monkeypatch)
    snapshot = _snapshot()
    created = backups.create_backup_sync(GUILD_ID, created_by=42, label="before redesign", snapshot=snapshot)
    assert created["backup_id"] > 0
    assert created["guild_id"] == GUILD_ID
    assert created["created_by"] == 42
    assert created["label"] == "before redesign"
    assert created["schema_version"] == 1
    assert created["restore_state"] is None
    assert created["snapshot"] == snapshot
    expected_bytes = len(json.dumps(snapshot, ensure_ascii=False, sort_keys=True).encode("utf-8"))
    assert created["snapshot_bytes"] == expected_bytes
    assert len(connection.rows) == 1

    fetched = backups.get_backup_sync(GUILD_ID, created["backup_id"])
    assert fetched is not None
    assert fetched["snapshot"] == snapshot
    assert fetched["label"] == "before redesign"

    listed = backups.list_backups_sync(GUILD_ID)
    assert len(listed) == 1
    assert listed[0]["backup_id"] == created["backup_id"]
    # List views never load the snapshot body.
    assert "snapshot" not in listed[0]

    assert backups.delete_backup_sync(GUILD_ID, created["backup_id"]) is True
    assert backups.get_backup_sync(GUILD_ID, created["backup_id"]) is None
    assert backups.delete_backup_sync(GUILD_ID, created["backup_id"]) is False


def test_storage_is_guild_scoped(monkeypatch):
    _backup_db(monkeypatch)
    created = backups.create_backup_sync(GUILD_ID, created_by=42, label="mine", snapshot=_snapshot())
    other_guild = GUILD_ID + 1
    assert backups.get_backup_sync(other_guild, created["backup_id"]) is None
    assert backups.list_backups_sync(other_guild) == ()
    assert backups.delete_backup_sync(other_guild, created["backup_id"]) is False
    assert backups.update_restore_state_sync(other_guild, created["backup_id"], {"status": "running"}) is False
    # The owner's row is untouched by the other guild's attempts.
    fetched = backups.get_backup_sync(GUILD_ID, created["backup_id"])
    assert fetched is not None
    assert fetched["restore_state"] is None


def test_update_restore_state_round_trip(monkeypatch):
    _backup_db(monkeypatch)
    created = backups.create_backup_sync(GUILD_ID, created_by=42, label="", snapshot=_snapshot())
    state = {"status": "running", "plan_mode": "all", "applied_keys": ["role:1"]}
    assert backups.update_restore_state_sync(GUILD_ID, created["backup_id"], state) is True
    fetched = backups.get_backup_sync(GUILD_ID, created["backup_id"])
    assert fetched is not None
    assert fetched["restore_state"] == state
    assert backups.update_restore_state_sync(GUILD_ID, created["backup_id"], None) is True
    fetched = backups.get_backup_sync(GUILD_ID, created["backup_id"])
    assert fetched is not None
    assert fetched["restore_state"] is None


def test_create_enforces_the_per_guild_cap(monkeypatch):
    _backup_db(monkeypatch)
    ids = [
        backups.create_backup_sync(GUILD_ID, created_by=42, label=f"b{i}", snapshot=_snapshot())["backup_id"]
        for i in range(backups.MAX_BACKUPS_PER_GUILD)
    ]
    assert len(backups.list_backups_sync(GUILD_ID)) == backups.MAX_BACKUPS_PER_GUILD
    newest = backups.create_backup_sync(GUILD_ID, created_by=42, label="overflow", snapshot=_snapshot())
    listed = backups.list_backups_sync(GUILD_ID)
    assert len(listed) == backups.MAX_BACKUPS_PER_GUILD
    listed_ids = {row["backup_id"] for row in listed}
    assert ids[0] not in listed_ids  # the oldest was deleted in the same call
    assert newest["backup_id"] in listed_ids
    assert listed[0]["backup_id"] == newest["backup_id"]  # newest first


def test_cap_never_deletes_a_backup_with_a_restore_in_flight(monkeypatch):
    connection = _backup_db(monkeypatch)
    ids = [
        backups.create_backup_sync(GUILD_ID, created_by=42, label=f"b{i}", snapshot=_snapshot())["backup_id"]
        for i in range(backups.MAX_BACKUPS_PER_GUILD)
    ]
    assert backups.update_restore_state_sync(GUILD_ID, ids[0], {"status": "running"}) is True
    backups.create_backup_sync(GUILD_ID, created_by=42, label="overflow-1", snapshot=_snapshot())
    backups.create_backup_sync(GUILD_ID, created_by=42, label="overflow-2", snapshot=_snapshot())
    # The running-restore backup survives past the cap; the next-oldest went.
    assert len(connection.rows) == backups.MAX_BACKUPS_PER_GUILD + 1
    fetched = backups.get_backup_sync(GUILD_ID, ids[0])
    assert fetched is not None
    assert fetched["restore_state"] == {"status": "running"}
    assert backups.get_backup_sync(GUILD_ID, ids[1]) is None


def test_create_refuses_an_oversize_snapshot(monkeypatch):
    connection = _backup_db(monkeypatch)
    monkeypatch.setattr(backups, "MAX_SNAPSHOT_BYTES", 16)
    with pytest.raises(backups.SnapshotTooLargeError):
        backups.create_backup_sync(GUILD_ID, created_by=42, label="big", snapshot=_snapshot())
    assert connection.rows == []


def test_create_rejects_a_bad_guild_id(monkeypatch):
    _backup_db(monkeypatch)
    with pytest.raises(ValueError):
        backups.create_backup_sync(0, created_by=42, label="", snapshot=_snapshot())


def test_async_wrappers(monkeypatch):
    _backup_db(monkeypatch)
    created = backups.create_backup_sync(GUILD_ID, created_by=42, label="async", snapshot=_snapshot())
    listed = asyncio.run(backups.list_backups(GUILD_ID))
    assert [row["backup_id"] for row in listed] == [created["backup_id"]]
    fetched = asyncio.run(backups.get_backup(GUILD_ID, created["backup_id"]))
    assert fetched is not None and fetched["snapshot"] == _snapshot()
    assert asyncio.run(backups.update_restore_state(GUILD_ID, created["backup_id"], {"status": "done"})) is True
    assert asyncio.run(backups.delete_backup(GUILD_ID, created["backup_id"])) is True
    assert asyncio.run(backups.list_backups(GUILD_ID)) == ()
