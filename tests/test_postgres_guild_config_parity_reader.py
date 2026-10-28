"""Unit tests for opt-in, read-only PG guild-config parity reader."""
from unittest.mock import MagicMock, patch

import pytest

from stoney_verify.postgres_guild_config_parity_reader import (
    compare_guild_config,
    require_guild_config_parity,
)
from stoney_verify.postgres_guild_config_repo import ConfigWriteResult

GID = "123456789012345678"


def pg_result(row, revision=0):
    return ConfigWriteResult(False, revision, row, (), (), "read")


def test_matching_row():
    row = {"guild_id": GID, "settings": {"verified_role_id": "42"}}
    with patch("stoney_verify.postgres_guild_config_parity_reader.GuildConfigPgRepository") as repo:
        repo.return_value.read.return_value = pg_result(row, 4)
        result = compare_guild_config(MagicMock(), GID, row)
    assert result.matches and result.pg_revision == 4


@pytest.mark.parametrize("source,pg,reason", [
    (None, None, "source_missing"),
    ({"guild_id": "999"}, None, "source_identity_mismatch"),
    ({"guild_id": GID}, None, "postgres_missing"),
    ({"guild_id": GID, "settings": {"a": 1}},
     {"guild_id": GID, "settings": {"a": 2}}, "payload_mismatch"),
])
def test_mismatch_fails_closed(source, pg, reason):
    with patch("stoney_verify.postgres_guild_config_parity_reader.GuildConfigPgRepository") as repo:
        repo.return_value.read.return_value = pg_result(pg) if pg else None
        result = compare_guild_config(MagicMock(), GID, source)
        assert result.reason == reason and not result.matches
        with pytest.raises(RuntimeError):
            require_guild_config_parity(MagicMock(), GID, source)


def test_rechecks_before_returning_pg_row():
    row = {"guild_id": GID, "settings": {"a": 1}}
    with patch("stoney_verify.postgres_guild_config_parity_reader.GuildConfigPgRepository") as repo:
        repo.return_value.read.side_effect = [pg_result(row, 1), pg_result(row, 2)]
        with pytest.raises(RuntimeError, match="changed"):
            require_guild_config_parity(MagicMock(), GID, row)


def test_rejects_invalid_guild_id():
    with pytest.raises(ValueError):
        compare_guild_config(MagicMock(), "invalid", {"guild_id": "invalid"})
