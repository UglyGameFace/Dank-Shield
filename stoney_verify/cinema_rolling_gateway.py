"""Opt-in, loopback-only memory-piece TorrServer transport for Dank Cinema.

Existing Cinema sessions remain the authority for authorization and leases.
This transport never exposes TorrServer's general API or accepts a caller URL.
The external engine must be independently provisioned and isolated, and its
disk-piece configuration is verified before creating any torrent session.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
import os
from pathlib import Path
import re
from urllib.parse import urlsplit
from typing import Any

from aiohttp import ClientSession, ClientTimeout, TCPConnector, web

_HASH = re.compile(r"^[a-f0-9]{40}$")
_MAX_RESPONSE_BYTES = 2 * 1024 * 1024
_CHUNK_BYTES = 128 * 1024


class RollingGatewayUnavailable(RuntimeError):
    """Fail closed without silently falling back to disk downloads."""


@dataclass(frozen=True)
class RollingMedia:
    info_hash: str
    file_index: int
    file_size: int


class CinemaRollingGateway:
    def __init__(
        self, *,
        base_url: str,
        cache_dir: Path,
        cache_budget_bytes: int,
        per_torrent_cache_bytes: int,
        max_torrents: int = 4,
        max_parallel_ranges: int = 8,
        max_per_guild_ranges: int = 2,
    ) -> None:
        url = urlsplit(str(base_url))
        if (
            url.scheme != "http"
            or url.hostname != "127.0.0.1"
            or not url.port
            or url.username or url.password or url.path not in ("", "/")
            or url.query or url.fragment
        ):
            raise ValueError("Rolling torrent engine must use an isolated 127.0.0.1 HTTP port.")
        self.base_url = f"http://127.0.0.1:{url.port}"
        self.cache_dir = Path(cache_dir).resolve()
        self.budget_bytes = int(cache_budget_bytes)
        self.per_torrent_bytes = int(per_torrent_cache_bytes)
        self.max_torrents = int(max_torrents)
        self.max_parallel = int(max_parallel_ranges)
        self.max_per_guild = int(max_per_guild_ranges)
        if not (1 <= self.max_torrents <= 16 and 1 <= self.max_per_guild <= self.max_parallel <= 32):
            raise ValueError("Invalid rolling torrent concurrency limits.")
        if (
            self.per_torrent_bytes < 4 * 1024 * 1024
            or self.per_torrent_bytes * self.max_torrents * 4 > self.budget_bytes
            or self.budget_bytes <= 0
        ):
            raise ValueError("Rolling cache reservations exceed the shared storage budget.")
        self._ranges = asyncio.Semaphore(self.max_parallel)
        self._guild_ranges: dict[int, asyncio.Semaphore] = {}
        self._active_hashes: set[str] = set()
        self._lock = asyncio.Lock()

    def _client(self) -> ClientSession:
        return ClientSession(
            connector=TCPConnector(limit=self.max_parallel + 2, use_dns_cache=False),
            timeout=ClientTimeout(total=None, sock_connect=3, sock_read=35),
            trust_env=False,
            auto_decompress=False,
        )

    async def _post(self, route: str, payload: dict[str, Any]) -> Any:
        # No arbitrary URL can enter this method; route is authored locally.
        if route not in ("/settings", "/torrents"):
            raise ValueError("Unsupported engine route")
        async with self._client() as client:
            async with client.post(
                self.base_url + route, json=payload, allow_redirects=False,
            ) as response:
                if response.status != 200:
                    raise RollingGatewayUnavailable(f"Rolling engine {route} returned {response.status}")
                body = await response.read()
                if len(body) > 1024 * 1024:
                    raise RollingGatewayUnavailable("Rolling engine response is unbounded.")
                if not body:
                    return None
                try:
                    return await response.json(content_type=None)
                except (ValueError, TypeError) as exc:
                    raise RollingGatewayUnavailable("Invalid rolling engine response.") from exc

    async def verified_settings(self) -> None:
        try:
            settings = await self._post("/settings", {"action": "get"})
        except (OSError, asyncio.TimeoutError) as exc:
            raise RollingGatewayUnavailable("Rolling engine is unavailable.") from exc
        if not isinstance(settings, dict):
            raise RollingGatewayUnavailable("Rolling engine settings could not be verified.")
        # Each active torrent owns a distinct per-torrent cache. Multiplying by
        # the maximum admission slots is essential: CacheSize is NOT global.
        configured_size = int(settings.get("CacheSize") or 0)
        # Real pinned-engine test run 38649065919 observed 16.8 MiB on
        # disk with a 4 MiB nominal cache, disproving even 4x headroom.
        # NO disk-backed torrent pieces are permitted for this integration.
        if not (
            settings.get("UseDisk") is False
            and settings.get("RemoveCacheOnDrop") is True
            and settings.get("DisableUPNP") is True
            and not str(settings.get("TorrentsSavePath") or "").strip()
            and configured_size >= 4 * 1024 * 1024
            and configured_size <= self.per_torrent_bytes
            and self.max_torrents * configured_size * 4 <= self.budget_bytes
        ):
            raise RollingGatewayUnavailable(
                "Rolling engine must use isolated memory-only pieces with bounded RAM reservations."
            )
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        if self.disk_allocated_bytes():
            raise RollingGatewayUnavailable(
                "Rolling engine media cache contains disk files; memory-only mode is required."
            )

    def disk_allocated_bytes(self) -> int:
        total = 0
        def raise_walk_error(error: OSError) -> None:
            raise error
        for root, dirs, files in os.walk(
            self.cache_dir, followlinks=False, onerror=raise_walk_error
        ):
            dirs[:] = [d for d in dirs if not (Path(root) / d).is_symlink()]
            for filename in files:
                item = Path(root) / filename
                total += item.stat(follow_symlinks=False).st_blocks * 512
        return total

    @staticmethod
    def _safe_hash(info_hash: str) -> str:
        value = str(info_hash or "").lower()
        if not _HASH.fullmatch(value):
            raise ValueError("Invalid torrent info hash.")
        return value

    async def add(
        self,
        *,
        info_hash: str,
        source: str,
        source_torrent_file: Path | None = None,
        expected_file_path: str,
        expected_file_size: int,
    ) -> RollingMedia:
        hash_key = self._safe_hash(info_hash)
        if source_torrent_file is not None:
            source_path = source_torrent_file.resolve(strict=True)
            if not (source_path.is_file() and source_path.suffix == ".torrent" and source_path.is_relative_to(self.cache_dir.parent)):
                raise ValueError("Unapproved local torrent metadata file.")
            link = source_path.as_uri()
        else:
            link = str(source or "")
            if not (link.startswith("magnet:?") and len(link) <= 8192):
                raise ValueError("Rolling backend only accepts magnet or internal torrent metadata.")
        await self.verified_settings()
        async with self._lock:
            if hash_key not in self._active_hashes and len(self._active_hashes) >= self.max_torrents:
                raise RollingGatewayUnavailable("Cinema's rolling stream slots are busy.")
            try:
                item = await self._post("/torrents", {"action": "add", "link": link})
            except (OSError, asyncio.TimeoutError) as exc:
                raise RollingGatewayUnavailable("Rolling engine did not accept this source.") from exc
            if not isinstance(item, dict) or self._safe_hash(item.get("hash")) != hash_key:
                raise RollingGatewayUnavailable("Rolling engine returned a different torrent identity.")
            try:
                media = await self.select_file(
                    hash_key,
                    expected_file_path=expected_file_path,
                    expected_file_size=expected_file_size,
                )
            except Exception:
                # A torrent is not left running if its files do not match the
                # Cinema-approved selection. Any cleanup error fails closed.
                await self._post("/torrents", {"action": "rem", "hash": hash_key})
                raise
            self._active_hashes.add(hash_key)
            return media

    async def select_file(
        self, info_hash: str, *, expected_file_path: str, expected_file_size: int,
    ) -> RollingMedia:
        hash_key = self._safe_hash(info_hash)
        deadline = asyncio.get_running_loop().time() + 30.0
        while True:
            info = await self._post("/torrents", {"action": "get", "hash": hash_key})
            if not isinstance(info, dict) or self._safe_hash(info.get("hash")) != hash_key:
                raise RollingGatewayUnavailable("Rolling torrent metadata is unavailable.")
            matched = [
                row for row in info.get("file_stats", []) if isinstance(row, dict)
                and str(row.get("path") or "") == expected_file_path
                and int(row.get("length") or 0) == int(expected_file_size)
                and int(row.get("id") or 0) > 0
            ]
            if len(matched) == 1:
                return RollingMedia(
                    info_hash=hash_key,
                    file_index=int(matched[0]["id"]),
                    file_size=int(expected_file_size),
                )
            if asyncio.get_running_loop().time() >= deadline:
                raise RollingGatewayUnavailable("Rolling metadata did not match the selected Cinema file.")
            await asyncio.sleep(0.5)

    async def remove(self, media: RollingMedia) -> None:
        hash_key = self._safe_hash(media.info_hash)
        async with self._lock:
            try:
                await self._post("/torrents", {"action": "rem", "hash": hash_key})
            except (OSError, asyncio.TimeoutError) as exc:
                raise RollingGatewayUnavailable("Rolling torrent cleanup failed.") from exc
            self._active_hashes.discard(hash_key)

    async def stream(
        self,
        request: web.Request,
        *,
        media: RollingMedia,
        start: int,
        end: int,
        content_type: str,
        guild_id: int,
        cors_headers: dict[str, str] | None = None,
    ) -> web.StreamResponse:
        """Proxy only a strictly validated signed Cinema byte range.

        Caller MUST validate the existing Cinema token HMAC before entry.
        Upstream never supplies public-facing headers or redirects.
        """
        if not (0 <= start <= end < media.file_size):
            raise web.HTTPRequestRangeNotSatisfiable()
        end = min(int(end), int(start) + _MAX_RESPONSE_BYTES - 1)
        length = end - start + 1
        if self.disk_allocated_bytes() > self.budget_bytes:
            raise web.HTTPServiceUnavailable(
                text="Cinema's rolling cache is full.", headers={"Retry-After": "3"},
            )
        gid = int(guild_id)
        # The global gate prevents unlimited network waiters; per-guild gates
        # keep one server from monopolizing all concurrent Range connections.
        gate = self._guild_ranges.setdefault(gid, asyncio.Semaphore(self.max_per_guild))
        try:
            await asyncio.wait_for(self._ranges.acquire(), timeout=2.0)
        except asyncio.TimeoutError as exc:
            raise web.HTTPServiceUnavailable(
                text="Cinema streaming is busy.", headers={"Retry-After": "2"},
            ) from exc
        got_guild = False
        try:
            try:
                await asyncio.wait_for(gate.acquire(), timeout=2.0)
                got_guild = True
            except asyncio.TimeoutError as exc:
                raise web.HTTPServiceUnavailable(
                    text="Cinema streaming is busy for this server.", headers={"Retry-After": "2"},
                ) from exc
            from urllib.parse import quote
            url = (
                f"{self.base_url}/stream/cinema.mkv"
                f"?link={media.info_hash}&index={media.file_index}&play"
            )
            async with self._client() as client:
                try:
                    async with client.get(
                        url,
                        headers={"Range": f"bytes={start}-{end}"},
                        allow_redirects=False,
                    ) as upstream:
                        header_range = f"bytes {start}-{end}/{media.file_size}"
                        if (
                            upstream.status != 206
                            or upstream.headers.get("Content-Range") != header_range
                            or upstream.content_length != length
                            or upstream.headers.get("Content-Encoding")
                        ):
                            raise web.HTTPServiceUnavailable(
                                text="Cinema's rolling engine returned an invalid byte range.",
                                headers={"Retry-After": "2"},
                            )
                        response = web.StreamResponse(
                            status=206,
                            headers={
                                "Accept-Ranges": "bytes",
                                "Content-Range": header_range,
                                "Content-Length": str(length),
                                "Content-Type": content_type,
                                "Cache-Control": "private, no-store",
                                "X-Content-Type-Options": "nosniff",
                                **dict(cors_headers or {}),
                            },
                        )
                        await response.prepare(request)
                        sent = 0
                        async for chunk in upstream.content.iter_chunked(_CHUNK_BYTES):
                            if not chunk or sent + len(chunk) > length:
                                break
                            await response.write(chunk)
                            sent += len(chunk)
                        if sent != length:
                            # A response already committed cannot be rewritten
                            # as HTTP 503; close it so the browser retries.
                            request.transport.close() if request.transport is not None else None
                            return response
                        await response.write_eof()
                        return response
                except (asyncio.TimeoutError, OSError) as exc:
                    raise web.HTTPServiceUnavailable(
                        text="Rolling stream temporarily unavailable.",
                        headers={"Retry-After": "2"},
                    ) from exc
        finally:
            if got_guild:
                gate.release()
            self._ranges.release()


__all__ = ["CinemaRollingGateway", "RollingGatewayUnavailable", "RollingMedia"]
