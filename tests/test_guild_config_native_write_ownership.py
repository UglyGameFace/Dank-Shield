from __future__ import annotations

import asyncio
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from stoney_verify import guild_config
from stoney_verify.commands_ext import public_setup_config_writer
from stoney_verify.commands_ext import public_setup_group
from stoney_verify.commands_ext import public_setup_recovery


class _FakeQuery:
    def __init__(self, store: dict[str, dict[str, Any]], table: str, writes: list[dict[str, Any]]) -> None:
        self.store = store
        self.table = table
        self.writes = writes
        self._guild_id = ""
        self._payload: dict[str, Any] | None = None
        self._mode = "select"

    def select(self, *_args: Any):
        self._mode = "select"
        return self

    def eq(self, _key: str, value: Any):
        self._guild_id = str(value)
        return self

    def limit(self, *_args: Any):
        return self

    def update(self, payload: dict[str, Any]):
        self._mode = "update"
        self._payload = deepcopy(payload)
        return self

    def upsert(self, payload: dict[str, Any], **_kwargs: Any):
        self._mode = "upsert"
        self._payload = deepcopy(payload)
        self._guild_id = str(payload.get("guild_id") or self._guild_id)
        return self

    def execute(self):
        table_store = self.store.setdefault(self.table, {})
        if self._mode == "select":
            row = table_store.get(self._guild_id)
            return SimpleNamespace(data=[deepcopy(row)] if row is not None else [])

        assert self._payload is not None
        self.writes.append(deepcopy(self._payload))
        current = deepcopy(table_store.get(self._guild_id) or {})
        current.update(deepcopy(self._payload))
        table_store[self._guild_id] = current
        return SimpleNamespace(data=[deepcopy(current)])


class _FakeSupabase:
    def __init__(self, store: dict[str, dict[str, Any]], writes: list[dict[str, Any]]) -> None:
        self.store = store
        self.writes = writes

    def table(self, name: str) -> _FakeQuery:
        return _FakeQuery(self.store, name, self.writes)


class _FakeGuild:
    def __init__(self, guild_id: int) -> None:
        self.id = guild_id
        self.roles: list[Any] = []
        self.text_channels: list[Any] = []
        self.categories: list[Any] = []

    def get_role(self, _role_id: int):
        return None

    def get_channel(self, _channel_id: int):
        return None


def _split_brain_row() -> dict[str, Any]:
    current = {
        "voice_verification_enabled": False,
        "verification_allows_voice": False,
        "vc_verify_channel_id": "111",
        "vc_verify_queue_channel_id": "222",
    }
    stale = {
        "voice_verification_enabled": True,
        "verification_allows_voice": True,
        "vc_verify_channel_id": "111",
        "vc_verify_queue_channel_id": "222",
    }
    return {
        "guild_id": "1514374173517152418",
        "voice_verification_enabled": False,
        "verification_allows_voice": False,
        "vc_verify_channel_id": "111",
        "vc_verify_queue_channel_id": "222",
        "settings": deepcopy(current),
        "config": deepcopy(stale),
        "metadata": deepcopy(stale),
        "meta": deepcopy(stale),
    }


def _install_fake_supabase(monkeypatch, row: dict[str, Any]) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    store = {guild_config.GUILD_CONFIG_TABLE: {str(row["guild_id"]): deepcopy(row)}}
    writes: list[dict[str, Any]] = []
    fake = _FakeSupabase(store, writes)
    monkeypatch.setattr(guild_config, "get_supabase", lambda: fake)
    monkeypatch.setattr(guild_config, "reset_supabase", lambda: None)
    guild_config.invalidate_guild_config()
    return store, writes


def test_canonical_writer_updates_flat_and_all_json_shapes_atomically(monkeypatch) -> None:
    row = _split_brain_row()
    store, writes = _install_fake_supabase(monkeypatch, row)

    saved = guild_config.upsert_guild_config_sync(
        int(row["guild_id"]),
        {
            "voice_verification_enabled": False,
            "verification_allows_voice": False,
            "__config_write_mode": "setup_builder",
            "__config_write_source": "/dank setup feature picker",
            "__config_write_invalidate_completion": True,
        },
    )

    assert len(writes) == 1
    payload = writes[0]
    assert payload["voice_verification_enabled"] is False
    for bucket in ("settings", "config", "metadata", "meta"):
        assert payload[bucket]["voice_verification_enabled"] is False
        assert payload[bucket]["verification_allows_voice"] is False
    persisted = store[guild_config.GUILD_CONFIG_TABLE][str(row["guild_id"])]
    for bucket in ("settings", "config", "metadata", "meta"):
        assert persisted[bucket]["voice_verification_enabled"] is False
    assert saved["voice_verification_enabled"] is False


def test_direct_canonical_writes_preserve_historical_overwrite_semantics(monkeypatch) -> None:
    row = _split_brain_row()
    row["staff_role_id"] = "111"
    for bucket in ("settings", "config", "metadata", "meta"):
        row[bucket]["staff_role_id"] = "111"
    _store, writes = _install_fake_supabase(monkeypatch, row)

    saved = guild_config.upsert_guild_config_sync(
        int(row["guild_id"]),
        {"staff_role_id": "999"},
    )

    assert len(writes) == 1
    payload = writes[0]
    assert payload["staff_role_id"] == "999"
    for bucket in ("settings", "config", "metadata", "meta"):
        assert payload[bucket]["staff_role_id"] == "999"
    assert saved["staff_role_id"] == "999"


def test_explicit_fill_missing_mode_blocks_protected_reassignment_without_write(monkeypatch) -> None:
    row = _split_brain_row()
    row["staff_role_id"] = "111"
    for bucket in ("settings", "config", "metadata", "meta"):
        row[bucket]["staff_role_id"] = "111"
    _store, writes = _install_fake_supabase(monkeypatch, row)

    saved = guild_config.upsert_guild_config_sync(
        int(row["guild_id"]),
        {
            "staff_role_id": "999",
            "__config_write_mode": "fill_missing",
            "__config_write_source": "runtime test",
        },
    )

    assert writes == []
    assert saved["staff_role_id"] == "111"


def test_canonical_clear_removes_flat_and_all_json_shapes(monkeypatch) -> None:
    row = _split_brain_row()
    store, writes = _install_fake_supabase(monkeypatch, row)

    guild_config.clear_guild_config_keys_sync(
        int(row["guild_id"]),
        {"vc_verify_channel_id", "vc_verify_queue_channel_id"},
        source="/dank setup resource reconciliation",
    )

    assert len(writes) == 1
    payload = writes[0]
    assert payload["vc_verify_channel_id"] is None
    assert payload["vc_verify_queue_channel_id"] is None
    for bucket in ("settings", "config", "metadata", "meta"):
        assert "vc_verify_channel_id" not in payload[bucket]
        assert "vc_verify_queue_channel_id" not in payload[bucket]
    persisted = store[guild_config.GUILD_CONFIG_TABLE][str(row["guild_id"])]
    assert persisted["vc_verify_channel_id"] is None
    for bucket in ("settings", "config", "metadata", "meta"):
        assert "vc_verify_channel_id" not in persisted[bucket]
        assert "vc_verify_queue_channel_id" not in persisted[bucket]


def test_runtime_discovery_natively_purges_all_invalid_saved_ids(monkeypatch) -> None:
    row = _split_brain_row()
    row["staff_role_id"] = "999"
    row["allow_runtime_discovery"] = False
    row["use_env_fallbacks"] = False
    for bucket in ("settings", "config", "metadata", "meta"):
        row[bucket].update(
            {
                "staff_role_id": "999",
                "allow_runtime_discovery": False,
                "use_env_fallbacks": False,
            }
        )
    store, writes = _install_fake_supabase(monkeypatch, row)
    guild = _FakeGuild(int(row["guild_id"]))

    discovered = asyncio.run(guild_config.discover_runtime_guild_config(guild))

    expected_invalid = {
        "staff_role_id": "999",
        "vc_verify_channel_id": "111",
        "vc_verify_queue_channel_id": "222",
    }
    assert discovered["staff_role_id"] is None
    assert discovered["vc_verify_channel_id"] is None
    assert discovered["vc_verify_queue_channel_id"] is None
    assert discovered["invalid_saved_config_ids"] == expected_invalid
    assert len(writes) == 1
    payload = writes[0]
    for key in expected_invalid:
        assert payload[key] is None
        for bucket in ("settings", "config", "metadata", "meta"):
            assert key not in payload[bucket]
    persisted = store[guild_config.GUILD_CONFIG_TABLE][str(row["guild_id"])]
    for key in expected_invalid:
        assert persisted[key] is None
        for bucket in ("settings", "config", "metadata", "meta"):
            assert key not in persisted[bucket]


def test_public_setup_writer_is_a_compatibility_facade(monkeypatch) -> None:
    seen: dict[str, Any] = {}

    def fake_sync(guild_id: int, patch: dict[str, Any]):
        seen["guild_id"] = guild_id
        seen["patch"] = dict(patch)
        return guild_config.GuildRuntimeConfig({"guild_id": str(guild_id), **patch})

    monkeypatch.setattr(guild_config, "upsert_guild_config_sync", fake_sync)

    result = public_setup_config_writer.upsert_guild_config_sync(
        123,
        {"ticket_category_id": "456"},
    )

    assert seen["guild_id"] == 123
    assert seen["patch"]["ticket_category_id"] == "456"
    assert seen["patch"]["__config_write_mode"] == "setup_builder"
    assert seen["patch"]["__config_write_invalidate_completion"] is True
    assert result["ticket_category_id"] == "456"


def test_setup_group_callbacks_are_bound_to_canonical_facade() -> None:
    assert public_setup_group._upsert_config_sync is public_setup_config_writer.upsert_guild_config_sync
    assert public_setup_group._upsert_config is public_setup_config_writer.upsert_guild_config


def test_commands_bootstrap_binds_writer_before_modules_can_copy_setup_callback() -> None:
    commands_text = Path("stoney_verify/commands.py").read_text(encoding="utf-8")
    binding = commands_text.index("public_setup_config_writer as _public_setup_config_writer")
    welcome_import = commands_text.index("public_welcome_card_studio")
    exit_import = commands_text.index("public_exit_compact_surface")

    assert binding < welcome_import
    assert binding < exit_import
    assert "Canonical guild-config writer binding failed during command bootstrap." in commands_text


def test_setup_recovery_config_mutations_delegate_to_canonical_owner(monkeypatch) -> None:
    calls: list[tuple[str, Any]] = []
    snapshot = {"config": {"ticket_category_id": "111"}}

    monkeypatch.setattr(
        public_setup_recovery,
        "_fetch_config_row_sync",
        lambda guild_id: (
            "guild_configs",
            {"guild_id": str(guild_id), "settings": {}},
            "",
        ),
    )

    def fake_upsert(guild_id: int, patch: dict[str, Any]):
        calls.append(("upsert", (guild_id, deepcopy(patch))))
        return guild_config.GuildRuntimeConfig({"guild_id": str(guild_id), **patch})

    def fake_clear(guild_id: int, keys: Any, *, source: str, actor: Any = None):
        calls.append(("clear", (guild_id, tuple(keys), source, actor)))
        return guild_config.GuildRuntimeConfig({"guild_id": str(guild_id)})

    monkeypatch.setattr(public_setup_recovery, "upsert_guild_config_sync", fake_upsert)
    monkeypatch.setattr(public_setup_recovery, "clear_guild_config_keys_sync", fake_clear)

    table = public_setup_recovery._write_config_patch_sync(
        123,
        {
            "ticket_category_id": None,
            "verification_enabled": True,
        },
        snapshot,
    )

    assert table == "guild_configs"
    assert [kind for kind, _payload in calls] == ["upsert", "clear", "upsert"]

    first_upsert = calls[0][1][1]
    assert first_upsert["last_setup_snapshot"] == snapshot
    assert first_upsert["__config_write_mode"] == "explicit_override"
    assert first_upsert["__config_write_source"] == "public_setup_recovery"

    clear_call = calls[1][1]
    assert clear_call[0] == 123
    assert clear_call[1] == ("ticket_category_id",)
    assert clear_call[2] == "guild config recovery"

    final_upsert = calls[2][1][1]
    assert final_upsert["verification_enabled"] is True
    assert final_upsert["last_setup_snapshot"] == snapshot


def test_async_public_setup_facade_uses_canonical_async_writer(monkeypatch) -> None:
    seen: dict[str, Any] = {}

    async def fake_async(guild_id: int, patch: dict[str, Any]):
        seen["guild_id"] = guild_id
        seen["patch"] = dict(patch)
        return guild_config.GuildRuntimeConfig({"guild_id": str(guild_id), **patch})

    monkeypatch.setattr(guild_config, "upsert_guild_config", fake_async)

    result = asyncio.run(
        public_setup_config_writer.upsert_guild_config(
            789,
            {"verify_channel_id": "987"},
        )
    )

    assert seen["guild_id"] == 789
    assert seen["patch"]["__config_write_mode"] == "setup_builder"
    assert seen["patch"]["__config_write_invalidate_completion"] is True
    assert result["verify_channel_id"] == "987"
