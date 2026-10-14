"""Isolated HTTP regressions for the opt-in Cinema rolling transport."""
import asyncio
from dataclasses import dataclass
from pathlib import Path
import re

import pytest
from aiohttp import ClientSession, web

from stoney_verify.cinema_rolling_gateway import (
    CinemaRollingGateway,
    RollingGatewayUnavailable,
    RollingMedia,
)
from stoney_verify.api_new import torrent_stream_routes

SIZE = 52 * 1024 ** 3
HASH = "a" * 40
START = 50 * 1024 ** 3
CONTENT = b"VALIDATED-CINEMA-BYTES" * (2 * 1024 * 1024 // 22 + 2)


async def launch(app):
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    return runner, f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}"


@dataclass
class Upstream:
    settings: dict
    add_calls: int = 0
    stream_calls: int = 0
    removed: bool = False
    corrupt_headers: bool = False

    def app(self):
        app = web.Application()
        async def settings(request):
            body = await request.json()
            assert body["action"] == "get"
            return web.json_response(self.settings)
        async def torrents(request):
            body = await request.json()
            if body["action"] == "add":
                self.add_calls += 1
                return web.json_response({"hash": HASH, "torrent_size": SIZE})
            if body["action"] == "get":
                return web.json_response({
                    "hash": HASH,
                    "file_stats": [{"id": 1, "path": "movie.mkv", "length": SIZE}],
                })
            if body["action"] == "rem":
                self.removed = True
                return web.Response(status=200)
            raise AssertionError("Unexpected torrent action")
        async def stream(request):
            self.stream_calls += 1
            assert request.query["link"] == HASH
            assert request.query["index"] == "1"
            assert "play" in request.query
            header = request.headers["Range"]
            m = re.fullmatch(r"bytes=(\d+)-(\d+)", header)
            assert m
            start, end = int(m[1]), int(m[2])
            size = end - start + 1
            assert size <= 2 * 1024 * 1024
            return web.Response(
                status=206,
                body=CONTENT[:size],
                headers={
                    "Content-Range": (
                        f"bytes {start + 1}-{end}/{SIZE}"
                        if self.corrupt_headers
                        else f"bytes {start}-{end}/{SIZE}"
                    ),
                    "Content-Type": "application/octet-stream",
                },
            )
        app.router.add_post("/settings", settings)
        app.router.add_post("/torrents", torrents)
        app.router.add_get("/stream/cinema.mkv", stream)
        return app


def gateway(url, cache_dir):
    return CinemaRollingGateway(
        base_url=url,
        cache_dir=cache_dir,
        cache_budget_bytes=256 * 1024 * 1024,
        per_torrent_cache_bytes=16 * 1024 * 1024,
        max_torrents=4,
        max_parallel_ranges=4,
        max_per_guild_ranges=2,
    )


def settings(cache):
    return {
        "CacheSize": 16 * 1024 * 1024,
        "UseDisk": False,
        "RemoveCacheOnDrop": True,
        "DisableUPNP": True,
        "TorrentsSavePath": "",
    }


def test_rolling_endpoint_and_reservations_reject_host_and_overcommit(tmp_path):
    for url in [
        "https://127.0.0.1:8090",
        "http://localhost:8090",
        "http://192.168.1.1:8090",
        "http://127.0.0.1:8090/admin",
        "http://alice@127.0.0.1:8090",
    ]:
        with pytest.raises(ValueError):
            gateway(url, tmp_path)
    with pytest.raises(ValueError, match="budget"):
        CinemaRollingGateway(
            base_url="http://127.0.0.1:9999",
            cache_dir=tmp_path, cache_budget_bytes=64 * 1024 * 1024,
            per_torrent_cache_bytes=64 * 1024 * 1024,
            max_torrents=4,
        )


def test_rolling_engine_refuses_unverified_disk_settings(tmp_path):
    async def run():
        root = tmp_path / "rolling-pieces"
        root.mkdir()
        upstream = Upstream(settings(root))
        runner, url = await launch(upstream.app())
        try:
            transport = gateway(url, root)
            await transport.verified_settings()
            upstream.settings["UseDisk"] = True
            with pytest.raises(RollingGatewayUnavailable, match="memory-only"):
                await transport.verified_settings()
            upstream.settings["UseDisk"] = False
            upstream.settings["CacheSize"] = 512 * 1024 * 1024
            with pytest.raises(RollingGatewayUnavailable):
                await transport.verified_settings()
        finally:
            await runner.cleanup()
    asyncio.run(run())


def test_real_http_rolling_path_preserves_signed_access_and_52gib_ranges(
    monkeypatch, tmp_path,
):
    async def run():
        root = tmp_path / "rolling-pieces"
        root.mkdir()
        upstream = Upstream(settings(root))
        engine_runner, url = await launch(upstream.app())
        transport = gateway(url, root)
        @dataclass
        class MediaSession:
            rolling_media: RollingMedia = RollingMedia(HASH, 1, SIZE)
            file_size: int = SIZE
            file_name: str = "movie.mkv"
            guild_id: int = 123
        session = MediaSession()
        class Manager:
            rolling_gateway = transport

            async def validate_stream_access(self, token, exp, sig, cid):
                return token == "demo" and sig == "allowed" and exp == "12345"

            async def get(self, token):
                return session if token == "demo" else None
        monkeypatch.setattr(torrent_stream_routes, "get_torrent_manager", lambda: Manager())
        app = web.Application()
        app.router.add_get(
            "/media/torrent/stream/{token}/{filename}", torrent_stream_routes.torrent_stream,
        )
        runner, public_url = await launch(app)
        try:
            async with ClientSession() as client:
                endpoint = public_url + "/media/torrent/stream/demo/movie.mkv"
                params = {"exp": "12345", "sig": "not-allowed"}
                async with client.get(endpoint, params=params, headers={
                    "Range": f"bytes={START}-{START + 512 - 1}"
                }) as response:
                    assert response.status == 401
                assert upstream.stream_calls == 0
                params["sig"] = "allowed"
                async with client.get(endpoint, params=params, headers={
                    "Range": f"bytes={START}-{START + 2 * 1024 * 1024 + 99}"
                }) as response:
                    data = await response.read()
                    assert response.status == 206
                    assert response.headers["Content-Range"] == (
                        f"bytes {START}-{START + 2 * 1024 * 1024 - 1}/{SIZE}"
                    )
                    assert response.headers["Cache-Control"] == "private, no-store"
                    assert data == CONTENT[:2 * 1024 * 1024]
                assert upstream.stream_calls == 1
                upstream.corrupt_headers = True
                async with client.get(endpoint, params=params, headers={
                    "Range": f"bytes={START}-{START + 1023}"
                }) as response:
                    assert response.status == 503
        finally:
            await runner.cleanup()
            await engine_runner.cleanup()
    asyncio.run(run())


def test_rolling_metadata_matching_and_last_lease_removal(tmp_path):
    async def run():
        root = tmp_path / "rolling-pieces"
        root.mkdir()
        upstream = Upstream(settings(root))
        runner, url = await launch(upstream.app())
        transport = gateway(url, root)
        try:
            media = await transport.add(
                info_hash=HASH,
                source="magnet:?xt=urn:btih:" + HASH,
                expected_file_path="movie.mkv",
                expected_file_size=SIZE,
            )
            assert media == RollingMedia(HASH, 1, SIZE)
            assert upstream.add_calls == 1
            with pytest.raises(RollingGatewayUnavailable, match="match"):
                # The backend cannot silently substitute another episode.
                await transport.select_file(
                    HASH, expected_file_path="other.mkv",
                    expected_file_size=SIZE,
                )
            await transport.remove(media)
            assert upstream.removed
            assert not transport._active_hashes
        finally:
            await runner.cleanup()
    asyncio.run(run())
