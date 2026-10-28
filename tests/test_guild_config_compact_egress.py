"""Egress regression for the canonical guild-config read path.

No live Discord/Supabase access is required. Legacy JSON precedence is retained
and failed/missing optimized RPCs must not erase persisted configuration.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from stoney_verify import guild_config as cfg


class _Response:
    def __init__(self, data):
        self.data = data


class _Supabase:
    def __init__(self, rows, *, error=None):
        self.rows = list(rows)
        self.error = error
        self.calls = []

    def rpc(self, name, params):
        self.calls.append((name, dict(params)))
        if self.error:
            raise self.error
        return _Response(self.rows.pop(0))


def _row(stamp="2026-10-28T12:00:00+00:00"):
    return {
        "guild_id": "98765",
        "updated_at": stamp,
        "settings": {
            "verify_channel_id": "101",
            "source_marker": "from-meta",
            "welcome_card_background_b64": "image-data",
        },
        "staff_role_id": "555",
    }


def test_compact_read_and_revision_hit_do_not_retransmit_config(monkeypatch):
    sb = _Supabase([_row(), {"_not_modified": True}])
    monkeypatch.setattr(cfg, "get_supabase", lambda: sb)
    monkeypatch.setattr(cfg, "GUILD_CONFIG_TABLE_FALLBACKS", ("guild_configs",))
    monkeypatch.setattr(
        cfg, "_fetch_existing_row_sync",
        lambda *_: (_ for _ in ()).throw(AssertionError("full-row fallback used")),
    )
    cfg.clear_guild_config_cache()
    first = asyncio.run(cfg.get_guild_config(98765))
    second = asyncio.run(cfg.get_guild_config(98765, refresh=True))
    assert first["staff_role_id"] == second["staff_role_id"] == "555"
    assert second["welcome_card_background_b64"] == "image-data"
    assert sb.calls[0][1]["p_known_updated_at"] is None
    assert sb.calls[1][1]["p_known_updated_at"] == first["updated_at"]
    cfg.clear_guild_config_cache()


def test_changed_revision_refreshes_even_with_valid_cache(monkeypatch):
    updated = _row("2026-10-28T12:01:00+00:00")
    updated["staff_role_id"] = "999"
    sb = _Supabase([_row(), updated])
    monkeypatch.setattr(cfg, "get_supabase", lambda: sb)
    monkeypatch.setattr(cfg, "GUILD_CONFIG_TABLE_FALLBACKS", ("guild_configs",))
    cfg.clear_guild_config_cache()
    first = asyncio.run(cfg.get_guild_config(98765))
    second = asyncio.run(cfg.get_guild_config(98765, refresh=True))
    assert first["staff_role_id"] == "555"
    assert second["staff_role_id"] == "999"
    cfg.clear_guild_config_cache()


def test_missing_rpc_keeps_legacy_safe_fallback(monkeypatch):
    sb = _Supabase([], error=RuntimeError("PGRST202: function not found"))
    monkeypatch.setattr(cfg, "get_supabase", lambda: sb)
    monkeypatch.setattr(cfg, "GUILD_CONFIG_TABLE_FALLBACKS", ("guild_configs",))
    monkeypatch.setattr(cfg, "_fetch_existing_row_sync", lambda *_: _row())
    result = cfg._db_get_guild_config_sync(98765)
    assert result["staff_role_id"] == "555"
    assert result["verify_channel_id"] == "101"


def test_merging_four_legacy_json_containers_is_lossless():
    old = {
        "guild_id": "98765",
        "settings": {"unique_settings": "s", "priority": "s"},
        "config": {"unique_config": "c", "priority": "c"},
        "metadata": {"unique_metadata": "m", "priority": "m"},
        "meta": {"unique_meta": "x", "priority": "x"},
        "updated_at": "2026-10-28T12:00:00+00:00",
        "staff_role_id": "201",
    }
    # Simulates the SQL jsonb concatenation order (exact compatibility contract).
    compact = dict(old)
    compact["settings"] = {
        **old["settings"], **old["config"], **old["metadata"], **old["meta"],
    }
    for key in ("config", "metadata", "meta"):
        del compact[key]
    before = cfg._normalize_config_row(old, 98765)
    after = cfg._normalize_config_row(compact, 98765)
    for key in ("unique_settings", "unique_config", "unique_metadata",
                "unique_meta", "priority", "staff_role_id", "updated_at"):
        assert before[key] == after[key]
    assert after["priority"] == "x"


def test_rpc_migration_is_read_only_and_server_only():
    sql = Path("supabase_fresh/migrations/202610280001_dankshield_compact_guild_config_rpc.sql").read_text()
    assert "security invoker" in sql.lower()
    assert "p_known_updated_at" in sql
    assert "to_jsonb(g)" in sql
    assert "grant execute on function" in sql.lower()
    assert "to service_role" in sql.lower()
    assert "from public, anon, authenticated" in sql.lower()
    assert "update public.guild_configs" not in sql.lower()
    assert "delete from public.guild_configs" not in sql.lower()


def test_configured_legacy_primary_is_not_overridden_by_compact_rpc(monkeypatch):
    sb = _Supabase([_row()])
    monkeypatch.setattr(cfg, "get_supabase", lambda: sb)
    monkeypatch.setattr(
        cfg, "GUILD_CONFIG_TABLE_FALLBACKS", ("legacy_config", "guild_configs")
    )
    monkeypatch.setattr(
        cfg, "_fetch_existing_row_sync",
        lambda table, _gid: {"guild_id": "98765", "staff_role_id": "202"}
        if table == "legacy_config" else None,
    )
    result = cfg._db_get_guild_config_sync(98765)
    assert result["staff_role_id"] == "202"
    assert sb.calls == []
