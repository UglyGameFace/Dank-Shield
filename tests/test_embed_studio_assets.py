"""Managed Studio uploads: bounded MIME-verified public assets; no new slash routes."""
from __future__ import annotations

import asyncio
from io import BytesIO
from types import SimpleNamespace

import discord
from PIL import Image
import pytest

from stoney_verify import embed_studio_assets as assets
from stoney_verify.embed_studio_ui import MediaLibraryMenu, StudioArtworkUploadModal, StudioSession
from stoney_verify.embed_studio_core import parse_library


def _image_bytes(fmt="PNG"):
    file = BytesIO()
    Image.new("RGB", (10, 6), color=(17, 140, 75)).save(file, format=fmt)
    return file.getvalue()


def test_disabled_by_default_and_excludes_unconfigured_assets():
    assert not assets.uploads_enabled({})
    assert not assets.uploads_enabled({assets.FLAG: "no"})
    assert assets.uploads_enabled({assets.FLAG: "true"})
    assert assets.MAX_UPLOAD_BYTES == 6 * 1024 * 1024


@pytest.mark.parametrize("fmt,extension,mime", [
    ("PNG", "png", "image/png"),
    ("JPEG", "jpg", "image/jpeg"),
    ("WEBP", "webp", "image/webp"),
    ("GIF", "gif", "image/gif"),
])
def test_image_signature_not_untrusted_discord_filename(fmt, extension, mime):
    name, ext, content_type = assets.inspect_image(_image_bytes(fmt), "Branding.txt")
    assert (name, ext, content_type) == ("Branding", extension, mime)


@pytest.mark.parametrize("bad", [b"", b"NOT AN IMAGE", b"<svg/>", b"GIF89aBAD",
                                  b"X" * (6*1024*1024+1)])
def test_malicious_or_invalid_upload_is_rejected(bad):
    with pytest.raises(assets.StudioAssetError):
        assets.inspect_image(bad, "picture.png")


def test_image_not_stored_in_guild_json_and_server_path_is_private_to_writer(monkeypatch):
    monkeypatch.setattr(assets, "uploads_enabled", lambda: True)
    monkeypatch.setattr(assets, "verify_project", lambda: None)
    captured = []
    class Storage:
        def upload(self, **kw):
            captured.append(kw)
            assert kw["path"].startswith("777/")
            assert kw["file_options"]["upsert"] == "false"
            assert kw["file"].read(8) == bytes([137, 80, 78, 71, 13, 10, 26, 10])
            return object()
        def get_public_url(self, path):
            return "https://viamriafkbqhquvrsnua.supabase.co/storage/v1/object/public/dank-studio-artwork/" + path
        def remove(self, paths):
            captured.append({"remove": paths})
    class Table:
        def insert(self, row):
            captured.append({"metadata": row})
            return self
        def execute(self):
            return SimpleNamespace(data=[{}])
    db = SimpleNamespace(
        storage=SimpleNamespace(from_=lambda bucket: Storage()),
        table=lambda name: Table(),
    )
    monkeypatch.setattr(assets, "_client", lambda: db)
    uploaded = assets._upload_sync(777, _image_bytes(), "Lounge Logo.png")
    assert uploaded.url.startswith("https://")
    assert uploaded.object_path.startswith("777/")
    assert uploaded.content_type == "image/png"
    assert uploaded.image_sha256 and len(uploaded.image_sha256) == 64
    assert captured[1]["metadata"]["guild_id"] == "777"
    assert captured[1]["metadata"]["object_path"] == uploaded.object_path


def test_upload_failure_does_not_keep_new_asset(monkeypatch):
    monkeypatch.setattr(assets, "uploads_enabled", lambda: True)
    monkeypatch.setattr(assets, "verify_project", lambda: None)
    removed = []
    class Storage:
        def upload(self, **kw):
            return object()
        def get_public_url(self, path):
            return "https://example.com/" + path
        def remove(self, paths):
            removed.extend(paths)
    class BrokenTable:
        def insert(self, data): return self
        def execute(self): raise RuntimeError("db down")
    monkeypatch.setattr(assets, "_client", lambda: SimpleNamespace(
        storage=SimpleNamespace(from_=lambda *_: Storage()),
        table=lambda *_: BrokenTable(),
    ))
    with pytest.raises(RuntimeError):
        assets._upload_sync(777, _image_bytes(), "image.png")
    assert len(removed) == 1 and removed[0].startswith("777/")


def test_mobile_media_upload_access_is_in_existing_editor():
    s = StudioSession(guild_id=777, owner_id=88)
    menu = MediaLibraryMenu(s, parse_library(None))
    assert "Upload Image/GIF" in {getattr(item, "label", "") for item in menu.children}
    assert len(StudioArtworkUploadModal(s).children) == 1
    assert max(sum(1 for item in menu.children if item.row == row) for row in range(5)) <= 5


def test_upload_write_cas_failure_preserves_existing_entries(monkeypatch):
    from stoney_verify import embed_studio_ui as ui
    s = StudioSession(guild_id=777, owner_id=88)
    modal = StudioArtworkUploadModal(s)
    art = assets.UploadedArtwork(
        777,"Banner","https://example.com/banner.png","777/deadbeef.png",
        "a"*64,"image/png",300,
    )
    dropped = []
    notes = []
    async def read_file(**_kwargs): return _image_bytes()
    attachment = SimpleNamespace(filename="Banner.png",size=len(_image_bytes()),read=read_file)
    modal.file_upload._values = [attachment]
    async def upload(*args): return art
    async def discard(item): dropped.append(item)
    async def load(_gid): return {}, parse_library(None)
    async def conflict(*_args): return False
    async def defer(**_kwargs): pass
    async def say(_interaction, note, **kwargs): notes.append(note)
    interaction = SimpleNamespace(guild=SimpleNamespace(id=777),
       user=SimpleNamespace(id=88),response=SimpleNamespace(defer=defer))
    monkeypatch.setattr(ui, "_staff", lambda *_: True)
    monkeypatch.setattr(ui, "_load_library", load)
    monkeypatch.setattr(ui, "_save_library", conflict)
    monkeypatch.setattr(ui, "_say", say)
    monkeypatch.setattr(assets, "upload_artwork", upload)
    monkeypatch.setattr(assets, "discard_failed_upload", discard)
    monkeypatch.setattr(assets, "uploads_enabled", lambda: True)
    asyncio.run(modal.on_submit(interaction))
    assert len(dropped) == 1
    assert "not saved" in notes[-1].lower()
    assert not s.dirty


def test_assets_sql_restricts_public_writes_and_keeps_media_bytes_external():
    from pathlib import Path
    sql=Path("supabase_fresh/migrations/202610200029_dankshield_embed_studio_assets.sql").read_text().lower()
    assert "insert into storage.buckets" in sql
    assert "public=true" in sql
    assert "enable row level security" in sql
    assert "revoke all on table public.dank_embed_studio_assets" in sql
    assert "grant select,insert,delete on public.dank_embed_studio_assets to service_role" in sql
    assert "drop table" not in sql
