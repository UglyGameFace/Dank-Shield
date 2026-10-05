from __future__ import annotations

"""Dedicated public media server for signed progressive torrent playback."""

import os
import shutil
from typing import Optional
from urllib.parse import urlsplit

from aiohttp import web

from stoney_verify.api_new.torrent_stream_routes import register_torrent_public_routes
from stoney_verify.movie_night_web import register_movie_night_public_routes
from stoney_verify.cinema_site import (
    cinema_oauth_ready,
    cinema_oauth_redirect_uri,
    cinema_oauth_status,
    register_cinema_site_routes,
)
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
    # The public media server owns only health + signed media/watch routes.
    # Binding externally by default keeps TYPE=site deployments healthy even
    # before the operator finishes Movie Night URL/signing configuration.
    return str(os.getenv("DANK_MEDIA_BIND_HOST", "0.0.0.0") or "0.0.0.0").strip()


def media_bind_port() -> int:
    port = _env_int("DANK_MEDIA_PORT", 8080)
    if port <= 0 or port > 65535:
        return 8080
    return port


def media_server_enabled() -> bool:
    raw = str(os.getenv("DANK_MEDIA_SERVER_ENABLED", "true") or "true").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def media_server_ready() -> bool:
    return _MEDIA_RUNNER is not None and _MEDIA_SITE is not None


def _validate_public_base_url() -> None:
    raw = media_public_base_url()
    if not raw:
        return
    parsed = urlsplit(raw)
    host = str(parsed.hostname or "").lower()
    if parsed.scheme == "https":
        return
    if parsed.scheme == "http" and host in {"127.0.0.1", "localhost", "::1"}:
        return
    raise RuntimeError(
        "DANK_MEDIA_PUBLIC_BASE_URL must use HTTPS outside localhost development."
    )


async def _health(request: web.Request) -> web.Response:
    _ = request
    manager = get_torrent_manager()
    oauth = cinema_oauth_status()
    return web.json_response(
        {
            "ok": True,
            "service": "dank_torrent_media",
            "public_base_url_configured": bool(manager.public_base_url),
            "stream_signing_configured": bool(manager.stream_secret),
            "cinema_ffmpeg_audio_ready": bool(shutil.which("ffmpeg")),
            "cinema_standalone_login_configured": bool(cinema_oauth_ready()),
            "cinema_oauth_client_id_ready": bool(oauth["client_id_ready"]),
            "cinema_oauth_client_secret_ready": bool(oauth["client_secret_ready"]),
            "cinema_oauth_redirect_ready": bool(oauth["redirect_uri_ready"]),
            "cinema_oauth_redirect_uri": cinema_oauth_redirect_uri(),
        }
    )


async def _root(request: web.Request) -> web.Response:
    _ = request
    raise web.HTTPFound("/cinema")


async def start_torrent_media_server() -> bool:
    global _MEDIA_RUNNER, _MEDIA_SITE

    if _MEDIA_RUNNER is not None:
        return True

    if not media_server_enabled():
        print("ℹ️ Torrent media server disabled by DANK_MEDIA_SERVER_ENABLED=false.")
        return False

    _validate_public_base_url()
    manager = get_torrent_manager()
    if not manager.stream_secret:
        print(
            "⚠️ Torrent media server starting without stream signing; "
            "health remains available but media/watch access stays fail-closed "
            "until DANK_TORRENT_STREAM_SECRET is configured."
        )
    if not manager.public_base_url:
        print(
            "⚠️ Torrent media server starting without DANK_MEDIA_PUBLIC_BASE_URL; "
            "Site health remains available but no playback URL can be issued yet."
        )
    if not shutil.which("ffmpeg"):
        print(
            "⚠️ Dank Cinema FFmpeg AAC compatibility is unavailable; "
            "browser playback will fall back to the source audio codec."
        )
    if not cinema_oauth_ready():
        print(
            "⚠️ Dank Cinema standalone login is not configured; "
            "set DANK_CINEMA_DISCORD_CLIENT_SECRET and register "
            f"{cinema_oauth_redirect_uri() or '<public-base>/cinema/auth/callback'} "
            "as a Discord OAuth2 redirect URI."
        )

    app = web.Application(client_max_size=1024 * 1024)
    app.router.add_get("/", _root)
    app.router.add_get("/health", _health)
    register_torrent_public_routes(app)
    register_movie_night_public_routes(app)
    register_cinema_site_routes(app)

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
    "media_server_ready",
    "start_torrent_media_server",
]
