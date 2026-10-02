from __future__ import annotations

"""Dedicated public media server for signed progressive torrent playback."""

import os
from typing import Optional

from aiohttp import web

from stoney_verify.api_new.torrent_stream_routes import register_torrent_public_routes
from stoney_verify.torrent_streaming import get_torrent_manager

_MEDIA_RUNNER: Optional[web.AppRunner] = None
_MEDIA_SITE: Optional[web.TCPSite] = None


def _env_int(name: str, default: int) -> int:
    try:
        value = int(str(os.getenv(name, str(default)) or default).strip())
    except Exception:
        value = int(default)
    return value


def media_public_base_url() -> str:
    return str(os.getenv("DANK_MEDIA_PUBLIC_BASE_URL", "") or "").strip().rstrip("/")


def media_bind_host() -> str:
    return str(os.getenv("DANK_MEDIA_BIND_HOST", "127.0.0.1") or "127.0.0.1").strip()


def media_bind_port() -> int:
    port = _env_int("DANK_MEDIA_PORT", 8080)
    if port <= 0 or port > 65535:
        return 8080
    return port


def media_server_enabled() -> bool:
    return bool(media_public_base_url())


async def _health(request: web.Request) -> web.Response:
    _ = request
    manager = get_torrent_manager()
    return web.json_response(
        {
            "ok": True,
            "service": "dank_torrent_media",
            "public_base_url_configured": bool(manager.public_base_url),
            "stream_signing_configured": bool(manager.stream_secret),
        }
    )


async def start_torrent_media_server() -> bool:
    global _MEDIA_RUNNER, _MEDIA_SITE

    if _MEDIA_RUNNER is not None:
        return True

    if not media_server_enabled():
        print("ℹ️ Torrent media server disabled: DANK_MEDIA_PUBLIC_BASE_URL is not configured.")
        return False

    manager = get_torrent_manager()
    if not manager.stream_secret:
        raise RuntimeError(
            "Torrent media server refused to start: DANK_TORRENT_STREAM_SECRET is required."
        )

    app = web.Application(client_max_size=1024 * 1024)
    app.router.add_get("/health", _health)
    register_torrent_public_routes(app)

    runner = web.AppRunner(app, access_log=None)
    await runner.setup()

    host = media_bind_host()
    port = media_bind_port()
    site = web.TCPSite(runner, host=host, port=port)
    await site.start()

    _MEDIA_RUNNER = runner
    _MEDIA_SITE = site

    print(
        f"🎞️ Torrent media server started on {host}:{port} "
        f"public_base={media_public_base_url()}"
    )
    return True


__all__ = [
    "media_bind_host",
    "media_bind_port",
    "media_public_base_url",
    "media_server_enabled",
    "start_torrent_media_server",
]
