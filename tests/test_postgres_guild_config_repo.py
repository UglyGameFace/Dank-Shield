from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from stoney_verify import postgres_guild_config_repo as pg

ROOT = Path(__file__).resolve().parents[1]
T0 = datetime(2026, 10, 13, 20, 0, tzinfo=timezone.utc)


def row(guild_id="123"):
    return {
        "id": "0dc9310f-d9b9-41aa-9dbd-ebac56c22201",
        "guild_id": guild_id,
        "updated_at": "2026-10-12T00:00:00+00:00",
        "ticket_prefix": "ticket",
        "verify_channel_id": "111",
        "settings": {"verify_channel_id": "111", "welcome_message": "hello"},
        "config": {"verify_channel_id": "111", "welcome_message": "hello"},
        "metadata": {"verify_channel_id": "111", "welcome_message": "hello"},
        "meta": {"verify_channel_id": "111", "welcome_message": "hello"},
    }


class FakeTransaction:
    def __init__(self, db):
        self.db = db

    def __enter__(self):
        self.saved = deepcopy(self.db.rows)
        self.db.in_transaction = True
        return self

    def __exit__(self, typ, *_):
        if typ is not None:
            self.db.rows = self.saved
        self.db.in_transaction = False
        return False


class FakeCursor:
    def __init__(self, db):
        self.db = db
        self.result = []

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def execute(self, sql, params=None):
        self.db.sql.append(sql)
        if sql.startswith("select row_payload, revision from"):
            if sql.endswith("for update"):
                assert self.db.in_transaction
            found = self.db.rows.get(params[0])
            self.result = [(deepcopy(found[0]), found[1])] if found else []
        elif sql.startswith("update dank_core.guild_config_state"):
            assert self.db.in_transaction
            payload, guild, previous = params
            current = self.db.rows[guild]
            assert current[1] == previous
            self.db.rows[guild] = (deepcopy(payload.obj), previous + 1)
            self.result = [(previous + 1,)]
        else:
            raise AssertionError("unexpected PG statement " + sql[:80])
        return self

    def fetchone(self):
        return self.result[0] if self.result else None


class FakeDb:
    def __init__(self, initial=None):
        self.rows = {"123": (deepcopy(initial), 0)} if initial is not None else {}
        self.sql = []
        self.in_transaction = False

    def cursor(self):
        return FakeCursor(self)

    def transaction(self):
        return FakeTransaction(self)


def repo():
    db = FakeDb(row())
    return pg.GuildConfigPgRepository(db), db


def test_read_is_stable_and_does_not_write():
    r, db = repo()
    value = r.read(123)
    assert value.revision == 0
    assert value.row["verify_channel_id"] == "111"
    assert value.applied is False
    assert r.read(456) is None
    assert not any(x.startswith("update ") for x in db.sql)


def test_patch_merges_only_requested_sibling_keys_and_noop_does_not_write():
    r, db = repo()
    updated = r.patch(123, {"welcome_message": "new"}, timestamp=T0)
    assert updated.applied is True
    assert updated.revision == 1
    for name in pg.JSON_COLUMNS:
        assert db.rows["123"][0][name]["welcome_message"] == "new"
        assert db.rows["123"][0][name]["verify_channel_id"] == "111"
    changed = r.patch(123, {"extra_style": "purple"}, timestamp=T0)
    assert changed.revision == 2
    assert r.read(123).row["settings"]["welcome_message"] == "new"
    before = sum(x.startswith("update ") for x in db.sql)
    same = r.patch(123, {"extra_style": "purple"}, timestamp=T0)
    assert same.reason == "no_change"
    assert same.revision == 2
    assert before == sum(x.startswith("update ") for x in db.sql)


def test_compare_and_swap_rejects_stale_settings_and_revision():
    r, _db = repo()
    winner = r.patch(123, {"welcome_message": "new"}, expected={
        "welcome_message": "hello"}, expected_revision=0, timestamp=T0)
    assert winner.applied and winner.revision == 1
    loser = r.patch(123, {"welcome_message": "stale"}, expected={
        "welcome_message": "hello"}, timestamp=T0)
    assert not loser.applied
    assert loser.reason == "compare_mismatch"
    stale = r.patch(123, {"extra_style": "other"}, expected_revision=0, timestamp=T0)
    assert not stale.applied
    assert stale.reason == "stale_revision"
    assert r.read(123).revision == 1


def test_expected_null_means_missing_or_json_null():
    r, db = repo()
    good = r.patch(123, {"feature": 1}, expected={"feature": None}, timestamp=T0)
    assert good.applied
    bad = r.patch(123, {"feature": 2}, expected={"feature": None}, timestamp=T0)
    assert bad.reason == "compare_mismatch"
    assert db.rows["123"][0]["settings"]["feature"] == 1


def test_protected_discord_ids_cannot_be_replaced_by_background_discovery():
    r, _db = repo()
    result = r.patch(
        123, {"verify_channel_id": "222", "new_discovery_key": "safe"},
        mode="runtime_discovery", timestamp=T0,
    )
    assert result.blocked == ("verify_channel_id",)
    assert result.changed == ("new_discovery_key",)
    assert result.applied is True
    assert r.read(123).row["verify_channel_id"] == "111"
    explicit = r.patch(
        123, {"verify_channel_id": "222"}, mode="setup_builder", timestamp=T0
    )
    assert explicit.applied
    assert explicit.row["verify_channel_id"] == "222"


def test_fill_only_sets_missing_not_configured_values():
    r, _db = repo()
    result = r.patch(
        123, {"brand_new_feature": True, "welcome_message": "overwrite"},
        mode="fill_missing", timestamp=T0
    )
    assert result.changed == ("brand_new_feature",)
    assert result.blocked == ("welcome_message",)
    assert result.row["settings"]["welcome_message"] == "hello"


def test_allowlisted_explicit_change_for_one_key():
    r, _db = repo()
    change = r.patch(
        123, {"verify_channel_id": "999"}, mode="fill_missing",
        allow_keys=("verify_channel_id",), timestamp=T0
    )
    assert change.applied
    assert change.row["verify_channel_id"] == "999"


def test_explicit_clear_removes_nested_and_flat_value():
    r, _db = repo()
    result = r.patch(
        123, {}, clear_keys=("verify_channel_id",),
        mode="explicit_override", timestamp=T0
    )
    assert result.applied
    assert result.row["verify_channel_id"] is None
    assert "verify_channel_id" not in result.row["settings"]


def test_null_patch_disallowed_and_fail_closed_before_writes():
    r, db = repo()
    with pytest.raises(ValueError, match="explicit clear_keys"):
        r.patch(123, {"verify_channel_id": None}, timestamp=T0)
    with pytest.raises(ValueError, match="reserved"):
        r.patch(123, {"guild_id": "777"}, timestamp=T0)
    with pytest.raises(ValueError, match="reserved"):
        r.patch(123, {"settings": {}}, timestamp=T0)
    with pytest.raises(ValueError, match="reserved"):
        r.patch(123, {"__config_write_mode": "force"}, timestamp=T0)
    with pytest.raises(ValueError, match="same key"):
        r.patch(123, {"a": 1}, clear_keys=("a",), timestamp=T0)
    assert not any(x.startswith("update ") for x in db.sql)


def test_oversized_patch_blocked_before_update():
    r, db = repo()
    with pytest.raises(ValueError, match="32 KiB"):
        r.patch(123, {"banner": "Z" * (33 * 1024)}, timestamp=T0)
    assert r.read(123).revision == 0
    assert not any(x.startswith("update ") for x in db.sql)


def test_missing_guild_never_silently_created():
    db = FakeDb()
    r = pg.GuildConfigPgRepository(db)
    with pytest.raises(LookupError, match="not seeded"):
        r.patch(123, {"welcome_message": "new"}, timestamp=T0)
    assert db.rows == {}


def test_uncertain_storage_state_is_not_success():
    db = FakeDb(row())
    db.rows["123"] = ({"guild_id": "wrong"}, 0)
    r = pg.GuildConfigPgRepository(db)
    with pytest.raises(RuntimeError, match="integrity"):
        r.patch(123, {"welcome_message": "no"}, timestamp=T0)


def test_no_live_routing_or_monkey_patch_and_schema_is_private():
    sql = (ROOT / "postgres/migrations/202610130004_guild_config_atomic_state.sql").read_text()
    assert "create table if not exists dank_core.guild_config_state" in sql.lower()
    assert "revoke all on table dank_core.guild_config_state from public" in sql.lower()
    assert "drop table" not in sql.lower()
    assert "public.dank_verify_reminder_delivery" not in sql.lower()
    assert not any(x in sql.lower() for x in (
        "delete from", "create or replace function", "vacuum full"))
    import stoney_verify.guild_config as active
    assert "get_supabase" in active._db_get_guild_config_sync.__code__.co_names
    assert active._db_upsert_guild_config_sync.__module__ == "stoney_verify.guild_config"


def test_policy_protected_key_catalog_matches_current_canonical_authority():
    # An independent PG repository must not silently drift from current
    # role/channel and permission protection rules.
    import stoney_verify.guild_config as active
    assert pg.PROTECTED_KEYS == frozenset(active._PROTECTED_CONFIG_KEYS)
