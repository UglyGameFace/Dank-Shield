from __future__ import annotations

from stoney_verify import guild_config
from stoney_verify.tickets_new import sync_categories


def test_public_env_fallback_wrapper_matches_canonical_policy(monkeypatch) -> None:
    monkeypatch.setattr(guild_config, "public_config_isolation_enabled", lambda: True)
    monkeypatch.setattr(guild_config, "GUILD_ID", 12345)

    assert guild_config.env_fallback_allowed_for_guild(12345) is True
    assert guild_config.env_fallback_allowed_for_guild(67890) is False


def test_cached_config_api_returns_cached_copy_without_db_io(monkeypatch) -> None:
    guild_config.invalidate_guild_config()
    key = guild_config._cache_key(12345)
    guild_config._CONFIG_CACHE[key] = {
        "guild_id": "12345",
        "source": "supabase:guild_configs",
        "staff_role_id": "999",
    }

    cfg = guild_config.get_cached_guild_config(12345)
    assert isinstance(cfg, guild_config.GuildRuntimeConfig)
    assert cfg.staff_role_id == "999"

    cfg["staff_role_id"] = "changed-locally"
    assert guild_config._CONFIG_CACHE[key]["staff_role_id"] == "999"


def test_cached_config_miss_is_isolation_safe(monkeypatch) -> None:
    guild_config.invalidate_guild_config()
    monkeypatch.setattr(guild_config, "public_config_isolation_enabled", lambda: True)
    monkeypatch.setattr(guild_config, "GUILD_ID", 12345)

    cfg = guild_config.get_cached_guild_config(67890)

    assert cfg.source == "unconfigured:cache_miss"
    assert cfg.use_env_fallbacks is False
    for key, value in cfg.items():
        if key.endswith("_id"):
            assert value is None


def test_ticket_sync_imports_native_cached_config_api() -> None:
    assert sync_categories.get_cached_guild_config is guild_config.get_cached_guild_config
