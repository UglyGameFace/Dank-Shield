from __future__ import annotations

import asyncio
import threading
from pathlib import Path
from types import SimpleNamespace

from stoney_verify import guild_config


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "supabase/migrations/20260929143000_atomic_guild_config_patch.sql"


class _AtomicRPC:
    def __init__(self, owner: "_FakeSupabase", params: dict[str, object]) -> None:
        self.owner = owner
        self.params = dict(params)

    def execute(self):
        patch = dict(self.params["p_patch"])  # type: ignore[arg-type]
        with self.owner.lock:
            for key in ("settings", "config", "metadata", "meta"):
                current = dict(self.owner.row.get(key) or {})
                current.update(patch)
                self.owner.row[key] = current
            return SimpleNamespace(data=dict(self.owner.row))


class _FakeSupabase:
    def __init__(self, row: dict[str, object]) -> None:
        self.row = dict(row)
        self.lock = threading.Lock()
        self.calls: list[tuple[str, dict[str, object]]] = []

    def rpc(self, name: str, params: dict[str, object]):
        assert name == guild_config.GUILD_CONFIG_PATCH_RPC
        self.calls.append((name, dict(params)))
        return _AtomicRPC(self, params)


def _stale_row() -> dict[str, object]:
    initial_settings = {
        "member_setup_state": {"current_revision": 1},
        "naming_identity_v1": {"policy": {"mode": "preserve"}},
        "neighboring_setting": {"keep": True},
    }
    return {
        "guild_id": "999",
        "settings": dict(initial_settings),
        "config": dict(initial_settings),
        "metadata": dict(initial_settings),
        "meta": dict(initial_settings),
    }


def test_atomic_writer_sends_only_sparse_updates_not_rebuilt_neighbor_state(monkeypatch) -> None:
    stale = _stale_row()
    fake = _FakeSupabase(stale)
    monkeypatch.setattr(guild_config, "get_supabase", lambda: fake)
    monkeypatch.setattr(guild_config, "GUILD_CONFIG_TABLE_FALLBACKS", ("guild_configs",))
    monkeypatch.setattr(guild_config, "_fetch_existing_row_sync", lambda _table, _gid: dict(stale))

    guild_config._db_upsert_guild_config_sync(
        999,
        {
            "naming_identity_v1": {"policy": {"mode": "search_safe"}},
            "__config_write_mode": "explicit_override",
            "__config_write_source": "naming_identity_runtime",
            "__config_write_allow_keys": ["naming_identity_v1"],
        },
    )

    assert len(fake.calls) == 1
    patch = dict(fake.calls[0][1]["p_patch"])  # type: ignore[arg-type]
    assert patch["naming_identity_v1"] == {"policy": {"mode": "search_safe"}}
    assert "member_setup_state" not in patch
    assert "neighboring_setting" not in patch


def test_concurrent_naming_and_member_setup_writes_preserve_each_other(monkeypatch) -> None:
    stale = _stale_row()
    fake = _FakeSupabase(stale)
    monkeypatch.setattr(guild_config, "get_supabase", lambda: fake)
    monkeypatch.setattr(guild_config, "GUILD_CONFIG_TABLE_FALLBACKS", ("guild_configs",))
    # Both callers deliberately see the same old row. The database RPC, not the
    # client snapshot, must merge their sparse patches.
    monkeypatch.setattr(guild_config, "_fetch_existing_row_sync", lambda _table, _gid: dict(stale))

    async def scenario() -> None:
        naming = asyncio.to_thread(
            guild_config._db_upsert_guild_config_sync,
            999,
            {
                "naming_identity_v1": {"policy": {"mode": "search_safe"}},
                "__config_write_mode": "explicit_override",
                "__config_write_source": "naming_identity_runtime",
                "__config_write_allow_keys": ["naming_identity_v1"],
            },
        )
        member_setup = asyncio.to_thread(
            guild_config._db_upsert_guild_config_sync,
            999,
            {
                "member_setup_state": {"current_revision": 2},
                "__config_write_mode": "explicit_override",
                "__config_write_source": "member_setup",
                "__config_write_allow_keys": ["member_setup_state"],
            },
        )
        await asyncio.gather(naming, member_setup)

    asyncio.run(scenario())

    saved = dict(fake.row["settings"])  # type: ignore[arg-type]
    assert saved["naming_identity_v1"] == {"policy": {"mode": "search_safe"}}
    assert saved["member_setup_state"] == {"current_revision": 2}
    assert saved["neighboring_setting"] == {"keep": True}


def test_atomic_patch_migration_uses_server_side_json_merge_and_service_role_only() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")

    assert "create or replace function public.patch_dank_guild_config" in sql.lower()
    assert "coalesce(%I, ''{}''::jsonb) || $2" in sql
    assert "on conflict (guild_id) do nothing" in sql.lower()
    assert "revoke all on function public.patch_dank_guild_config(text, jsonb) from public" in sql.lower()
    assert "grant execute on function public.patch_dank_guild_config(text, jsonb) to service_role" in sql.lower()
