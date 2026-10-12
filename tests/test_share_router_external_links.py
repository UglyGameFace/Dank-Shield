from __future__ import annotations

import asyncio
import io
import json
import time
from pathlib import Path

import aiohttp
from aiohttp import web

from stoney_verify import share_router_external_links as links


def test_link_ttl_presets_are_explicit():
    assert links._TTL_PRESETS == {3600, 86400, 604800, 2592000}
    assert max(links._TTL_PRESETS) == 30 * 24 * 3600


def test_unsafe_public_url_status_fails_closed(monkeypatch, tmp_path):
    monkeypatch.setattr(links, "_storage_dir", lambda: tmp_path)
    monkeypatch.setattr(links, "_public_base", lambda: "")
    good, message = links.delivery_status()
    assert good is False
    assert "HTTPS" in message


def test_expiring_token_watch_download_range_and_revoke(tmp_path, monkeypatch):
    monkeypatch.setattr(links, "_storage_dir", lambda: tmp_path)
    monkeypatch.setattr(links, "_public_base", lambda: "https://media.example.com")

    async def scenario():
        content = b"x" * 2048 + b"video" + b"y" * 2048
        url_watch, url_download, ident = await links.stage_video(
            io.BytesIO(content), ttl_seconds=3600, ext=".mp4",
        )
        token = url_watch.split("/")[-2]
        assert len(token) == 64
        metadata = json.loads((tmp_path / (ident + ".json")).read_text())
        assert "token" not in metadata
        assert "source" not in metadata
        assert metadata["size"] == len(content)
        assert metadata["expires_at"] >= int(time.time()) + 3500

        app = web.Application()
        links.register_share_video_routes(app)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, host="127.0.0.1", port=0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        prefix = f"http://127.0.0.1:{port}/share-video/{ident}/{token}"
        try:
            async with aiohttp.ClientSession() as client:
                async with client.get(prefix + "/watch") as response:
                    assert response.status == 200
                    assert response.headers["Content-Type"].startswith("video/mp4")
                    assert response.headers["Content-Disposition"].startswith("inline")
                    assert await response.read() == content
                async with client.get(prefix + "/download") as response:
                    assert response.status == 200
                    assert response.headers["Content-Disposition"].startswith("attachment")
                async with client.get(prefix + "/watch", headers={"Range": "bytes=2-5"}) as response:
                    assert response.status == 206
                    assert await response.read() == content[2:6]
                async with client.get(prefix.replace(token, "1" * 64) + "/watch") as response:
                    assert response.status == 404
                async with client.get(prefix + "/not-valid") as response:
                    assert response.status == 404
                await links.revoke_video(ident)
                async with client.get(prefix + "/watch") as response:
                    assert response.status == 404
        finally:
            await runner.cleanup()
            task = links._SWEEP_TASK
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
            links._SWEEP_TASK = None

    asyncio.run(scenario())


def test_expiration_gates_expired_asset(tmp_path, monkeypatch):
    monkeypatch.setattr(links, "_storage_dir", lambda: tmp_path)
    monkeypatch.setattr(links, "_public_base", lambda: "https://media.example.com")
    async def scenario():
        watch, download, ident = await links.stage_video(
            io.BytesIO(b"demo"), ttl_seconds=86400, ext=".mp4",
        )
        metadata_path = tmp_path / (ident + ".json")
        payload = json.loads(metadata_path.read_text())
        payload["expires_at"] = int(time.time()) - 1
        metadata_path.write_text(json.dumps(payload))
        assert links._read_metadata(ident)["expires_at"] <= time.time()
        assert links._sweep_sync() >= 1
        assert not (tmp_path / (ident + ".video")).exists()
        assert not metadata_path.exists()
    asyncio.run(scenario())


def test_quota_refuses_new_asset_without_removing_existing(tmp_path, monkeypatch):
    monkeypatch.setattr(links, "_storage_dir", lambda: tmp_path)
    monkeypatch.setattr(links, "_public_base", lambda: "https://media.example.com")
    async def scenario():
        _, _, ident = await links.stage_video(io.BytesIO(b"ready"), ttl_seconds=604800)
        original_path = tmp_path / (ident + ".video")
        assert original_path.exists()
        monkeypatch.setattr(links, "_storage_used", lambda: links.storage_max_bytes())
        blocked = await links.stage_video(io.BytesIO(b"another"), ttl_seconds=604800)
        assert blocked is None
        assert original_path.exists()
    asyncio.run(scenario())


def test_invalid_ttl_and_unsupported_type_do_not_create_records(tmp_path, monkeypatch):
    monkeypatch.setattr(links, "_storage_dir", lambda: tmp_path)
    monkeypatch.setattr(links, "_public_base", lambda: "https://media.example.com")
    async def scenario():
        assert await links.stage_video(io.BytesIO(b"test"), ttl_seconds=31 * 86400) is None
        assert await links.stage_video(io.BytesIO(b"test"), ttl_seconds=3600, ext=".exe") is None
    asyncio.run(scenario())
    assert not list(tmp_path.glob("*.json"))
