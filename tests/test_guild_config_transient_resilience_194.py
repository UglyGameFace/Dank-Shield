from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from stoney_verify import guild_config


@pytest.fixture(autouse=True)
def clear_config_cache():
    guild_config._CONFIG_CACHE.clear()
    guild_config._CONFIG_CACHE_TS.clear()
    yield
    guild_config._CONFIG_CACHE.clear()
    guild_config._CONFIG_CACHE_TS.clear()


def test_unavailable_config_is_not_treated_as_configured() -> None:
    cfg = guild_config.GuildRuntimeConfig(
        {"guild_id": "123", "source": "unavailable:db_read_failed"}
    )
    assert cfg.is_unconfigured is True


def test_db_read_failure_is_distinct_from_genuine_unconfigured_guild(monkeypatch) -> None:
    monkeypatch.setattr(guild_config, "get_supabase", lambda: object())
    monkeypatch.setattr(guild_config, "_allow_env_fallback_for_guild", lambda _gid: False)
    monkeypatch.setattr(guild_config, "_is_missing_table_error", lambda _exc: False)

    def fail_read(_name, _executor, max_attempts=guild_config._DB_MAX_ATTEMPTS):
        _ = max_attempts
        raise RuntimeError("Gateway Timeout")

    monkeypatch.setattr(guild_config, "_execute_db_op", fail_read)

    cfg = guild_config._db_get_guild_config_sync(123)

    assert cfg["source"] == "unavailable:db_read_failed"
    assert cfg["use_env_fallbacks"] is False
    assert all(value is None for key, value in cfg.items() if key.endswith("_id"))


@pytest.mark.asyncio
async def test_refresh_preserves_stale_known_good_config_when_db_is_unavailable(monkeypatch) -> None:
    gid = 234
    key = guild_config._cache_key(gid)
    old_timestamp = datetime.now(timezone.utc) - timedelta(minutes=5)
    guild_config._CONFIG_CACHE[key] = {
        "guild_id": str(gid),
        "source": "supabase:guild_configs",
        "automod_block_invites": True,
    }
    guild_config._CONFIG_CACHE_TS[key] = old_timestamp

    async def unavailable(_op_name, _executor):
        return guild_config.GuildRuntimeConfig(
            {
                "guild_id": str(gid),
                "source": "unavailable:db_read_failed",
                "automod_block_invites": False,
            }
        )

    monkeypatch.setattr(guild_config, "_run_db", unavailable)

    cfg = await guild_config.get_guild_config(gid, refresh=True)

    assert cfg["source"] == "supabase:guild_configs"
    assert cfg["automod_block_invites"] is True
    assert guild_config._CONFIG_CACHE[key]["automod_block_invites"] is True
    assert guild_config._CONFIG_CACHE_TS[key] == old_timestamp


@pytest.mark.asyncio
async def test_unavailable_read_without_cache_is_never_cached(monkeypatch) -> None:
    gid = 345
    key = guild_config._cache_key(gid)

    async def unavailable(_op_name, _executor):
        return guild_config.GuildRuntimeConfig(
            {"guild_id": str(gid), "source": "unavailable:supabase_none"}
        )

    monkeypatch.setattr(guild_config, "_run_db", unavailable)

    cfg = await guild_config.get_guild_config(gid, refresh=True)

    assert cfg["source"] == "unavailable:supabase_none"
    assert key not in guild_config._CONFIG_CACHE
    assert key not in guild_config._CONFIG_CACHE_TS


@pytest.mark.asyncio
async def test_genuine_unconfigured_result_remains_cacheable(monkeypatch) -> None:
    gid = 456
    key = guild_config._cache_key(gid)

    async def unconfigured(_op_name, _executor):
        return guild_config.GuildRuntimeConfig(
            {
                "guild_id": str(gid),
                "source": "unconfigured:isolated_public_fallback",
                "use_env_fallbacks": False,
            }
        )

    monkeypatch.setattr(guild_config, "_run_db", unconfigured)

    cfg = await guild_config.get_guild_config(gid, refresh=True)

    assert cfg["source"] == "unconfigured:isolated_public_fallback"
    assert guild_config._CONFIG_CACHE[key]["source"] == cfg["source"]
    assert key in guild_config._CONFIG_CACHE_TS


@pytest.mark.asyncio
async def test_unavailable_write_preserves_previous_cached_truth(monkeypatch) -> None:
    gid = 567
    key = guild_config._cache_key(gid)
    guild_config._CONFIG_CACHE[key] = {
        "guild_id": str(gid),
        "source": "supabase:guild_configs",
        "automod_block_invites": True,
    }
    guild_config._CONFIG_CACHE_TS[key] = datetime.now(timezone.utc)

    async def unavailable(_op_name, _executor):
        return guild_config.GuildRuntimeConfig(
            {
                "guild_id": str(gid),
                "source": "unavailable:db_read_failed",
                "automod_block_invites": False,
            }
        )

    monkeypatch.setattr(guild_config, "_run_db", unavailable)

    cfg = await guild_config.upsert_guild_config(gid, {"automod_block_invites": False})

    assert cfg["source"] == "supabase:guild_configs"
    assert cfg["automod_block_invites"] is True
    assert guild_config._CONFIG_CACHE[key]["automod_block_invites"] is True
