from __future__ import annotations

"""Signed HTTP byte-range routes for progressive torrent playback."""

import asyncio
from typing import Any

from aiohttp import web

from stoney_verify.torrent_streaming import (
    TorrentSessionUnavailableError,
    get_torrent_manager,
    media_content_type,
    parse_http_range,
)

_STREAM_CHUNK_BYTES = 1024 * 1024


def _bounded_partial_response_end(
    start: int,
    requested_end: int,
    buffered_end: int,
    *,
    partial: bool,
) -> int:
    """Return the byte end we can truthfully advertise for this response.

    RFC 9110 permits a 206 response to satisfy only a subset of the requested
    range. Movie Night uses that deliberately so a slow torrent never advertises
    a huge Content-Length and then closes the response early when later pieces
    are still unavailable.
    """

    requested = max(int(start), int(requested_end))
    if not partial:
        return requested
    return max(int(start), min(requested, int(buffered_end)))


async def torrent_stream(request: web.Request) -> web.StreamResponse:
    manager = get_torrent_manager()
    token = str(request.match_info.get("token", "") or "")
    expires = str(request.query.get("exp", "") or "")
    signature = str(request.query.get("sig", "") or "")
    consumer_key = str(request.query.get("cid", "") or "").strip()[:96]

    if not await manager.validate_stream_access(
        token,
        expires,
        signature,
        consumer_key,
    ):
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

    requested_end = end
    status_code = 206 if partial else 200

    if request.method == "HEAD":
        length = requested_end - start + 1
        headers = {
            "Accept-Ranges": "bytes",
            "Content-Type": media_content_type(session.file_name),
            "Content-Length": str(length),
            "Content-Disposition": f'inline; filename="{session.file_name}"',
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        }
        if partial:
            headers["Content-Range"] = (
                f"bytes {start}-{requested_end}/{session.file_size}"
            )
        return web.Response(status=status_code, headers=headers)

    first_end = min(requested_end, start + _STREAM_CHUNK_BYTES - 1)
    try:
        plan = manager.prepare_playback_request(
            session,
            start,
            first_end,
            consumer_key=consumer_key,
        )
        startup_wait_end = max(first_end, plan.startup_wait_end)
        ready = await manager.wait_range(
            session,
            start,
            startup_wait_end,
            readahead_bytes=plan.target_bytes,
        )
    except TorrentSessionUnavailableError:
        await manager.discard_unusable_session(token)
        raise web.HTTPGone(
            text=(
                "This torrent media session is no longer available. "
                "Return to Dank Cinema and choose the release again."
            )
        )
    if not ready:
        try:
            status = manager.status(session)
        except TorrentSessionUnavailableError:
            await manager.discard_unusable_session(token)
            raise web.HTTPGone(
                text=(
                    "This torrent media session is no longer available. "
                    "Return to Dank Cinema and choose the release again."
                )
            )
        return web.json_response(
            {
                "ok": False,
                "error": "buffering",
                "progress": status.get("progress", 0.0),
                "download_rate": status.get("download_rate", 0),
                "peers": status.get("peers", 0),
                "buffer": status.get("buffer", {}),
            },
            status=503,
            headers={"Retry-After": "2"},
        )

    # For a byte-range request, only advertise the contiguous bytes that the
    # startup wait above has already proven available. Browsers can request the
    # remainder with the next Range request. This avoids a protocol-invalid
    # short body when a weak swarm cannot deliver a later chunk in time.
    end = _bounded_partial_response_end(
        start,
        requested_end,
        startup_wait_end,
        partial=partial,
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
    if partial:
        headers["Content-Range"] = f"bytes {start}-{end}/{session.file_size}"

    manager.schedule_metadata_probe(session)

    response = web.StreamResponse(status=status_code, headers=headers)
    await response.prepare(request)

    cursor = start
    client_disconnected = False
    try:
        while cursor <= end:
            chunk_end = min(end, cursor + _STREAM_CHUNK_BYTES - 1)
            if cursor != start:
                plan = manager.prepare_playback_request(
                    session,
                    cursor,
                    chunk_end,
                    consumer_key=consumer_key,
                )
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
    except TorrentSessionUnavailableError:
        # The HTTP response may already be committed at this point, so do not
        # attempt to replace it with a JSON error. Close this range cleanly and
        # retire the dead handle so the next state poll reports media_missing.
        client_disconnected = True
        await manager.discard_unusable_session(token)
    except FileNotFoundError:
        # Another request may have just retired a terminally invalid session.
        # The browser will repoll Movie Night state and receive media_missing.
        client_disconnected = True
    except (ConnectionError, asyncio.CancelledError):
        # Browser reloads, seeks, tab closes, and mobile media-source swaps all
        # legitimately abandon an in-flight Range request. aiohttp may surface
        # those as ConnectionError rather than the narrower ConnectionResetError.
        # Treat that as a normal client disconnect instead of an application error.
        client_disconnected = True
    finally:
        if not client_disconnected:
            try:
                await response.write_eof()
            except ConnectionError:
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
