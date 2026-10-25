"""Exact backend-switch contracts for Embed Studio v2.

No live database is touched. The legacy guild-config CAS remains the default.
"""
from __future__ import annotations

import asyncio
from copy import deepcopy
from types import SimpleNamespace

import pytest

from stoney_verify.embed_studio_core import STUDIO_KEY, new_project, parse_library
from stoney_verify import embed_studio_storage_v2 as v2
from stoney_verify import embed_studio_ui as ui


def test_new_backend_opt_in_and_pinned_project(monkeypatch):
    assert not v2.storage_v2_enabled({})
    assert not v2.storage_v2_enabled({v2.FLAG: "false"})
    assert v2.storage_v2_enabled({v2.FLAG: "true"})
    v2.verify_project({"SUPABASE_URL": "https://viamriafkbqhquvrsnua.supabase.co"})
    for url in ("https://old.example.supabase.co",
                "https://viamriafkbqhquvrsnua.supabase.co.evil.net",
                "http://viamriafkbqhquvrsnua.supabase.co", ""):
        with pytest.raises(v2.StudioV2Unavailable):
            v2.verify_project({"SUPABASE_URL": url})


def test_read_missing_v2_with_legacy_never_discards_drafts(monkeypatch):
    monkeypatch.setattr(v2, "verify_project", lambda: None)

    class Query:
        def select(self, *_args): return self
        def eq(self, *_args): return self
        def limit(self, *_args): return self
        def execute(self): return SimpleNamespace(data=[])

    monkeypatch.setattr(v2, "_client", lambda: SimpleNamespace(table=lambda *_: Query()))
    legacy = {STUDIO_KEY: {
        "revision": 7, "drafts": {"1": {"version": 1, "project": new_project()}}
    }}
    with pytest.raises(v2.StudioV2Unavailable, match="migration"):
        v2._read_sync(123, legacy)
    source, lib = v2._read_sync(123, {})
    assert source.expected_revision == -1
    assert lib["drafts"] == {}


def test_v2_read_rejects_legacy_drift_and_oversized_or_duplicate_collections(monkeypatch):
    monkeypatch.setattr(v2, "verify_project", lambda: None)
    class Query:
        def __init__(self, data): self.data = data
        def select(self, *_args): return self
        def eq(self, *_args): return self
        def limit(self, *_args): return self
        def execute(self): return SimpleNamespace(data=self.data)
    class Database:
        def __init__(self, snap):
            self.snap = snap
        def table(self, name):
            if name == v2.LIBRARY_TABLE:
                return Query([{"revision": 4, "legacy_snapshot": self.snap}])
            return Query([{"section": "media", "item_id": "logo", "payload":
                           {"name": "Logo", "url": "https://example.com/logo.png"}}])
    db = Database({"revision": 2})
    monkeypatch.setattr(v2, "_client", lambda: db)
    with pytest.raises(v2.StudioV2Unavailable, match="changed"):
        v2._read_sync(123, {STUDIO_KEY: {"revision": 3}})
    source, lib = v2._read_sync(123, {STUDIO_KEY: {"revision": 2}})
    assert source.expected_revision == 4
    assert lib["media"]["logo"]["name"] == "Logo"
    assert lib["revision"] == 4


def test_v2_write_only_uses_rpc_and_never_writes_old_guild_config(monkeypatch):
    monkeypatch.setattr(v2, "verify_project", lambda: None)
    calls = []

    class DB:
        def rpc(self, name, args):
            calls.append((name, args))
            return SimpleNamespace(execute=lambda: SimpleNamespace(data={
                "applied": True, "revision": 5, "item_count": 1,
            }))
    monkeypatch.setattr(v2, "_client", lambda: DB())
    source = v2.StudioV2Source(123, 4, {"revision": 2})
    lib = parse_library(None)
    lib["media"]["logo"] = {"name": "Logo", "url": "https://example.com/logo.png"}
    assert v2._write_sync(source, lib) is True
    assert len(calls) == 1
    assert calls[0][0] == v2.WRITE_RPC
    assert calls[0][1]["p_expected_revision"] == 4
    assert calls[0][1]["p_sections"]["media"]["logo"]["name"] == "Logo"
    assert calls[0][1]["p_legacy_snapshot"] == {"revision": 2}


def test_v2_no_confirmation_is_not_success_and_backend_errors_fail_closed(monkeypatch):
    monkeypatch.setattr(v2, "verify_project", lambda: None)
    source = v2.StudioV2Source(111, -1, {})
    monkeypatch.setattr(v2, "_client", lambda: SimpleNamespace(
        rpc=lambda *_a, **_kw: SimpleNamespace(execute=lambda: SimpleNamespace(data=[]))
    ))
    with pytest.raises(v2.StudioV2Unavailable, match="atomic confirmation"):
        v2._write_sync(source, parse_library(None))
    monkeypatch.setattr(v2, "_client", lambda: (_ for _ in ()).throw(RuntimeError("db down")))
    with pytest.raises(v2.StudioV2Unavailable, match="save unavailable"):
        asyncio.run(v2.save_library(source, parse_library(None)))


def test_ui_routes_to_v2_only_when_flag_set_and_refuses_mode_flip(monkeypatch):
    old = {STUDIO_KEY: {"revision": 2}}
    async def legacy(_id, refresh):
        return old
    monkeypatch.setattr("stoney_verify.guild_config.get_guild_config", legacy)

    async def v2_read(guild_id, legacy):
        assert guild_id == 123 and legacy is old
        return v2.StudioV2Source(123, 6, old[STUDIO_KEY]), {"revision": 6}
    monkeypatch.setattr(v2, "read_library", v2_read)
    monkeypatch.setattr(v2, "storage_v2_enabled", lambda: True)
    source, lib = asyncio.run(ui._load_library(123))
    assert isinstance(source, v2.StudioV2Source)
    assert lib["revision"] == 6
    monkeypatch.setattr(v2, "storage_v2_enabled", lambda: False)
    with pytest.raises(ValueError, match="backend changed"):
        asyncio.run(ui._save_library(123, source, parse_library(None)))


def test_legacy_editor_path_stays_unchanged_by_default(monkeypatch):
    old = {STUDIO_KEY: {"revision": 2, "media": {"a": {
        "name": "Logo", "url": "https://example.com/logo.png",
    }}}}
    async def cfg(_guild, refresh):
        return old
    monkeypatch.setattr("stoney_verify.guild_config.get_guild_config", cfg)
    monkeypatch.setattr(v2, "storage_v2_enabled", lambda: False)
    source, data = asyncio.run(ui._load_library(7))
    assert source is old
    assert data["media"]["a"]["name"] == "Logo"


def test_sql_v2_is_private_and_has_atomic_guards():
    from pathlib import Path
    sql = Path("supabase_fresh/migrations/202610200028_dankshield_embed_studio_v2.sql").read_text()
    for clause in (
        "enable row level security",
        "revoke all on public.dank_embed_studio_items",
        "security invoker",
        "for update",
        "on conflict (guild_id,section,item_id)",
        "where public.dank_embed_studio_items.payload is distinct from excluded.payload",
        "from public, anon, authenticated",
        "grant execute on function public.dank_embed_studio_write",
    ):
        assert clause in sql.lower()
    assert "drop table" not in sql.lower()
    assert "truncate" not in sql.lower()
    assert "security definer" not in sql.lower()


def test_manual_seeding_requires_owner_confirmation_and_does_not_autorun():
    from pathlib import Path
    source = Path("tools/stage_embed_studio_v2.py").read_text()
    assert 'op.add_argument("--dry-run"' in source
    assert 'op.add_argument("--apply"' in source
    assert "SEED_{args.guild_id}" in source
    assert "legacy data" in source.lower()


def test_normalized_v2_can_save_valid_library_above_old_config_blob_guard(monkeypatch):
    from stoney_verify.embed_studio_core import library_payload, StudioValidationError
    monkeypatch.setattr(v2, "verify_project", lambda: None)
    lib = parse_library(None)
    for i in range(12):
        p = new_project()
        e = p["embeds"][0]
        e["description"] = "D" * 3400
        e["footer"] = "F" * 1900
        e["image"] = "https://cdn.example.com/" + ("a" * 1900)
        e["thumbnail"] = "https://cdn.example.com/" + ("b" * 1900)
        e["author_name"] = "Studio team"
        e["author_url"] = "https://cdn.example.com/" + ("c" * 1900)
        e["author_icon"] = "https://cdn.example.com/" + ("d" * 1900)
        e["footer_icon"] = "https://cdn.example.com/" + ("e" * 1900)
        e["title_url"] = "https://cdn.example.com/" + ("f" * 1900)
        lib["templates"][str(i)] = {"name": f"Layout {i}", "project": p}
    for i in range(50):
        lib["media"][str(i)] = {"name": f"Artwork {i}",
            "url": "https://cdn.example.com/" + ("g" * 1900)}
    with pytest.raises(StudioValidationError, match="256 KiB"):
        library_payload(lib)
    calls = []
    class DB:
        def rpc(self, name, args):
            calls.append(args)
            return SimpleNamespace(execute=lambda:SimpleNamespace(data={"applied":True}))
    monkeypatch.setattr(v2, "_client", lambda: DB())
    assert v2._write_sync(v2.StudioV2Source(123,1,{}),lib)
    assert v2.MAX_STUDIO_V2_BYTES == 1024*1024
    assert len(calls)==1 and len(calls[0]["p_sections"]["templates"])==12
