from __future__ import annotations

"""Bounded ephemeral private video links on the existing public media server.

Bearer-token URLs are secrets: anyone possessing a link can access it until it
expires or is revoked. No database migrations, user URLs or video payloads in
PostgreSQL, no additional web listener, no unlimited storage promises.
"""

import asyncio
import io
import hashlib
import hmac
import json
import os
import re
import secrets
import shutil
import time
from pathlib import Path
from urllib.parse import urlsplit

import aiohttp
from aiohttp import web

from .share_router_media_network import public_tcp_connector, public_get

_ID_RE = re.compile(r"^[0-9a-f]{32}$")
_TOKEN_RE = re.compile(r"^[0-9a-f]{64}$")
_ALLOWED_EXTS = {".mp4": "video/mp4", ".webm": "video/webm", ".mov": "video/quicktime", ".gif": "image/gif"}
_DEFAULT_FILE_MAX = 128 * 1024 * 1024
_HARD_FILE_MAX = 256 * 1024 * 1024
_DEFAULT_STORE_MAX = 512 * 1024 * 1024
_DEFAULT_RESERVE = 256 * 1024 * 1024
_DEFAULT_LINK_EGRESS_BYTES = 2 * 1024 * 1024 * 1024
_MAX_TTL_SECONDS = 30 * 24 * 3600
_DEFAULT_TTL_SECONDS = 7 * 24 * 3600
_TTL_PRESETS = frozenset({3600, 86400, 7 * 86400, 30 * 86400})
_IO_LOCK = asyncio.Lock()
_DOWNLOAD_ACTIVE = 0
_DELIVERY_MAX_ACTIVE = 4
_SWEEP_TASK: asyncio.Task | None = None
_SWEEP_CURSOR = 0


def _env_limit(key: str, default: int, low: int, high: int) -> int:
    try:
        value = int(os.getenv(key, str(default)) or default)
    except (ValueError, TypeError):
        value = default
    return max(low, min(high, value))


def file_max_bytes() -> int:
    return _env_limit("DANK_SHARE_DOWNLOAD_FILE_MAX_BYTES", _DEFAULT_FILE_MAX, 20 * 1024 * 1024, 256 * 1024 * 1024)


def storage_max_bytes() -> int:
    return _env_limit("DANK_SHARE_DOWNLOAD_STORAGE_MAX_BYTES", _DEFAULT_STORE_MAX, 64 * 1024 * 1024, 2 * 1024 * 1024 * 1024)


def link_egress_budget() -> int:
    return _env_limit(
        "DANK_SHARE_DOWNLOAD_LINK_EGRESS_BYTES",
        _DEFAULT_LINK_EGRESS_BYTES,
        256 * 1024 * 1024,
        16 * 1024 * 1024 * 1024,
    )


def _storage_dir() -> Path:
    return Path(os.getenv("DANK_SHARE_DOWNLOAD_STORAGE_DIR", "data/share_downloads")).expanduser()


def _public_base() -> str:
    from .share_router_public_server import media_public_base_url, media_server_enabled, media_server_ready
    if not media_server_enabled() or not media_server_ready():
        return ""
    value = media_public_base_url()
    try:
        p = urlsplit(value)
        if p.scheme != "https" or not p.hostname or p.username or p.password or p.query or p.fragment:
            return ""
        if p.port not in {None, 443}:
            return ""
    except ValueError:
        return ""
    return value.rstrip("/")


def delivery_status() -> tuple[bool, str]:
    if not _public_base():
        return False, "Public HTTPS media address is not configured or the media server is offline"
    root = _storage_dir()
    try:
        root.mkdir(parents=True, exist_ok=True)
        if not root.is_dir() or root.is_symlink():
            return False, "Storage directory is unsafe"
        usage = shutil.disk_usage(root)
        if usage.free <= _DEFAULT_RESERVE:
            return False, "Low available storage; reserve is protected"
        if storage_max_bytes() < file_max_bytes():
            return False, "Configured total storage quota is below the per-file maximum"
    except OSError:
        return False, "Storage directory is not writable"
    return True, "Large-file links available (public HTTPS reachability still requires a live canary)"


def _paths(ident: str) -> tuple[Path, Path]:
    root = _storage_dir()
    return root / (ident + ".video"), root / (ident + ".json")


def _valid_meta(value: object) -> bool:
    return (
        isinstance(value, dict)
        and isinstance(value.get("expires_at"), (int, float))
        and isinstance(value.get("size"), int)
        and 0 < value["size"] <= _HARD_FILE_MAX
        and value.get("ext") in _ALLOWED_EXTS
        and isinstance(value.get("token_hash"), str)
        and len(value["token_hash"]) == 64
    )


def _read_metadata(ident: str) -> dict | None:
    if not _ID_RE.fullmatch(ident):
        return None
    _media, meta_path = _paths(ident)
    try:
        raw = json.loads(meta_path.read_text(encoding="utf-8"))
        return raw if _valid_meta(raw) else None
    except (OSError, ValueError, TypeError):
        return None


def _sweep_sync(max_entries: int = 128) -> int:
    global _SWEEP_CURSOR
    root = _storage_dir()
    if not root.is_dir():
        return 0
    deleted = 0
    now = time.time()
    # Rotate over candidates. Repeated scans must reach late directory entries,
    # not continually inspect only the first 128 valid/active media files.
    entries = list(root.iterdir())
    if not entries:
        _SWEEP_CURSOR = 0
        return 0
    count = min(max(1, max_entries), len(entries))
    start = _SWEEP_CURSOR % len(entries)
    _SWEEP_CURSOR = (start + count) % len(entries)
    for i in range(count):
        entry = entries[(start + i) % len(entries)]
        if not entry.is_file() or entry.is_symlink():
            continue
        suffix = entry.suffix
        ident = entry.stem
        if not _ID_RE.fullmatch(ident):
            continue
        video, metadata = _paths(ident)
        if suffix == ".json":
            data = _read_metadata(ident)
            if data is None or data["expires_at"] <= now or not video.is_file():
                video.unlink(missing_ok=True)
                metadata.unlink(missing_ok=True)
                deleted += 1
        elif suffix == ".video":
            if not metadata.is_file() and now - entry.stat().st_mtime > 3600:
                video.unlink(missing_ok=True)
                deleted += 1
        elif suffix == ".part" and now - entry.stat().st_mtime > 3600:
            # A crash while spooling must not leak temporary disk space.
            entry.unlink(missing_ok=True)
            deleted += 1
    return deleted


async def _sweeper() -> None:
    while True:
        try:
            async with _IO_LOCK:
                await asyncio.to_thread(_sweep_sync)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            print(f"⚠️ share_download cleanup error={type(exc).__name__}")
        await asyncio.sleep(900)


def start_cleanup_worker() -> None:
    global _SWEEP_TASK
    if _SWEEP_TASK is None or _SWEEP_TASK.done():
        _SWEEP_TASK = asyncio.create_task(_sweeper(), name="share-router-link-expiry")


def _storage_used() -> int:
    root = _storage_dir()
    total = 0
    for item in root.glob("*.video"):
        if item.is_file() and not item.is_symlink():
            total += item.stat().st_size
    return total


def _stage_sync(source, ident: str, token: str, ttl_seconds: int, ext: str) -> int:
    root = _storage_dir()
    root.mkdir(parents=True, exist_ok=True)
    target, metadata = _paths(ident)
    temp = root / (ident + ".part")
    max_file = file_max_bytes()
    try:
        if shutil.disk_usage(root).free < max_file + _DEFAULT_RESERVE:
            return 0
        if _storage_used() + max_file > storage_max_bytes():
            return 0
        size = 0
        source.seek(0)
        with temp.open("xb") as output:
            while True:
                block = source.read(64 * 1024)
                if not block:
                    break
                size += len(block)
                if size > max_file:
                    return 0
                output.write(block)
        if size <= 0:
            return 0
        os.replace(temp, target)
        record = {
            "expires_at": int(time.time()) + ttl_seconds,
            "size": size,
            "ext": ext,
            "token_hash": hashlib.sha256(token.encode("ascii")).hexdigest(),
            "reserved_bytes": 0,
        }
        # File is not addressable until the metadata exists.
        metadata.write_text(json.dumps(record, separators=(",", ":")), encoding="utf-8")
        return size
    except (OSError, ValueError):
        return 0
    finally:
        temp.unlink(missing_ok=True)
        if not metadata.is_file():
            target.unlink(missing_ok=True)


async def stage_video(fp, *, ttl_seconds: int, ext: str = ".mp4") -> tuple[str, str, str] | None:
    if ttl_seconds not in _TTL_PRESETS or ext not in _ALLOWED_EXTS:
        return None
    base = _public_base()
    if not base:
        return None
    ok, _ = delivery_status()
    if not ok:
        return None
    ident = secrets.token_hex(16)
    token = secrets.token_hex(32)
    async with _IO_LOCK:
        size = await asyncio.to_thread(_stage_sync, fp, ident, token, ttl_seconds, ext)
    if not size:
        return None
    prefix = f"{base}/share-video/{ident}/{token}"
    return (f"{prefix}/watch{ext}", f"{prefix}/download", ident)


async def check_public_delivery() -> tuple[bool, str]:
    """Use a short-lived throwaway token to check real HTTPS reachability.

    Does not publish the probe URL or media bytes to Discord. A 200 response
    proves the route is reachable, not that Discord will generate a player.
    """
    ready, reason = delivery_status()
    if not ready:
        return False, reason
    staged = await stage_video(io.BytesIO(b"dank-share-probe"), ttl_seconds=3600, ext=".mp4")
    if staged is None:
        return False, "Hosting could not reserve test storage"
    watch_url, _download_url, ident = staged
    try:
        timeout = aiohttp.ClientTimeout(total=10.0, connect=4.0)
        async with aiohttp.ClientSession(
            timeout=timeout, connector=public_tcp_connector(),
        ) as session:
            response, _final = await public_get(session, watch_url)
            async with response:
                if response.status != 200 or await response.content.read(64) != b"dank-share-probe":
                    return False, "Public HTTPS route returned an unexpected response"
        return True, "HTTPS watch endpoint is reachable with a valid private token"
    except (aiohttp.ClientError, asyncio.TimeoutError, OSError, ValueError):
        return False, "Public HTTPS route could not be reached from this deployment"
    finally:
        await revoke_video(ident)


async def revoke_video(ident: str) -> None:
    if not _ID_RE.fullmatch(ident):
        return
    async with _IO_LOCK:
        video, meta = _paths(ident)
        await asyncio.to_thread(video.unlink, missing_ok=True)
        await asyncio.to_thread(meta.unlink, missing_ok=True)


def _requested_transfer_size(request: web.Request, size: int) -> int:
    if request.method == "HEAD":
        return 0
    try:
        requested = request.http_range
    except ValueError:
        return size
    start = requested.start
    stop = requested.stop
    if start is None and stop is None:
        return size
    if start is None:
        return min(size, int(stop or size))
    if start < 0:
        return min(size, -start)
    return max(0, min(stop if stop is not None else size, size) - min(start, size))


def _reserve_delivery_sync(ident: str, token_digest: str, expected_bytes: int) -> bool:
    """Atomic under _IO_LOCK; debit advertised HTTP bytes before streaming.

    Overcounts aborted transfers instead of permitting indefinite bearer-link
    replay. No analytics or remote-user IDs are stored.
    """
    record = _read_metadata(ident)
    if record is None or record["expires_at"] <= time.time():
        return False
    if not hmac.compare_digest(str(record["token_hash"]), token_digest):
        return False
    debit = max(0, int(expected_bytes))
    already = max(0, int(record.get("reserved_bytes", 0)))
    if already + debit > link_egress_budget():
        return False
    if not debit:
        return True
    _media, metadata = _paths(ident)
    temporary = metadata.with_suffix(".json.tmp")
    try:
        record["reserved_bytes"] = already + debit
        temporary.write_text(json.dumps(record, separators=(",", ":")), encoding="utf-8")
        os.replace(temporary, metadata)
        return True
    except (OSError, TypeError, ValueError):
        return False
    finally:
        temporary.unlink(missing_ok=True)


async def _serve_video(request: web.Request) -> web.StreamResponse:
    global _DOWNLOAD_ACTIVE
    ident = str(request.match_info["ident"])
    token = str(request.match_info["token"])
    if not _ID_RE.fullmatch(ident) or not _TOKEN_RE.fullmatch(token):
        raise web.HTTPNotFound()
    if _DOWNLOAD_ACTIVE >= _DELIVERY_MAX_ACTIVE:
        raise web.HTTPServiceUnavailable(text="Too many active transfers")
    meta = _read_metadata(ident)
    if meta is None or float(meta["expires_at"]) <= time.time():
        raise web.HTTPNotFound()
    digest = hashlib.sha256(token.encode("ascii")).hexdigest()
    if not hmac.compare_digest(digest, meta["token_hash"]):
        raise web.HTTPNotFound()
    video, _ = _paths(ident)
    try:
        if not video.is_file() or video.is_symlink() or video.stat().st_size != meta["size"]:
            raise web.HTTPNotFound()
    except OSError:
        raise web.HTTPNotFound()
    mode = str(request.match_info["mode"])
    is_download = mode == "download"
    if not is_download and mode != "watch" + str(meta["ext"]):
        raise web.HTTPNotFound()
    disposition = "attachment" if is_download else "inline"
    headers = {
        "Content-Disposition": f'{disposition}; filename="DankShield-Video{meta["ext"]}"',
        "Cache-Control": "private, no-store",
        "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "no-referrer",
        "X-Robots-Tag": "noindex, nofollow, noarchive",
        "Cross-Origin-Resource-Policy": "cross-origin",
    }
    # The copied bearer URL may be fetched repeatedly by Discord previews or
    # shared by strangers. Bound lifetime egress per asset, even after restart.
    size_to_reserve = _requested_transfer_size(request, meta["size"])
    async with _IO_LOCK:
        if not await asyncio.to_thread(
            _reserve_delivery_sync, ident, digest, size_to_reserve,
        ):
            raise web.HTTPTooManyRequests(text="Private download limit exhausted or link expired")

    response = web.FileResponse(video, headers=headers)
    response.content_type = _ALLOWED_EXTS[meta["ext"]]
    _DOWNLOAD_ACTIVE += 1
    try:
        return await response.prepare(request)
    finally:
        _DOWNLOAD_ACTIVE -= 1


def register_share_video_routes(app: web.Application) -> None:
    app.router.add_get(
        "/share-video/{ident}/{token}/{mode:watch(?:\\.mp4|\\.webm|\\.mov|\\.gif)?|download}",
        _serve_video,
    )
    start_cleanup_worker()


__all__ = ["check_public_delivery", "delivery_status", "file_max_bytes", "register_share_video_routes",
           "link_egress_budget", "revoke_video", "stage_video", "storage_max_bytes", "_DEFAULT_TTL_SECONDS", "_TTL_PRESETS"]
