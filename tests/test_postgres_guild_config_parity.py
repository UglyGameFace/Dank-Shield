from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

import pytest

from stoney_verify import postgres_guild_config_parity as parity

ROOT = Path(__file__).resolve().parents[1]


def rows():
    return [
        {"id": "alpha", "guild_id": "123", "settings": {"roles": ["1", "2"]}},
        {"id": "beta", "guild_id": "456", "settings": {"roles": ["2", "3"]}},
    ]


def pages(records):
    def fetch(offset, limit):
        return records[offset:offset + limit]
    return fetch


def test_matching_config_rows_are_only_diagnostic_not_cutover():
    initial = rows()
    result = parity.compare_guild_config_sources(
        pages(initial), pages(deepcopy(initial)), page_size=1,
    )
    assert result.matching
    public = result.public_summary()
    assert public["source_rows"] == 2 and public["postgres_rows"] == 2
    assert public["source_digest"] == public["postgres_digest"]
    assert public["cutover_authorized"] is False
    assert "alpha" not in str(public)
    assert "roles" not in str(public)


def test_parity_not_approved_when_pg_config_missing_or_changed():
    source = rows()
    missing = parity.compare_guild_config_sources(
        pages(source), pages(source[:1])
    )
    assert not missing.matching
    assert missing.postgres_rows == 1
    different = deepcopy(source)
    different[1]["settings"]["roles"].append("77")
    result = parity.compare_guild_config_sources(
        pages(source), pages(different),
    )
    assert not result.matching
    assert result.source_digest != result.postgres_digest


def test_source_drift_detected_on_rescan_without_ever_writing():
    source = rows()
    calls = []
    def fetch(offset, size):
        calls.append(offset)
        if len(calls) >= 2:
            source[0]["settings"]["new_feature"] = True
        return source[offset:offset + size]
    result = parity.compare_guild_config_sources(
        fetch, pages(rows()), page_size=100,
    )
    assert not result.matching
    assert not result.unchanged_source
    assert result.cutover_authorized is False


def test_empty_sources_cannot_be_approved():
    r = parity.compare_guild_config_sources(pages([]), pages([]))
    assert not r.matching
    assert r.source_rows == 0 and r.postgres_rows == 0


def test_duplicate_guild_ids_or_incomplete_page_fails_closed():
    dup = rows() + [{"id": "different", "guild_id": "123"}]
    with pytest.raises(RuntimeError, match="duplicate guild"):
        parity.compare_guild_config_sources(pages(dup), pages(dup))
    def too_big(offset, size):
        return rows() * 2
    with pytest.raises(RuntimeError, match="oversized parity page"):
        parity.compare_guild_config_sources(too_big, pages(rows()), page_size=2)


@pytest.mark.parametrize("value", ["", "00", "0", "-5", "1e6", "  ", "1"*21])
def test_bad_guild_identity_rejected(value):
    with pytest.raises(RuntimeError, match="invalid guild identity"):
        parity.compare_guild_config_sources(
            pages([{"guild_id": value}]), pages([]),
        )


def test_page_and_memory_bounds_block_unbounded_audit():
    with pytest.raises(ValueError, match="1..200"):
        parity.compare_guild_config_sources(pages(rows()), pages(rows()), page_size=201)
    too_many = [{"guild_id": str(i + 10000)} for i in range(501)]
    with pytest.raises(RuntimeError, match="first-slice guild limit"):
        parity.compare_guild_config_sources(
            pages(too_many), pages(too_many), page_size=200,
        )
    big = [{"guild_id": "123", "settings": {"x": "A" * (3 * 1024 * 1024)}}]
    with pytest.raises(RuntimeError, match="oversized guild row"):
        parity.compare_guild_config_sources(pages(big), pages(big))


def test_source_reader_is_ordered_and_read_only():
    calls = []
    class Response:
        data = rows()
    class Query:
        def select(self, s):
            calls.append(("select", s))
            return self
        def order(self, s):
            calls.append(("order", s))
            return self
        def range(self, first, last):
            calls.append(("range", first, last))
            return self
        def execute(self):
            calls.append(("execute",))
            return Response()
    class Client:
        def table(self, name):
            calls.append(("table", name))
            return Query()
    result = parity.supabase_page_reader(Client())(0, 2)
    assert result == rows()
    assert calls == [
        ("table", "guild_configs"), ("select", "*"),
        ("order", "guild_id"), ("order", "id"),
        ("range", 0, 1), ("execute",),
    ]


def test_target_reader_is_read_only_and_parameterized():
    calls = []
    class Cursor:
        def __enter__(self):
            return self
        def __exit__(self, *_):
            return False
        def execute(self, statement, args):
            calls.append((statement, args))
        def fetchall(self):
            return [(rows()[0],)]
    class Connection:
        def cursor(self):
            return Cursor()
    got = parity.postgres_page_reader(Connection())(7, 20)
    assert got == rows()[:1]
    assert "select row_payload" in calls[0][0]
    assert calls[0][1] == (20, 7)
    assert not any(x in calls[0][0].lower() for x in (
        "insert", "update", "delete", "drop", "alter",
    ))


def test_no_live_switch_or_monkey_patch_in_parity_tool():
    src = (ROOT / "tools/audit_dankshield_guild_config_parity.py").read_text()
    assert "set transaction isolation level repeatable read, read only" in src
    assert "DANK_MIGRATION_SOURCE_REF" in src
    assert "DANK_MIGRATION_EXPECTED_DB" in src
    assert "SUPABASE_SERVICE_ROLE_KEY" in src
    assert "cutover_authorized" in src
    assert "if __name__ == \"__main__\":" in src
    assert "reset_supabase" not in src
    import stoney_verify.guild_config as live
    assert live._db_upsert_guild_config_sync.__module__ == "stoney_verify.guild_config"
