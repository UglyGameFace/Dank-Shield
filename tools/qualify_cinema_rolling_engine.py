#!/usr/bin/env python3
"""Real-engine qualification of bounded BitTorrent streaming.

Runs only under the dedicated isolated CI workflow. A loopback synthetic
BitTorrent peer supplies deterministic 1 MiB pieces for a 50+ GiB logical
torrent without materializing a large file. This is an external engine
qualification, NOT an enabled Dank Cinema backend or a production guarantee.
"""
from __future__ import annotations

from collections import Counter
from contextlib import contextmanager
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import socket
import socketserver
import struct
import subprocess
import tempfile
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

FILE_BYTES = 52 * 1024 ** 3
PIECE_BYTES = 1024 * 1024
CACHE_BYTES = 4 * 1024 * 1024
PIECES = FILE_BYTES // PIECE_BYTES
PATTERN = (b"Cinema rolling cache integrity proof; " * 30000)[:PIECE_BYTES]
assert len(PATTERN) == PIECE_BYTES
PIECE_HASH = hashlib.sha1(PATTERN).digest()
PROOF_RANGE_BYTES = 64 * 1024
TRACKER_INTERVAL = 2
TIMEOUT_SECONDS = 105


def bencode(value):
    if isinstance(value, int):
        return b"i" + str(value).encode() + b"e"
    if isinstance(value, (bytes, str)):
        value = value.encode() if isinstance(value, str) else value
        return str(len(value)).encode() + b":" + value
    if isinstance(value, list):
        return b"l" + b"".join(map(bencode, value)) + b"e"
    if isinstance(value, dict):
        return (b"d" + b"".join(bencode(key) + bencode(value[key])
                                for key in sorted(value)) + b"e")
    raise TypeError(type(value))


def receive_exact(sock, count):
    chunks = []
    while count:
        payload = sock.recv(count)
        if not payload:
            raise EOFError("peer connection closed")
        chunks.append(payload)
        count -= len(payload)
    return b"".join(chunks)


class SyntheticPeer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, info_hash):
        super().__init__(("127.0.0.1", 0), PeerHandler)
        self.info_hash = info_hash
        self.requests = Counter()
        self.requests_lock = threading.Lock()

    def piece_requests(self, index):
        with self.requests_lock:
            return self.requests[index]


class PeerHandler(socketserver.BaseRequestHandler):
    def handle(self):
        sock = self.request
        sock.settimeout(60)
        try:
            header = receive_exact(sock, 68)
            if header[0] != 19 or header[1:20] != b"BitTorrent protocol":
                return
            if header[28:48] != self.server.info_hash:
                return
            sock.sendall(header[:28] + self.server.info_hash +
                         (b"-DS0001-" + b"proof-peer01"))
            # Each piece is advertised as available. All data is generated
            # on demand; no file is opened or seeded from the internet.
            bitfield = bytes([255]) * (PIECES // 8)
            sock.sendall(struct.pack("!I", len(bitfield) + 1) +
                         b"\x05" + bitfield)
            sock.sendall(b"\x00\x00\x00\x01\x01")  # unchoke
            while True:
                length = struct.unpack("!I", receive_exact(sock, 4))[0]
                if length > 1024 * 1024:
                    return
                if not length:
                    continue
                message = receive_exact(sock, length)
                if message[0] != 6 or len(message) != 13:
                    continue
                index, offset, count = struct.unpack("!III", message[1:])
                if index >= PIECES or offset + count > PIECE_BYTES or count > 16384:
                    return
                with self.server.requests_lock:
                    self.server.requests[index] += 1
                response = b"\x07" + struct.pack("!II", index, offset)
                response += PATTERN[offset:offset + count]
                sock.sendall(struct.pack("!I", len(response)) + response)
        except (EOFError, OSError, TimeoutError):
            return


@contextmanager
def local_services():
    with tempfile.TemporaryDirectory(prefix="cinema-real-engine-") as work:
        root = Path(work)
        proof_info = {
            b"length": FILE_BYTES,
            b"name": b"cinema-52gib-proof.mkv",
            b"piece length": PIECE_BYTES,
            b"pieces": PIECE_HASH * PIECES,
        }
        encoded = bencode(proof_info)
        info_hash = hashlib.sha1(encoded).digest()
        peer = SyntheticPeer(info_hash)
        tracker_address = ("127.0.0.1", 0)

        class Tracker(BaseHTTPRequestHandler):
            def do_GET(self):
                addr = socket.inet_aton("127.0.0.1")
                compact = addr + struct.pack("!H", peer.server_address[1])
                data = bencode({b"interval": TRACKER_INTERVAL,
                                b"complete": 1, b"incomplete": 0, b"peers": compact})
                self.send_response(200)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *_args):
                pass

        tracker = ThreadingHTTPServer(tracker_address, Tracker)
        torrent_bytes = bencode({
            b"announce": (
                f"http://127.0.0.1:{tracker.server_address[1]}/announce"
            ).encode(),
            b"info": proof_info,
        })
        torrent_file = root / "synthetic-52gib.torrent"
        torrent_file.write_bytes(torrent_bytes)
        threading.Thread(target=peer.serve_forever, daemon=True).start()
        threading.Thread(target=tracker.serve_forever, daemon=True).start()
        try:
            yield root, torrent_file, peer
        finally:
            tracker.shutdown()
            peer.shutdown()
            tracker.server_close()
            peer.server_close()


def request_json(base, path, payload):
    body = json.dumps(payload).encode()
    with urlopen(Request(
        base + path, data=body,
        headers={"Content-Type": "application/json"}, method="POST",
    ), timeout=15) as response:
        return json.loads(response.read()) if response.status != 204 else None


def port_free():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def allocated_bytes(directory):
    # Linux st_blocks is what Discloud counts for sparse/allocated cache
    # files; logical file length is not a substitute for physical usage.
    total = 0
    for root, _, files in os.walk(directory, followlinks=False):
        for name in files:
            total += (Path(root) / name).stat(follow_symlinks=False).st_blocks * 512
    return total


def stream_once(base, torrent_url, offset, report):
    end = offset + PROOF_RANGE_BYTES - 1
    url = (base + "/stream/proof.mkv?link=" + quote(torrent_url, safe="") +
           "&index=1&play")
    last = None
    for attempt in range(3):
        try:
            with urlopen(Request(url, headers={
                "Range": f"bytes={offset}-{end}"
            }), timeout=TIMEOUT_SECONDS) as response:
                status = response.status
                content_range = response.headers.get("Content-Range")
                data = response.read()
            if (status != 206 or content_range !=
                    f"bytes {offset}-{end}/{FILE_BYTES}"):
                raise AssertionError(
                    f"HTTP range mismatch: {status}, {content_range}"
                )
            if data != PATTERN[offset % PIECE_BYTES:
                               offset % PIECE_BYTES + PROOF_RANGE_BYTES]:
                raise AssertionError(f"byte integrity failure at offset={offset}")
            report.append({"offset": offset, "status": status,
                           "bytes": len(data)})
            return
        except (HTTPError, URLError, TimeoutError, OSError) as error:
            last = error
            time.sleep(min(5, attempt + 1))
    raise AssertionError(f"synthetic range unavailable offset={offset}: {last}")


def main():
    binary = os.environ.get("DANK_TEST_TORRSERVER_BINARY", "")
    if not binary or not Path(binary).is_file():
        raise SystemExit("Provide an existing pinned TorrServer binary in CI")
    with local_services() as (root, torrent_file, peer):
        port = port_free()
        base = f"http://127.0.0.1:{port}"
        runtime_dir = root / "private-config"
        runtime_dir.mkdir()
        cache_dir = root / "piece-cache"
        cache_dir.mkdir()
        # Only on loopback; never reachable via Cinema's public signed route.
        log_path = root / "engine-stderr.log"
        engine_log = log_path.open("wb")
        proc = subprocess.Popen([
            binary, "--port", str(port), "--ip", "127.0.0.1",
            "--path", str(runtime_dir), "--torrentaddr", "127.0.0.1:0",
        ], cwd=str(root), stdout=engine_log, stderr=subprocess.STDOUT)
        try:
            for _ in range(60):
                if proc.poll() is not None:
                    engine_log.flush()
                    raise AssertionError("TorrServer exited during startup: " + log_path.read_text(errors="replace")[-7000:])
                try:
                    settings = request_json(base, "/settings", {"action": "get"})
                    break
                except (HTTPError, URLError, TimeoutError, OSError):
                    time.sleep(0.5)
            else:
                raise AssertionError("TorrServer did not bind loopback")
            settings.update({
                "CacheSize": CACHE_BYTES,
                "UseDisk": True,
                "TorrentsSavePath": str(cache_dir),
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
            request_json(base, "/settings", {"action": "set", "sets": settings})
            state = request_json(base, "/settings", {"action": "get"})
            assert state.get("UseDisk") is True
            assert state.get("CacheSize") == CACHE_BYTES

            torrent_uri = torrent_file.as_uri()
            sequence = [3 * 1024 ** 3, 12 * 1024 ** 3,
                        50 * 1024 ** 3, 3 * 1024 ** 3]
            report = []
            for position in sequence:
                stream_once(base, torrent_uri, position, report)
                used = allocated_bytes(cache_dir)
                # Allow a bounded 4x transient margin for in-flight pieces.
                # This is a QUALIFICATION limit; production needs a measured
                # global cap across concurrent torrents and other apps.
                if used > CACHE_BYTES * 4:
                    raise AssertionError(
                        f"disk cache exceeded qualification ceiling: {used}"
                    )
            first_piece = sequence[0] // PIECE_BYTES
            rerequests = peer.piece_requests(first_piece)
            if rerequests < (2 * PROOF_RANGE_BYTES // 16384):
                raise AssertionError(
                    "Evicted playback data was not proven re-downloadable"
                )
            print(json.dumps({
                "result": "engine_qualification_only",
                "logical_movie_bytes": FILE_BYTES,
                "disk_cache_bytes": allocated_bytes(cache_dir),
                "configured_cache_bytes": CACHE_BYTES,
                "requests_to_original_piece": rerequests,
                "verified_ranges": report,
            }, sort_keys=True))
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
            engine_log.close()


if __name__ == "__main__":
    main()
