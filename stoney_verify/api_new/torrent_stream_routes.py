from __future__ import annotations

"""Signed HTTP byte-range routes for progressive torrent playback."""

import asyncio
import os
import shutil
import time
from typing import Any
from urllib.parse import quote, urlsplit

from aiohttp import web

from stoney_verify.torrent_streaming import (
    TorrentSessionUnavailableError,
    get_torrent_manager,
    media_content_type,
    parse_http_range,
)

_STREAM_CHUNK_BYTES = 1024 * 1024


_AUDIO_CHUNK_BYTES = 64 * 1024
_AUDIO_TRANSCODE_LOOP: asyncio.AbstractEventLoop | None = None
_AUDIO_TRANSCODE_SEMAPHORE: asyncio.Semaphore | None = None


def _audio_transcode_limit() -> int:
    try:
        value = int(str(os.getenv("DANK_CINEMA_AUDIO_TRANSCODE_LIMIT", "20") or "20"))
    except Exception:
        value = 20
    return max(1, min(value, 20))


def _audio_transcode_semaphore() -> asyncio.Semaphore:
    global _AUDIO_TRANSCODE_LOOP
    global _AUDIO_TRANSCODE_SEMAPHORE
    loop = asyncio.get_running_loop()
    if _AUDIO_TRANSCODE_LOOP is not loop:
        _AUDIO_TRANSCODE_LOOP = loop
        _AUDIO_TRANSCODE_SEMAPHORE = asyncio.Semaphore(_audio_transcode_limit())
    assert _AUDIO_TRANSCODE_SEMAPHORE is not None
    return _AUDIO_TRANSCODE_SEMAPHORE


def _audio_start_seconds(value: str) -> float:
    try:
        parsed = float(str(value or "0").strip())
    except Exception:
        return 0.0
    if not (parsed >= 0):
        return 0.0
    return min(parsed, 12 * 60 * 60.0)


def _ffmpeg_audio_command(
    ffmpeg: str,
    input_url: str,
    *,
    start_seconds: float = 0.0,
    track_index: int = 0,
) -> list[str]:
    command = [
        ffmpeg,
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
    ]
    if start_seconds > 0.05:
        command.extend(["-ss", f"{start_seconds:.3f}"])
    command.extend(
        [
            "-re",
            "-i",
            input_url,
            "-map",
            f"0:a:{max(0, min(int(track_index), 7))}",
            "-vn",
            "-sn",
            "-dn",
            "-c:a",
            "aac",
            "-threads",
            "1",
            "-b:a",
            "160k",
            "-ac",
            "2",
            "-ar",
            "48000",
            "-af",
            "aresample=async=1:first_pts=0",
            "-movflags",
            "frag_keyframe+empty_moov+default_base_moof",
            "-f",
            "mp4",
            "pipe:1",
        ]
    )
    return command


def _cast_cors_headers(request: web.Request) -> dict[str, str]:
    """Allow Google's Web Receiver to fetch an already-signed media URL.

    The stream URL remains HMAC-gated. CORS only lets the Cast receiver origin
    consume a URL that was already authorized for this Cinema session.
    """

    origin = str(request.headers.get("Origin", "") or "").strip()
    if not origin:
        return {}
    try:
        parsed = urlsplit(origin)
        host = str(parsed.hostname or "").casefold()
    except Exception:
        return {}
    if parsed.scheme != "https" or not (
        host == "gstatic.com"
        or host == "www.gstatic.com"
        or host.endswith(".gstatic.com")
    ):
        return {}
    return {
        "Access-Control-Allow-Origin": origin,
        "Access-Control-Allow-Methods": "GET, HEAD, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type, Accept-Encoding, Range",
        "Access-Control-Expose-Headers": (
            "Accept-Ranges, Content-Length, Content-Range, Content-Type"
        ),
        "Vary": "Origin",
    }


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

    cors_headers = _cast_cors_headers(request)

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
                **cors_headers,
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
            **cors_headers,
        }
        if partial:
            headers["Content-Range"] = (
                f"bytes {start}-{requested_end}/{session.file_size}"
            )
        return web.Response(status=status_code, headers=headers)

    first_end = min(requested_end, start + _STREAM_CHUNK_BYTES - 1)
    manager.record_stream_timing(
        session,
        consumer_key,
        event="request",
        start=start,
        end=requested_end,
        http_status=status_code,
    )
    try:
        plan = manager.prepare_playback_request(
            session,
            start,
            first_end,
            consumer_key=consumer_key,
        )
        startup_wait_end = max(first_end, plan.startup_wait_end)
        # A Range client only needs the first response chunk before it can begin
        # parsing media. Waiting for the entire adaptive startup corridor made
        # first byte all-or-nothing: one slow piece anywhere in that corridor
        # held every byte until the 20s buffering timeout. Keep prioritizing the
        # wider corridor, but gate the first 206 only on its first chunk.
        initial_wait_end = first_end if partial else startup_wait_end
        wait_started = time.monotonic()
        ready = await manager.wait_range(
            session,
            start,
            initial_wait_end,
            readahead_bytes=plan.target_bytes,
        )
        manager.record_stream_timing(
            session,
            consumer_key,
            event="wait",
            start=start,
            end=initial_wait_end,
            elapsed_ms=(time.monotonic() - wait_started) * 1000.0,
            ready=ready,
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
            headers={"Retry-After": "2", **cors_headers},
        )

    # For a byte-range request, advertise only the contiguous prefix that is
    # actually complete right now. The first chunk is guaranteed by the wait
    # above; any additional already-complete pieces can ride in the same 206.
    # Missing later pieces do not block first byte, and we still never promise a
    # Content-Length that the torrent cannot currently satisfy.
    buffered_end = startup_wait_end
    if partial:
        buffered_end = max(
            first_end,
            manager.contiguous_available_end(
                session,
                start,
                startup_wait_end,
            ),
        )
    end = _bounded_partial_response_end(
        start,
        requested_end,
        buffered_end,
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
        **cors_headers,
    }
    if partial:
        headers["Content-Range"] = f"bytes {start}-{end}/{session.file_size}"

    manager.schedule_metadata_probe(session)

    response = web.StreamResponse(status=status_code, headers=headers)
    await response.prepare(request)
    manager.record_stream_timing(
        session,
        consumer_key,
        event="headers",
    )

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
            if cursor == start:
                manager.record_stream_timing(
                    session,
                    consumer_key,
                    event="first_byte",
                )
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


async def torrent_audio_compat(request: web.Request) -> web.StreamResponse:
    """Transcode only the selected torrent's audio to browser-safe AAC.

    Video remains on the canonical byte-range stream. The Watch player uses this
    route as a synchronized audio sidecar only when metadata says the embedded
    audio codec is risky for browser playback.
    """

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
        raise web.HTTPUnauthorized(text="Invalid or expired Cinema audio token.")

    session = await manager.get(token)
    if session is None:
        raise web.HTTPNotFound(text="Cinema media session not found.")

    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise web.HTTPServiceUnavailable(
            text="FFmpeg audio compatibility is unavailable on this host."
        )

    start_seconds = _audio_start_seconds(str(request.query.get("start", "") or ""))
    # Only verified existing audio streams may be requested. The legacy AAC
    # compatibility URL (without a track parameter) continues to use track 0.
    selected_track = 0
    raw_track = request.query.get("track")
    if raw_track is not None:
        if not (str(raw_track).isascii() and str(raw_track).isdigit() and len(str(raw_track)) <= 2):
            raise web.HTTPBadRequest(text="Invalid Cinema audio track.")
        selected_track = int(raw_track)
        verified = session.verified_metadata if isinstance(session.verified_metadata, dict) else {}
        tracks = verified.get("audio_tracks") if verified.get("available") else None
        if (
            not isinstance(tracks, list)
            or selected_track >= min(len(tracks), 8)
        ):
            raise web.HTTPBadRequest(text="That audio track is not available.")
    try:
        port = int(str(os.getenv("DANK_MEDIA_PORT", "8080") or "8080"))
    except Exception:
        port = 8080
    port = port if 0 < port <= 65535 else 8080

    filename = quote(session.file_name, safe="")
    consumer_query = f"&cid={quote(consumer_key, safe='')}" if consumer_key else ""
    input_url = (
        f"http://127.0.0.1:{port}/media/torrent/stream/{session.token}/{filename}"
        f"?exp={quote(expires, safe='')}&sig={quote(signature, safe='')}{consumer_query}"
    )
    command = _ffmpeg_audio_command(
        ffmpeg,
        input_url,
        start_seconds=start_seconds,
        track_index=selected_track,
    )

    semaphore = _audio_transcode_semaphore()
    await semaphore.acquire()
    process: asyncio.subprocess.Process | None = None
    response = web.StreamResponse(
        status=200,
        headers={
            "Content-Type": "audio/mp4",
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
            "X-Dank-Cinema-Audio": "ffmpeg-aac",
        },
    )
    try:
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await response.prepare(request)
        stdout = process.stdout
        if stdout is None:
            raise web.HTTPServiceUnavailable(text="FFmpeg audio output was unavailable.")

        while True:
            chunk = await stdout.read(_AUDIO_CHUNK_BYTES)
            if not chunk:
                break
            await response.write(chunk)
        try:
            await response.write_eof()
        except ConnectionError:
            pass
        return response
    except asyncio.CancelledError:
        if process is not None and process.returncode is None:
            try:
                process.kill()
            except ProcessLookupError:
                pass
        raise
    except ConnectionError:
        if process is not None and process.returncode is None:
            try:
                process.kill()
            except ProcessLookupError:
                pass
        return response
    finally:
        if process is not None and process.returncode is None:
            try:
                process.kill()
            except ProcessLookupError:
                pass
        if process is not None:
            try:
                await asyncio.wait_for(process.wait(), timeout=3.0)
            except Exception:
                pass
        semaphore.release()


async def torrent_stream_options(request: web.Request) -> web.Response:
    """CORS preflight for a signed Cast media URL."""

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
    headers = _cast_cors_headers(request)
    if not headers:
        raise web.HTTPForbidden(text="That cross-origin media request is not allowed.")
    return web.Response(status=204, headers=headers)


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
    # Route registration runs during Discord setup_hook. Do not construct the
    # native libtorrent session merely to bind the public HTTP listener.
    app.router.add_get(
        "/media/torrent/stream/{token}/{filename}",
        torrent_stream,
        allow_head=True,
    )
    app.router.add_options(
        "/media/torrent/stream/{token}/{filename}",
        torrent_stream_options,
    )
    app.router.add_get(
        "/media/torrent/audio/{token}/{filename}",
        torrent_audio_compat,
        allow_head=False,
    )


def register_torrent_admin_routes(app: web.Application, server: Any) -> None:
    _ = server
    app.router.add_get("/media/torrent/status/{token}", torrent_status)
    app.router.add_post("/media/torrent/cancel/{token}", torrent_cancel)


__all__ = [
    "register_torrent_admin_routes",
    "register_torrent_public_routes",
    "_ffmpeg_audio_command",
    "torrent_audio_compat",
    "torrent_cancel",
    "torrent_status",
    "torrent_stream",
    "torrent_stream_options",
]
