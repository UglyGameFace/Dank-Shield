"""Role truth must not fetch giant guild JSON every 45 seconds."""
from __future__ import annotations

from types import SimpleNamespace

from stoney_verify import role_truth
from stoney_verify import guild_config
from stoney_verify import globals as shared_globals


class _Query:
    def __init__(self, db):
        self.db = db
    def select(self, *_):
        self.db.full_reads += 1
        return self
    def eq(self, *_):
        return self
    def limit(self, *_):
        return self
    def execute(self):
        return SimpleNamespace(data=[{
            "guild_id": "98765", "settings": {
                "unverified_role_id": "77", "verified_role_id": "88",
            },
        }])


class _DB:
    def __init__(self, *, rpc_error=None):
        self.rpc_error = rpc_error
        self.rpc_calls = 0
        self.full_reads = 0
    def rpc(self, name, args):
        assert name == "get_dank_guild_role_config"
        assert args == {"p_guild_id": "98765"}
        self.rpc_calls += 1
        if self.rpc_error:
            raise self.rpc_error
        return SimpleNamespace(execute=lambda: SimpleNamespace(data={
            "guild_id": "98765",
            "verified_role_id": "88",
            "unverified_role_id": "77",
            "staff_role_id": "99",
        }))
    def table(self, *_):
        return _Query(self)


def _install(monkeypatch, db):
    monkeypatch.setattr(shared_globals, "get_supabase", lambda: db)
    monkeypatch.setenv("DANK_GUILD_CONFIG_TABLE", "guild_configs")
    guild_config.clear_guild_config_cache()
    role_truth.clear_cache()


def test_compact_role_truth_reads_only_role_ids_and_caches(monkeypatch):
    db = _DB()
    _install(monkeypatch, db)
    a = role_truth.get_guild_role_config(98765)
    b = role_truth.get_guild_role_config(98765)
    assert a["verified_role_id"] == b["verified_role_id"] == "88"
    assert a["staff_role_id"] == "99"
    assert db.rpc_calls == 1
    assert db.full_reads == 0
    role_truth.clear_cache()


def test_compact_rpc_unavailable_falls_back_to_legacy_authoritative_row(monkeypatch):
    db = _DB(rpc_error=RuntimeError("PGRST202"))
    _install(monkeypatch, db)
    cfg = role_truth.get_guild_role_config(98765)
    assert cfg["verified_role_id"] == "88"
    assert db.rpc_calls == 1
    assert db.full_reads == 1
    role_truth.clear_cache()


def test_custom_primary_uses_legacy_configuration_not_another_project(monkeypatch):
    db = _DB()
    _install(monkeypatch, db)
    monkeypatch.setenv("DANK_GUILD_CONFIG_TABLE", "guild_config")
    cfg = role_truth.get_guild_role_config(98765)
    assert cfg["unverified_role_id"] == "77"
    assert db.rpc_calls == 0
    assert db.full_reads == 1
    role_truth.clear_cache()


def test_role_truth_rpc_has_service_role_only_and_no_data_changes():
    from pathlib import Path
    sql = Path("supabase_fresh/migrations/202610280002_dankshield_compact_role_truth_rpc.sql").read_text()
    assert "security invoker" in sql.lower()
    assert "to service_role" in sql.lower()
    assert "from public, anon, authenticated" in sql.lower()
    assert "to_jsonb(g)" in sql.lower()
    assert "update public.guild_configs" not in sql.lower()
    assert "delete from public.guild_configs" not in sql.lower()
