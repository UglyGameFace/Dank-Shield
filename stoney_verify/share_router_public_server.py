from __future__ import annotations

"""Standalone Share Router public listener. Never imports Cinema or torrent code."""

import os
from typing import Optional
from urllib.parse import urlsplit

from aiohttp import web

from stoney_verify.share_router_external_links import register_share_video_routes

_PUBLIC_RUNNER: Optional[web.AppRunner] = None
_PUBLIC_SITE: Optional[web.TCPSite] = None


def _config(primary: str, old: str, default: str) -> str:
    value = os.getenv(primary)
    # Empty new settings should not suppress a working existing Site URL.
    # An explicit off switch ("false") still takes precedence.
    if value is None or not str(value).strip():
        value = os.getenv(old, default)
    return str(value or default).strip()


def media_public_base_url() -> str:
    return _config("DANK_SHARE_VIDEO_PUBLIC_BASE_URL", "DANK_MEDIA_PUBLIC_BASE_URL", "").rstrip("/")


def media_bind_host() -> str:
    return _config("DANK_SHARE_VIDEO_BIND_HOST", "DANK_MEDIA_BIND_HOST", "0.0.0.0")


def media_bind_port() -> int:
    try:
        port = int(_config("DANK_SHARE_VIDEO_PORT", "DANK_MEDIA_PORT", "8080"))
    except ValueError:
        port = 8080
    return port if 1 <= port <= 65535 else 8080


def media_server_enabled() -> bool:
    enabled = _config("DANK_SHARE_VIDEO_SERVER_ENABLED", "DANK_MEDIA_SERVER_ENABLED", "true").lower()
    return enabled not in {"0", "false", "no", "off"}


def media_server_ready() -> bool:
    return _PUBLIC_RUNNER is not None and _PUBLIC_SITE is not None


def _validate_public_base_url() -> None:
    value = media_public_base_url()
    if not value:
        return
    parsed = urlsplit(value)
    host = str(parsed.hostname or "").lower()
    if parsed.scheme == "https" and parsed.hostname:
        return
    if parsed.scheme == "http" and host in {"localhost", "127.0.0.1", "::1"}:
        return
    raise RuntimeError("Share Router public URL must use HTTPS outside localhost development.")


async def _health(request: web.Request) -> web.Response:
    return web.json_response({"ok": True, "service": "dank_share_router_public", "cinema": "detached"})


async def _root(request: web.Request) -> web.Response:
    return web.json_response({"service": "dank_share_router_public", "message": "Only Share Router links are served here."})


async def start_share_router_public_server() -> bool:
    global _PUBLIC_RUNNER, _PUBLIC_SITE
    if media_server_ready():
        return True
    if not media_server_enabled():
        print("ℹ️ Share Router public listener disabled")
        return False

    _validate_public_base_url()
    app = web.Application(client_max_size=1024 * 1024)
    app.router.add_get("/", _root)
    app.router.add_get("/health", _health)
    register_share_video_routes(app)
    # Dedicated, read-only LFG pages; no Cinema/media functionality is restored.
    from stoney_verify.community_public_lfg import register_public_lfg_routes
    register_public_lfg_routes(app)

    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    host, port = media_bind_host(), media_bind_port()
    try:
        site = web.TCPSite(runner, host=host, port=port)
        await site.start()
    except Exception:
        await runner.cleanup()
        raise
    _PUBLIC_RUNNER, _PUBLIC_SITE = runner, site
    print(f"🔗 Share Router public server listening on {host}:{port}")
    return True


__all__ = [
    "media_public_base_url", "media_bind_host", "media_bind_port",
    "media_server_enabled", "media_server_ready", "start_share_router_public_server",
]
