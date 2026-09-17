from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

from stoney_verify import anti_nuke
from stoney_verify import anti_nuke_hostile_actor_runtime as hostile
from stoney_verify import anti_nuke_reentry_race_runtime as race


class FakeIntegration:
    def __init__(self, integration_id: int = 55) -> None:
        self.id = integration_id
        self.deleted = 0
        self.user = None
        self.application = None

    async def delete(self, *, reason: str) -> None:
        assert "Dank Shield AntiNuke" in reason
        self.deleted += 1


class FakeGuild:
    def __init__(self, guild_id: int = 7) -> None:
        self.id = guild_id
        self.owner_id = 999

    async def integrations(self):
        return []


def _clear_runtime_state() -> None:
    race._RECENT_HOSTILE_READD.clear()  # noqa: SLF001
    race._PENDING_INTEGRATIONS.clear()  # noqa: SLF001


def test_fast_reputation_uses_hot_memory_without_local_or_db(monkeypatch) -> None:
    key = (7, 77)
    old = hostile._MEMORY.get(key)  # noqa: SLF001
    hostile._MEMORY[key] = {  # noqa: SLF001
        "guild_id": 7,
        "user_id": 77,
        "active": True,
        "classification": "confirmed_destructive_actor",
    }

    def should_not_read(*_args, **_kwargs):
        raise AssertionError("hot hostile re-entry must not wait on local/DB refresh")

    monkeypatch.setattr(hostile, "_read_local_record", should_not_read)
    try:
        row = asyncio.run(race._fast_reputation(7, 77))  # noqa: SLF001
        assert row is not None
        assert row["active"] is True
        assert row["user_id"] == 77
    finally:
        if old is None:
            hostile._MEMORY.pop(key, None)  # noqa: SLF001
        else:
            hostile._MEMORY[key] = old  # noqa: SLF001


def test_owner_integration_before_hostile_bot_add_is_held_then_removed(monkeypatch) -> None:
    _clear_runtime_state()
    guild = FakeGuild()
    actor = SimpleNamespace(id=999)
    integration = FakeIntegration()
    entry = SimpleNamespace(target=integration)

    async def settings(_guild_id: int):
        return {
            "antinuke_enabled": True,
            "antinuke_mode": "contain",
            "antinuke_strict_lockdown": False,
        }

    monkeypatch.setattr(anti_nuke, "get_antinuke_settings", settings)
    monkeypatch.setattr(anti_nuke, "_actor_is_owner_or_bot", lambda _g, _a: True)

    result = asyncio.run(race._integration_create_guard(guild, entry, actor))  # noqa: SLF001
    assert "held" in result
    assert integration.deleted == 0

    race._mark_hostile_readd(guild.id, actor.id)  # noqa: SLF001
    cleanup = asyncio.run(race._purge_pending_integrations(guild, actor.id))  # noqa: SLF001
    assert integration.deleted == 1
    assert any("deleted correlated integration" in item for item in cleanup)


def test_owner_integration_after_known_hostile_readd_is_deleted(monkeypatch) -> None:
    _clear_runtime_state()
    guild = FakeGuild()
    actor = SimpleNamespace(id=999)
    integration = FakeIntegration()
    entry = SimpleNamespace(target=integration)

    async def settings(_guild_id: int):
        return {
            "antinuke_enabled": True,
            "antinuke_mode": "contain",
            "antinuke_strict_lockdown": False,
        }

    monkeypatch.setattr(anti_nuke, "get_antinuke_settings", settings)
    monkeypatch.setattr(anti_nuke, "_actor_is_owner_or_bot", lambda _g, _a: True)
    race._mark_hostile_readd(guild.id, actor.id)  # noqa: SLF001

    result = asyncio.run(race._integration_create_guard(guild, entry, actor))  # noqa: SLF001
    assert result == "deleted correlated integration"
    assert integration.deleted == 1


def test_untrusted_integration_creation_is_rolled_back_immediately(monkeypatch) -> None:
    _clear_runtime_state()
    guild = FakeGuild()
    actor = SimpleNamespace(id=123)
    integration = FakeIntegration()
    entry = SimpleNamespace(target=integration)

    async def settings(_guild_id: int):
        return {
            "antinuke_enabled": True,
            "antinuke_mode": "contain",
            "antinuke_strict_lockdown": False,
        }

    monkeypatch.setattr(anti_nuke, "get_antinuke_settings", settings)
    monkeypatch.setattr(anti_nuke, "_actor_is_owner_or_bot", lambda _g, _a: False)
    monkeypatch.setattr(anti_nuke, "_actor_is_configured_trusted", lambda _a, _s: False)

    result = asyncio.run(race._integration_create_guard(guild, entry, actor))  # noqa: SLF001
    assert result == "deleted correlated integration"
    assert integration.deleted == 1


def test_trusted_normal_contain_integration_is_not_deleted(monkeypatch) -> None:
    _clear_runtime_state()
    guild = FakeGuild()
    actor = SimpleNamespace(id=123)
    integration = FakeIntegration()
    entry = SimpleNamespace(target=integration)

    async def settings(_guild_id: int):
        return {
            "antinuke_enabled": True,
            "antinuke_mode": "contain",
            "antinuke_strict_lockdown": False,
        }

    monkeypatch.setattr(anti_nuke, "get_antinuke_settings", settings)
    monkeypatch.setattr(anti_nuke, "_actor_is_owner_or_bot", lambda _g, _a: False)
    monkeypatch.setattr(anti_nuke, "_actor_is_configured_trusted", lambda _a, _s: True)

    result = asyncio.run(race._integration_create_guard(guild, entry, actor))  # noqa: SLF001
    assert "held" in result
    assert integration.deleted == 0


def test_fast_member_join_bans_cached_hostile_without_network_refresh(monkeypatch) -> None:
    key = (7, 77)
    old = hostile._MEMORY.get(key)  # noqa: SLF001
    hostile._MEMORY[key] = {  # noqa: SLF001
        "guild_id": 7,
        "user_id": 77,
        "active": True,
        "classification": "confirmed_destructive_actor",
    }
    guild = FakeGuild()
    member = SimpleNamespace(id=77, guild=guild)
    bans: list[int] = []

    async def settings(_guild_id: int):
        return {"antinuke_enabled": True, "antinuke_mode": "contain"}

    async def ban_identity(_guild, user_id: int, **_kwargs):
        bans.append(user_id)
        return True, "banned hostile identity"

    async def post(*_args, **_kwargs):
        return None

    monkeypatch.setattr(anti_nuke, "get_antinuke_settings", settings)
    monkeypatch.setattr(hostile, "_is_owner", lambda _g, _uid: False)
    monkeypatch.setattr(hostile, "_is_dank_bot", lambda _a, _uid: False)
    monkeypatch.setattr(hostile, "_ban_identity", ban_identity)
    monkeypatch.setattr(hostile, "_post_reputation_incident", post)

    try:
        asyncio.run(race._fast_member_join(member))  # noqa: SLF001
        assert bans == [77]
    finally:
        if old is None:
            hostile._MEMORY.pop(key, None)  # noqa: SLF001
        else:
            hostile._MEMORY[key] = old  # noqa: SLF001


def test_reentry_guard_follows_product_policy_in_post_app_coordinator() -> None:
    coordinator_source = Path(
        "stoney_verify/anti_nuke_runtime_coordinator.py"
    ).read_text(encoding="utf-8")
    product = coordinator_source.index('"product_policy"')
    race_guard = coordinator_source.index('"reentry_race"', product)
    assert product < race_guard

    main_source = Path("main.py").read_text(encoding="utf-8")
    post_install = main_source.index("    install_anti_nuke_post_app(bot)")
    run = main_source.index("    _run_dank_shield()", post_install)
    assert post_install < run
