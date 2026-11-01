"""Member write compatibility must reuse full-row schema evidence, not retry errors."""

import asyncio
from types import SimpleNamespace

import pytest

from stoney_verify.members_new import sync_service as sync


VOICE = {
    "in_voice", "voice_channel_id", "voice_channel_name", "voice_muted",
    "voice_deafened", "voice_self_muted", "voice_self_deafened",
    "voice_streaming", "voice_video", "voice_suppressed",
}
REQUIRED = {
    "guild_id": "1", "user_id": "2", "username": "example",
    "display_name": "Example", "role_ids": [], "role_names": [],
    "in_guild": True, "data_health": "ok",
    "synced_at": "2026-11-01T00:00:00Z",
    "created_at": "2026-11-01T00:00:00Z",
    "updated_at": "2026-11-01T00:00:00Z",
}


@pytest.fixture(autouse=True)
def preserve_optional_schema_cache():
    before = dict(sync._OPTIONAL_GUILD_MEMBER_COLUMN_SUPPORT)
    try:
        sync._OPTIONAL_GUILD_MEMBER_COLUMN_SUPPORT.update(
            {name: None for name in before}
        )
        yield
    finally:
        sync._OPTIONAL_GUILD_MEMBER_COLUMN_SUPPORT.clear()
        sync._OPTIONAL_GUILD_MEMBER_COLUMN_SUPPORT.update(before)


def full_row(*, voice: bool = False):
    row = dict(REQUIRED)
    row.update({
        col: None for col in sync._OPTIONAL_GUILD_MEMBER_COLUMNS
        if voice or col not in VOICE
    })
    return row


def test_existing_full_row_avoids_repeated_missing_voice_write_attempts(monkeypatch):
    class Table:
        def select(self, *args):
            assert args == ("*",)
            return self

        def eq(self, *_args):
            return self

        def limit(self, count):
            assert count == 1
            return self

        def execute(self):
            return SimpleNamespace(data=[full_row()])

    class DB:
        def table(self, name):
            assert name == "guild_members"
            return Table()

    existing = sync._guild_members_select_existing_sync(DB(), "1", "2")
    assert existing["user_id"] == "2"
    assert all(sync._OPTIONAL_GUILD_MEMBER_COLUMN_SUPPORT[col] is False for col in VOICE)
    assert sync._OPTIONAL_GUILD_MEMBER_COLUMN_SUPPORT["nickname"] is True

    sent = []

    async def fake_upsert(_sb, payload, on_conflict=True):
        sent.append((dict(payload), on_conflict))

    monkeypatch.setattr(sync, "_guild_members_upsert_async", fake_upsert)
    asyncio.run(sync._guild_members_upsert_safe_async(
        DB(), {"guild_id": "1", "user_id": "2", "nickname": "Nick",
               "in_voice": False, "voice_channel_id": None}
    ))
    assert sent == [({"guild_id": "1", "user_id": "2", "nickname": "Nick"}, True)]


def test_partial_row_never_disables_unknown_optional_columns():
    sync._learn_optional_column_support_from_full_row({"guild_id": "1", "user_id": "2"})
    assert all(v is None for v in sync._OPTIONAL_GUILD_MEMBER_COLUMN_SUPPORT.values())


def test_full_row_with_voice_columns_reenables_after_schema_change():
    sync._learn_optional_column_support_from_full_row(full_row())
    assert sync._OPTIONAL_GUILD_MEMBER_COLUMN_SUPPORT["in_voice"] is False
    sync._learn_optional_column_support_from_full_row(full_row(voice=True))
    assert sync._OPTIONAL_GUILD_MEMBER_COLUMN_SUPPORT["in_voice"] is True
    assert "in_voice" in sync._strip_optional_unsupported_columns({"in_voice": True})


def test_empty_existing_row_does_not_guess_schema():
    class Table:
        def select(self, *_args):
            return self

        def eq(self, *_args):
            return self

        def limit(self, *_args):
            return self

        def execute(self):
            return SimpleNamespace(data=[])

    class DB:
        def table(self, _name):
            return Table()

    assert sync._guild_members_select_existing_sync(DB(), "1", "2") is None
    assert all(v is None for v in sync._OPTIONAL_GUILD_MEMBER_COLUMN_SUPPORT.values())
