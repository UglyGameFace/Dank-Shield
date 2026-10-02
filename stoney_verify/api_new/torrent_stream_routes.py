from __future__ import annotations

"""Signed HTTP byte-range routes for progressive torrent playback."""

import asyncio
from typing import Any

from aiohttp import web

from stoney_verify.torrent_streaming import (
    get_torrent_manager,
    media_content_type,
    parse_http_range,
)

_STREAM_CHUNK_BYTES = 1024 * 1024


async def torrent_stream(request: web.Request) -> web.StreamResponse:
    manager = get_torrent_manager()
    token = str(request.match_info.get("token", "") or "")
    expires = str(request.query.get("exp", "") or "")
    signature = str(request.query.get("sig", "") or "")

    if not await manager.validate_stream_access(token, expires, signature):
        raise web.HTTPUnauthorized(text="Invalid or expired torrent stream token.")

    session = await manager.get(token)
    if session is None:
        raise web.HTTPNotFound(text="Torrent stream session not found.")

    try:
        start, end, partial = parse_http_range(
            str(request.headers.get("Range", "") or ""),
            session.file_size,
        )
    except ValueError:
        return web.Response(
            status=416,
            headers={
                "Content-Range": f"bytes */{session.file_size}",
                "Accept-Ranges": "bytes",
            },
        )

    length = end - start + 1
    headers = {
        "Accept-Ranges": "bytes",
        "Content-Type": media_content_type(session.file_name),
        "Content-Length": str(length),
        "Content-Disposition": f'inline; filename="{session.file_name}"',
        "Cache-Control": "private, no-store",
        "X-Content-Type-Options": "nosniff",
    }
    status_code = 206 if partial else 200
    if partial:
        headers["Content-Range"] = f"bytes {start}-{end}/{session.file_size}"

    if request.method == "HEAD":
        return web.Response(status=status_code, headers=headers)

    plan = manager.prepare_playback_request(session, start, end)
    first_end = min(end, start + _STREAM_CHUNK_BYTES - 1)
    startup_wait_end = max(first_end, plan.startup_wait_end)
    ready = await manager.wait_range(
        session,
        start,
        startup_wait_end,
        readahead_bytes=plan.target_bytes,
    )
    if not ready:
        status = manager.status(session)
        return web.json_response(
            {
                "ok": False,
                "error": "buffering",
                "progress": status.get("progress", 0.0),
                "download_rate": status.get("download_rate", 0),
                "peers": status.get("peers", 0),
            },
            status=503,
            headers={"Retry-After": "2"},
        )

    response = web.StreamResponse(status=status_code, headers=headers)
    await response.prepare(request)

    cursor = start
    try:
        while cursor <= end:
            chunk_end = min(end, cursor + _STREAM_CHUNK_BYTES - 1)
            if cursor != start:
                ready = await manager.wait_range(
                    session,
                    cursor,
                    chunk_end,
                    readahead_bytes=plan.target_bytes,
                )
                if not ready:
                    break
            payload = await manager.read_range(session, cursor, chunk_end)
            if not payload:
                break
            await response.write(payload)
            cursor += len(payload)
    except (ConnectionResetError, asyncio.CancelledError):
        pass
    finally:
        try:
            await response.write_eof()
        except Exception:
            pass
    return response


async def torrent_status(request: web.Request) -> web.Response:
    manager = get_torrent_manager()
    token = str(request.match_info.get("token", "") or "")
    session = await manager.get(token)
    if session is None:
        return web.json_response({"ok": False, "error": "not found"}, status=404)
    return web.json_response({"ok": True, "torrent": manager.status(session)})


async def torrent_cancel(request: web.Request) -> web.Response:
    manager = get_torrent_manager()
    token = str(request.match_info.get("token", "") or "")
    removed = await manager.remove(token)
    return web.json_response({"ok": bool(removed)})


def register_torrent_public_routes(app: web.Application) -> None:
    get_torrent_manager().ensure_cleanup_task()
    app.router.add_get(
        "/media/torrent/stream/{token}/{filename}",
        torrent_stream,
        allow_head=True,
    )


def register_torrent_admin_routes(app: web.Application, server: Any) -> None:
    _ = server
    app.router.add_get("/media/torrent/status/{token}", torrent_status)
    app.router.add_post("/media/torrent/cancel/{token}", torrent_cancel)


__all__ = [
    "register_torrent_admin_routes",
    "register_torrent_public_routes",
    "torrent_cancel",
    "torrent_status",
    "torrent_stream",
]
