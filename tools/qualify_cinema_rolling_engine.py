#!/usr/bin/env python3
"""Qualify a real rolling BitTorrent engine using a 52 GiB sparse seeder.

This script is opt-in, CI-only and must never run in production. Its real
libtorrent 2.1.1 seed-mode source has 52 GiB of logical bytes but only a few
MiB physically allocated. A real pinned TorrServer instance makes the range
requests, not a fake socket peer. No copyrighted media or internet swarm.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import socket
import struct
import subprocess
import tempfile
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

import libtorrent as lt

FILE_BYTES = 52 * 1024 ** 3
PIECE_BYTES = 1024 * 1024
CACHE_BYTES = 4 * 1024 * 1024
RANGE_BYTES = 64 * 1024
TIMEOUT_SECONDS = 45
POSITIONS = (3 * 1024 ** 3, 12 * 1024 ** 3, 50 * 1024 ** 3)
PIECES = FILE_BYTES // PIECE_BYTES
NAME = "cinema-52gib-proof.mkv"
ZERO_PIECE = bytes(PIECE_BYTES)
SPECIAL_PIECES = {
    offset // PIECE_BYTES for offset in POSITIONS
}


def proof_piece(index):
    if index not in SPECIAL_PIECES:
        return ZERO_PIECE
    phrase = f"CINEMA-PIECE-{index:08d}-KNOWN-CONTENT! ".encode()
    return (phrase * (PIECE_BYTES // len(phrase) + 1))[:PIECE_BYTES]


def bencode(value):
    if isinstance(value, int):
        return b"i" + str(value).encode() + b"e"
    if isinstance(value, (bytes, str)):
        payload = value.encode() if isinstance(value, str) else value
        return str(len(payload)).encode() + b":" + payload
    if isinstance(value, list):
        return b"l" + b"".join(bencode(x) for x in value) + b"e"
    if isinstance(value, dict):
        return b"d" + b"".join(
            bencode(key) + bencode(value[key]) for key in sorted(value)
        ) + b"e"
    raise TypeError(type(value))


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def allocated_bytes(directory):
    total = 0
    for root, _, files in os.walk(directory, followlinks=False):
        for name in files:
            total += (Path(root) / name).stat(
                follow_symlinks=False
            ).st_blocks * 512
    return total


@contextmanager
def synthetic_seed():
    with tempfile.TemporaryDirectory(prefix="cinema-52gib-ci-") as work:
        root = Path(work)
        payload_root = root / "seeder-files"
        payload_root.mkdir()
        sparse = payload_root / NAME
        with sparse.open("wb") as out:
            out.truncate(FILE_BYTES)
            for index in sorted(SPECIAL_PIECES):
                out.seek(index * PIECE_BYTES)
                out.write(proof_piece(index))
        if allocated_bytes(payload_root) > 16 * 1024 * 1024:
            raise AssertionError("seed-mode synthetic fixture is not sparse")

        seeder_port = free_port()
        tracker_hits = [0]

        class Tracker(BaseHTTPRequestHandler):
            def do_GET(self):
                tracker_hits[0] += 1
                peer = socket.inet_aton("127.0.0.1")
                compact = peer + struct.pack("!H", seeder_port)
                payload = bencode({
                    b"interval": 2, b"complete": 1,
                    b"incomplete": 0, b"peers": compact,
                })
                self.send_response(200)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *_unused):
                pass

        tracker = ThreadingHTTPServer(("127.0.0.1", 0), Tracker)
        meta = {
            b"length": FILE_BYTES,
            b"name": NAME.encode(),
            b"piece length": PIECE_BYTES,
            b"pieces": b"".join(
                hashlib.sha1(proof_piece(i)).digest()
                for i in range(PIECES)
            ),
        }
        torrent_data = bencode({
            b"announce": (
                f"http://127.0.0.1:{tracker.server_address[1]}/announce"
            ).encode(),
            b"info": meta,
        })
        torrent_file = root / "synthetic-52gib.torrent"
        torrent_file.write_bytes(torrent_data)
        expected_hash = hashlib.sha1(bencode(meta)).hexdigest()
        seed = lt.session({
            "listen_interfaces": f"127.0.0.1:{seeder_port}",
            "enable_dht": False, "enable_lsd": False,
            "enable_upnp": False, "enable_natpmp": False,
        })
        params = lt.add_torrent_params()
        params.ti = lt.torrent_info(str(torrent_file))
        params.save_path = str(payload_root)
        params.flags |= lt.torrent_flags.seed_mode
        handle = seed.add_torrent(params)
        if not seed.is_listening():
            raise AssertionError("actual libtorrent seeder did not bind locally")
        print(
            f"QUALIFY real libtorrent={lt.version} seeder_port={seeder_port} "
            f"infohash={expected_hash} sparse_bytes={allocated_bytes(payload_root)}",
            flush=True,
        )
        threading.Thread(target=tracker.serve_forever, daemon=True).start()
        try:
            yield root, torrent_file, handle, tracker_hits, expected_hash
        finally:
            tracker.shutdown()
            tracker.server_close()
            seed.pause()
            del handle
            del seed


def post_json(base, path, data):
    with urlopen(Request(
        base + path, data=json.dumps(data).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    ), timeout=15) as response:
        payload = response.read()
        return json.loads(payload) if payload else None


def check_range(base, torrent_uri, offset):
    end = offset + RANGE_BYTES - 1
    url = (
        base + "/stream/proof.mkv?link=" + quote(torrent_uri, safe="")
        + "&index=1&play"
    )
    print(f"QUALIFY HTTP 206 Range bytes={offset}-{end}", flush=True)
    last = None
    for attempt in range(2):
        try:
            with urlopen(Request(
                url, headers={"Range": f"bytes={offset}-{end}"}
            ), timeout=TIMEOUT_SECONDS) as response:
                status = response.status
                content_range = response.headers.get("Content-Range")
                result = response.read()
            if status != 206:
                raise AssertionError(f"wrong status {status}")
            if content_range != f"bytes {offset}-{end}/{FILE_BYTES}":
                raise AssertionError(f"wrong Content-Range {content_range}")
            reference = proof_piece(offset // PIECE_BYTES)
            if result != reference[:RANGE_BYTES]:
                raise AssertionError(f"corrupt or incomplete bytes at {offset}")
            return {"offset": offset, "bytes": len(result), "status": status}
        except (HTTPError, URLError, TimeoutError, OSError) as err:
            print(
                f"QUALIFY attempt {attempt + 1}: {type(err).__name__}",
                flush=True,
            )
            last = err
            time.sleep(2)
    raise AssertionError(f"range {offset} cannot be streamed: {last}")


async def prove_rolling_gateway_http(
    *, base: str, root: Path, source: Path, infohash: str,
) -> None:
    """Exercise the production rolling transport against real TorrServer.

    The synthetic seeder and actual engine are already running. A separate
    loopback test route checks authorization before giving the transport a
    request, just as the existing signed Cinema route does in production.
    """
    from aiohttp import ClientSession, web
    from stoney_verify.cinema_rolling_gateway import CinemaRollingGateway

    client = CinemaRollingGateway(
        base_url=base,
        cache_dir=root / "piece-cache",
        cache_budget_bytes=64 * 1024 * 1024,
        per_torrent_cache_bytes=16 * 1024 * 1024,
        max_torrents=1,
        max_parallel_ranges=4,
        max_per_guild_ranges=2,
    )
    await client.verified_settings()
    media = await client.add(
        info_hash=infohash,
        source="",
        source_torrent_file=source,
        expected_file_path=NAME,
        expected_file_size=FILE_BYTES,
    )

    async def signed_media(request: web.Request) -> web.StreamResponse:
        if request.query.get("signed") != "ci-test-token":
            raise web.HTTPUnauthorized()
        try:
            start = int(request.match_info["offset"])
        except (ValueError, KeyError) as exc:
            raise web.HTTPBadRequest() from exc
        if start not in POSITIONS:
            raise web.HTTPForbidden()
        return await client.stream(
            request,
            media=media,
            start=start,
            end=start + RANGE_BYTES - 1,
            content_type="video/x-matroska",
            guild_id=1,
        )

    app = web.Application()
    app.router.add_get("/ci/signed/{offset}", signed_media)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    listening = site._server.sockets[0].getsockname()[1]
    try:
        async with ClientSession() as http:
            no_sig = f"http://127.0.0.1:{listening}/ci/signed/{POSITIONS[0]}"
            async with http.get(no_sig) as response:
                if response.status != 401:
                    raise AssertionError("Unsigned large media accessed engine")
            for offset in (POSITIONS[0], POSITIONS[-1], POSITIONS[0]):
                uri = f"http://127.0.0.1:{listening}/ci/signed/{offset}?signed=ci-test-token"
                async with http.get(uri, timeout=50) as response:
                    payload = await response.read()
                    expected_header = f"bytes {offset}-{offset + RANGE_BYTES - 1}/{FILE_BYTES}"
                    if response.status != 206 or response.headers.get("Content-Range") != expected_header:
                        raise AssertionError("Proxy corrupted HTTP 206 range metadata")
                    if payload != proof_piece(offset // PIECE_BYTES)[:RANGE_BYTES]:
                        raise AssertionError("Proxy corrupted 52 GiB logical movie bytes")
                if allocated_bytes(root / "piece-cache") > 64 * 1024 * 1024:
                    raise AssertionError("Integrated rolling cache exceeded the reserved allowance")
        print("QUALIFY actual gateway HTTP: unauthorized=401, signed 52GiB ranges=3, cache bounded", flush=True)
    finally:
        await runner.cleanup()
        await client.remove(media)


def main():
    binary = os.environ.get("DANK_TEST_TORRSERVER_BINARY", "")
    memory_mode = os.environ.get("DANK_QUALIFY_MEMORY_MODE") == "1"
    if not binary or not Path(binary).is_file():
        raise SystemExit("DANK_TEST_TORRSERVER_BINARY must be set by isolated CI")
    if not str(lt.version).startswith("2.1.1"):
        raise AssertionError("expected pinned libtorrent 2.1.1 as seed peer")
    with synthetic_seed() as (root, source, seed, tracker_hits, infohash):
        api_port = free_port()
        base = f"http://127.0.0.1:{api_port}"
        config = root / "private-config"
        cache = root / "piece-cache"
        config.mkdir()
        cache.mkdir()
        log_path = root / "engine.log"
        with log_path.open("wb") as engine_log:
            engine = subprocess.Popen([
                binary, "--port", str(api_port), "--ip", "127.0.0.1",
                "--path", str(config), "--torrentaddr", "127.0.0.1:0",
            ], cwd=str(root), stdout=engine_log, stderr=subprocess.STDOUT)
            rss_peak = [0]
            stop_rss = threading.Event()
            def sample_rss():
                while not stop_rss.is_set():
                    try:
                        status = Path(f"/proc/{engine.pid}/status").read_text()
                        for line in status.splitlines():
                            if line.startswith("VmRSS:"):
                                rss_peak[0] = max(rss_peak[0], int(line.split()[1]) * 1024)
                                break
                    except (FileNotFoundError, OSError, ValueError):
                        pass
                    stop_rss.wait(0.05)
            rss_sampler = threading.Thread(target=sample_rss, daemon=True)
            rss_sampler.start()
            try:
                for _ in range(60):
                    if engine.poll() is not None:
                        raise AssertionError(
                            "real engine exited at startup: " +
                            log_path.read_text(errors="replace")[-5000:]
                        )
                    try:
                        sets = post_json(base, "/settings", {"action": "get"})
                        break
                    except (HTTPError, URLError, TimeoutError, OSError):
                        time.sleep(0.5)
                else:
                    raise AssertionError("real engine failed to bind loopback")

                sets.update({
                    "CacheSize": CACHE_BYTES,
                    "UseDisk": not memory_mode,
                    "TorrentsSavePath": "" if memory_mode else str(cache),
                    "RemoveCacheOnDrop": True,
                    "ResponsiveMode": False,
                    "ReaderReadAHead": 50,
                    "PreloadCache": 0,
                    "ConnectionsLimit": 8,
                    "DisableUPNP": True,
                    "DisableDHT": True,
                    "DisablePEX": True,
                    "DisableUpload": True,
                })
                post_json(base, "/settings", {"action": "set", "sets": sets})
                verified_sets = post_json(base, "/settings", {"action": "get"})
                if not (
                    verified_sets.get("UseDisk") is (not memory_mode)
                    and verified_sets.get("CacheSize") == CACHE_BYTES
                ):
                    raise AssertionError("real engine rejected cache settings")

                uri = source.as_uri()  # synthetic CI file only
                accepted = post_json(base, "/torrents", {
                    "action": "add", "link": uri
                })
                print(
                    f"QUALIFY engine accepted hash={accepted.get('hash')} "
                    f"size={accepted.get('torrent_size')} "
                    f"tracker_hits={tracker_hits[0]}",
                    flush=True,
                )
                if accepted.get("hash") != infohash:
                    raise AssertionError("torrent infohash mismatch")
                if accepted.get("torrent_size") != FILE_BYTES:
                    raise AssertionError("52 GiB metadata size not accepted")

                results = []
                for offset in POSITIONS:
                    results.append(check_range(base, uri, offset))
                    used = allocated_bytes(cache)
                    if memory_mode and used != 0:
                        raise AssertionError(f"memory-only engine wrote {used} media cache bytes to disk")
                    if not memory_mode and used > CACHE_BYTES * 4:
                        raise AssertionError(f"disk cache allocated {used} bytes, above tested margin")

                uploaded_before_seek = int(seed.status().total_upload)
                results.append(check_range(base, uri, POSITIONS[0]))
                uploaded_after_seek = int(seed.status().total_upload)
                if uploaded_after_seek <= uploaded_before_seek:
                    raise AssertionError(
                        "backward seek did not force new peer data transfer"
                    )
                used = allocated_bytes(cache)
                if memory_mode and used != 0:
                    raise AssertionError(f"memory-only engine wrote {used} media cache bytes")
                if not memory_mode and used > CACHE_BYTES * 4:
                    raise AssertionError(f"disk cache exceeded 4x transient bound: {used}")
                if memory_mode and rss_peak[0] > 512 * 1024 * 1024:
                    raise AssertionError(
                        f"memory-only engine RSS exceeded qualification guard: {rss_peak[0]}"
                    )
                print(json.dumps({
                    "peak_worker_rss_bytes": rss_peak[0],
                    "result": "real_engine_memory_only" if memory_mode else "real_engine_qualification_only",
                    "logical_movie_bytes": FILE_BYTES,
                    "configured_cache_bytes": CACHE_BYTES,
                    "actual_disk_bytes": used,
                    "tracker_hits": tracker_hits[0],
                    "backward_seek_download_bytes":
                        uploaded_after_seek - uploaded_before_seek,
                    "verified_ranges": results,
                }, sort_keys=True), flush=True)
                if not memory_mode:
                    raise AssertionError("Disk-backed Cinema is not approved for rolling streaming.")
                import asyncio
                asyncio.run(prove_rolling_gateway_http(
                    base=base, root=root, source=source, infohash=infohash,
                ))
            except Exception:
                engine_log.flush()
                print(
                    f"QUALIFY failure tracker_hits={tracker_hits[0]} "
                    f"seed_upload={seed.status().total_upload} "
                    f"seed_peers={seed.status().num_peers}",
                    flush=True,
                )
                print(
                    "QUALIFY engine tail:",
                    log_path.read_text(errors="replace")[-4000:],
                    flush=True,
                )
                raise
            finally:
                stop_rss.set()
                rss_sampler.join(timeout=2)
                if memory_mode and rss_peak[0] > 512 * 1024 * 1024:
                    print(f"QUALIFY MEMORY RSS EXCEEDED: {rss_peak[0]}", flush=True)
                engine.terminate()
                try:
                    engine.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    engine.kill()
                    engine.wait(timeout=5)


if __name__ == "__main__":
    main()
