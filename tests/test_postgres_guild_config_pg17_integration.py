"""Real PG17 contract test. DISABLED unless a separate local PG17
test service is deliberately provided. Cannot reach private Discloud.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import os
from pathlib import Path
from urllib.parse import urlparse
import uuid

import pytest

from stoney_verify.postgres_guild_config_repo import GuildConfigPgRepository

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_PG_GUILD_CONFIG_INTEGRATION") != "1",
    reason="requires explicitly provisioned isolated localhost PostgreSQL 17",
)


def test_pg17_atomic_config_revision_and_concurrent_compare_and_swap():
    import psycopg
    from psycopg.types.json import Jsonb

    url = os.environ["DANK_TEST_DATABASE_URL"]
    parsed = urlparse(url)
    # No remote host, no existing Discloud service, no production database.
    assert parsed.hostname in ("localhost", "127.0.0.1")
    assert parsed.path == "/postgres"
    test_guild = str(900000000000000000 + uuid.uuid4().int % 1000000000)

    with psycopg.connect(url) as conn:
        assert conn.execute("show server_version_num").fetchone()[0].startswith("17")
        sql_path = (Path(__file__).resolve().parents[1]
                    / "postgres/migrations/202610130004_guild_config_atomic_state.sql")
        conn.execute(sql_path.read_text("utf-8"))
        initial = {
            "id": str(uuid.uuid4()), "guild_id": test_guild,
            "updated_at": "2026-10-13T00:00:00+00:00",
            "settings": {"welcome": "hello", "verify_channel_id": "101"},
            "config": {"welcome": "hello", "verify_channel_id": "101"},
            "metadata": {}, "meta": {},
            "verify_channel_id": "101"
        }
        conn.execute(
            "insert into dank_core.guild_config_state(guild_id,row_payload) "
            "values (%s,%s)", (test_guild, Jsonb(initial))
        )
        conn.commit()

    def contender(value):
        with psycopg.connect(url) as con:
            repo = GuildConfigPgRepository(con)
            outcome = repo.patch(
                test_guild, {"welcome": value},
                expected={"welcome": "hello"},
                timestamp=datetime.now(timezone.utc)
            )
            return outcome

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(contender, ["a", "b"]))
    assert sum(x.applied for x in results) == 1
    assert sum(x.reason == "compare_mismatch" for x in results) == 1

    with psycopg.connect(url) as conn:
        repo = GuildConfigPgRepository(conn)
        current = repo.read(test_guild)
        assert current and current.revision == 1
        first = current.row["settings"]["welcome"]
        assert first in ("a", "b")
        next_result = repo.patch(test_guild, {"new_key": True}, expected_revision=1)
        assert next_result.applied and next_result.revision == 2
        assert next_result.row["settings"]["welcome"] == first
        unchanged = repo.patch(test_guild, {"new_key": True})
        assert not unchanged.applied
        assert unchanged.revision == 2
        assert repo.read(test_guild).row["config"]["new_key"] is True
    # Intentionally leave fixture row in the disposable localhost test service.


def test_pg17_full_staging_shadow_seed_and_voice_isolation():
    """Exercise real PG JSONB + transactions across all three migration slices.

    Disposable localhost PG17 only; clear test fixtures in the disposable
    instance so hashes/counts cannot be accidentally accepted from older runs.
    """
    import psycopg
    from stoney_verify.core_data_stage import plan_source, apply_stage
    from stoney_verify.postgres_guild_config_shadow import (
        import_config_shadow, read_shadow_config,
    )
    from stoney_verify.postgres_guild_config_repo import seed_from_verified_shadow

    dsn = os.environ["DANK_TEST_DATABASE_URL"]
    parsed = urlparse(dsn)
    assert parsed.hostname in ("localhost", "127.0.0.1")
    assert parsed.path == "/postgres"
    guild = str(900000000000000000 + uuid.uuid4().int % 1000000000)
    pk = str(uuid.uuid4())
    data = [{
        "id": pk, "guild_id": guild,
        "settings": {"verify_channel_id": "111", "welcome_message": "hi"},
        "config": {"verify_channel_id": "111", "welcome_message": "hi"},
        "metadata": {}, "meta": {},
        "verify_channel_id": "111",
        "ticket_prefix": "ticket",
    }]

    def fetch(name, keys, offset, size):
        assert name == "guild_configs" and keys == ("id",)
        return data[offset:offset + size]

    def scan():
        return plan_source(fetch, selected_tables=["guild_configs"])

    with psycopg.connect(dsn) as conn:
        assert conn.execute("show server_version_num").fetchone()[0].startswith("17")
        # All cleanup is confined to the disposable localhost CI database.
        conn.execute(
            "create table if not exists public.voice_data_canary "
            "(name text primary key, state text not null)"
        )
        conn.execute(
            "insert into public.voice_data_canary(name,state) "
            "values ('sentinel','untouched') "
            "on conflict (name) do update set state=excluded.state"
        )
        conn.execute("delete from dank_core.guild_config_state")
        conn.execute("delete from dank_core.guild_config_shadow")
        conn.execute(
            "delete from dank_migration.staged_records "
            "where source_table='guild_configs'"
        )
        conn.commit()

        staged = apply_stage(
            scan(), conn, expected_db="postgres",
            disk_budget_mb=1024, source_reread=scan,
        )
        assert staged["staged_count"] == 1
        conn.commit()
        shadowed = import_config_shadow(
            conn, expected_database="postgres", disk_budget_mb=1024
        )
        assert shadowed["guild_count"] == 1
        conn.commit()
        assert read_shadow_config(conn, int(guild)) == data[0]
        conn.commit()

        seeded = seed_from_verified_shadow(
            conn, expected_database="postgres", disk_budget_mb=1024
        )
        assert seeded["seeded_guilds"] == 1
        conn.commit()
        repo = GuildConfigPgRepository(conn)
        current = repo.read(guild)
        assert current is not None and current.revision == 0
        assert current.row == data[0]
        conn.commit()
        updated = repo.patch(
            guild, {"welcome_message": "new"},
            expected={"welcome_message": "hi"},
            expected_revision=0,
        )
        assert updated.applied and updated.revision == 1
        assert updated.row["verify_channel_id"] == "111"
        assert updated.row["settings"]["verify_channel_id"] == "111"
        conn.commit()

        # The original private voice workload and the imported shadow remain
        # unchanged when the future core repository performs writes.
        assert conn.execute(
            "select state from public.voice_data_canary "
            "where name='sentinel'"
        ).fetchone()[0] == "untouched"
        assert read_shadow_config(conn, int(guild)) == data[0]
