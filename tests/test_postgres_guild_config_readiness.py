"""Offline tests for the read-only PostgreSQL guild-state readiness gate."""
from __future__ import annotations

from hashlib import sha256

import pytest

from stoney_verify.core_data_stage import canonical_json
from stoney_verify.postgres_guild_config_readiness import inspect_guild_config_state


class Cursor:
    def __init__(self, connection):
        self.connection = connection

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def execute(self, sql, params):
        assert sql.lstrip().lower().startswith("select ")
        assert params == (self.connection.expected + 1,)
        self.connection.queries.append(sql)
        self.rows = (self.connection.shadow if "guild_config_shadow" in sql
                     else self.connection.state)

    def fetchall(self):
        return self.rows


class Connection:
    def __init__(self, shadow, state, expected=1):
        self.shadow, self.state, self.expected = shadow, state, expected
        self.queries = []

    def cursor(self):
        return Cursor(self)


def fixture_rows():
    row = {"guild_id": "123456789", "settings": {"verified_role_id": "999"}}
    digest = sha256(canonical_json(row).encode("utf-8")).hexdigest()
    return [("123456789", row, digest)], [("123456789", dict(row), 0)]


def test_matching_rows_do_not_authorize_production():
    shadow, state = fixture_rows()
    conn = Connection(shadow, state)
    result = inspect_guild_config_state(conn, expected_guilds=1)
    assert result["guild_configs_verified"] is True
    assert result["production_cutover_authorized"] is False
    assert len(conn.queries) == 2


@pytest.mark.parametrize("mutation,reason", [
    ("shadow_hash", "invalid_shadow_row"),
    ("state_payload", "state_payload_mismatch"),
    ("revision", "invalid_revision"),
    ("missing_state", "state_count_mismatch"),
    ("extra_state", "state_count_mismatch"),
])
def test_mismatch_fails_closed(mutation, reason):
    shadow, state = fixture_rows()
    if mutation == "shadow_hash":
        gid, row, _ = shadow[0]
        shadow[0] = (gid, row, "0" * 64)
    elif mutation == "state_payload":
        gid, row, rev = state[0]
        state[0] = (gid, {**row, "settings": {}}, rev)
    elif mutation == "revision":
        gid, row, _ = state[0]
        state[0] = (gid, row, -1)
    elif mutation == "missing_state":
        state.clear()
    else:
        state.append(("222222222", {"guild_id": "222222222", "settings": {}}, 0))
    result = inspect_guild_config_state(Connection(shadow, state), expected_guilds=1)
    assert result["guild_configs_verified"] is False
    assert reason in result["errors"]
    assert result["production_cutover_authorized"] is False


@pytest.mark.parametrize("count", [0, -1, 501])
def test_rejects_unsafe_expected_count(count):
    with pytest.raises(ValueError):
        inspect_guild_config_state(Connection([], []), expected_guilds=count)
