"""PG18 staging and CAS regression suite for a disposable GitHub CI database.

Not a live database migration. No connection to Aiven, Supabase or Discloud.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import os
from urllib.parse import urlsplit
from uuid import uuid4

import pytest

from stoney_verify.core_data_stage import apply_stage, plan_source
from stoney_verify.postgres_guild_config_repo import (
    GuildConfigPgRepository, seed_from_verified_shadow,
)
from stoney_verify.postgres_guild_config_shadow import (
    import_config_shadow, read_shadow_config,
)
from stoney_verify.postgres_guild_config_parity import (
    compare_guild_config_sources, postgres_page_reader,
)

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_AIVEN_PG18_ISOLATED") != "1",
    reason="requires explicitly opted-in disposable PostgreSQL 18 CI service",
)


def _dsn() -> str:
    dsn = os.environ["DANK_TEST_DATABASE_URL"]
    parsed = urlsplit(dsn)
    assert parsed.scheme in {"postgresql", "postgres"}
    assert parsed.hostname in {"127.0.0.1", "localhost"}
    assert parsed.port == 5432
    assert parsed.path == "/postgres"
    assert parsed.username == "postgres"
    assert os.environ.get("GITHUB_ACTIONS") == "true"
    assert os.environ.get("GITHUB_REPOSITORY") == "UglyGameFace/Dank-Shield"
    return dsn


def _verify_pg18(conn) -> None:
    assert int(conn.execute("show server_version_num").fetchone()[0]) // 10000 == 18


def test_pg18_replay_schema_is_empty_and_private() -> None:
    import psycopg
    with psycopg.connect(_dsn(), autocommit=True) as conn:
        _verify_pg18(conn)
        for relation in (
            "dank_migration.staged_records",
            "dank_core.guild_config_shadow",
            "dank_core.guild_config_state",
        ):
            assert conn.execute("select to_regclass(%s)", (relation,)).fetchone()[0]
        assert conn.execute(
            "select count(*) from pg_catalog.pg_tables where schemaname='public'"
        ).fetchone()[0] == 0
        assert conn.execute(
            "select count(*) from dank_core.guild_config_state"
        ).fetchone()[0] == 0


def test_pg18_staging_shadow_seed_parity_and_voice_isolation() -> None:
    import psycopg

    dsn = _dsn()
    gid = str(900000000000000000 + uuid4().int % 1000000000)
    source = {
        "id": str(uuid4()), "guild_id": gid,
        "updated_at": "2026-10-19T00:00:00+00:00",
        "settings": {"verify_channel_id": "111", "welcome_message": "hi"},
        "config": {"verify_channel_id": "111", "welcome_message": "hi"},
        "metadata": {}, "meta": {},
        "verify_channel_id": "111",
    }

    def fetch(name, keys, offset, size):
        assert name == "guild_configs" and keys == ("id",)
        return [source][offset:offset + size]

    def scan():
        return plan_source(fetch, selected_tables=["guild_configs"])

    with psycopg.connect(dsn, autocommit=True) as conn:
        _verify_pg18(conn)
        conn.execute(
            "create table public.voice_bot_canary "
            "(name text primary key, state text not null)"
        )
        conn.execute(
            "insert into public.voice_bot_canary(name,state) "
            "values ('sentinel','untouched')"
        )
        staged = apply_stage(
            scan(), conn, expected_db="postgres",
            disk_budget_mb=1024, source_reread=scan,
        )
        assert staged["staged_count"] == 1
        shadowed = import_config_shadow(
            conn, expected_database="postgres", disk_budget_mb=1024,
        )
        assert shadowed["guild_count"] == 1
        assert read_shadow_config(conn, int(gid)) == source
        seeded = seed_from_verified_shadow(
            conn, expected_database="postgres", disk_budget_mb=1024,
        )
        assert seeded["seeded_guilds"] == 1

        repo = GuildConfigPgRepository(conn)
        starting = repo.read(gid)
        assert starting is not None and starting.revision == 0
        assert starting.row == source
        updated = repo.patch(
            gid, {"welcome_message": "new"},
            expected={"welcome_message": "hi"}, expected_revision=0,
        )
        assert updated.applied and updated.revision == 1
        assert updated.row["settings"]["verify_channel_id"] == "111"
        assert updated.row["config"]["welcome_message"] == "new"
        assert read_shadow_config(conn, int(gid)) == source
        assert conn.execute(
            "select state from public.voice_bot_canary where name='sentinel'"
        ).fetchone()[0] == "untouched"
        with conn.transaction():
            conn.execute("set transaction isolation level repeatable read, read only")
            drift = compare_guild_config_sources(
                lambda offset, size: [source][offset:offset + size],
                postgres_page_reader(conn),
            )
        assert not drift.matching
        assert drift.cutover_authorized is False


def test_pg18_concurrent_cas_and_stale_revision() -> None:
    import psycopg
    from psycopg.types.json import Jsonb

    dsn = _dsn()
    gid = str(900000000000000000 + uuid4().int % 1000000000)
    initial = {
        "id": str(uuid4()), "guild_id": gid,
        "settings": {"welcome": "hello", "verify_channel_id": "111"},
        "config": {"welcome": "hello", "verify_channel_id": "111"},
        "metadata": {}, "meta": {},
        "verify_channel_id": "111",
    }
    with psycopg.connect(dsn, autocommit=True) as conn:
        _verify_pg18(conn)
        conn.execute(
            "insert into dank_core.guild_config_state(guild_id,row_payload) "
            "values (%s,%s)", (gid, Jsonb(initial)),
        )

    def contender(value):
        with psycopg.connect(dsn, autocommit=True) as conn:
            return GuildConfigPgRepository(conn).patch(
                gid, {"welcome": value}, expected={"welcome": "hello"},
                timestamp=datetime.now(timezone.utc),
            )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(contender, ["first", "second"]))
    assert sum(item.applied for item in results) == 1
    assert sum(item.reason == "compare_mismatch" for item in results) == 1

    with psycopg.connect(dsn, autocommit=True) as conn:
        repo = GuildConfigPgRepository(conn)
        stored = repo.read(gid)
        assert stored is not None and stored.revision == 1
        assert stored.row["settings"]["welcome"] in {"first", "second"}
        assert stored.row["config"]["welcome"] == stored.row["settings"]["welcome"]
        assert stored.row["settings"]["verify_channel_id"] == "111"
        stale = repo.patch(gid, {"extra": True}, expected_revision=0)
        assert not stale.applied and stale.reason == "stale_revision"
        assert repo.read(gid).revision == 1
