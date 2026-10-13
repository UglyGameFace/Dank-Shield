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
