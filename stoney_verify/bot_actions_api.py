# stoney_verify/bot_actions_api.py
from __future__ import annotations

import os
from typing import Any

from aiohttp import web
import discord


def _env(name: str, default: str = "") -> str:
    return str(os.getenv(name, default) or "").strip()


def _truthy(value: str) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "on"}


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == "":
        return bool(default)
    return _truthy(str(raw))


def _deployment_mode() -> str:
    explicit = _env("STONEY_DEPLOYMENT_MODE", "").lower()
    if explicit:
        return explicit
    if _env_bool("STONEY_PRODUCTION_MODE", False):
        return "production"
    if _env_bool("STONEY_PUBLIC_MODE", False):
        return "public"
    return "development"


async def _gone(_: web.Request) -> web.Response:
    return web.json_response(
        {
            "ok": False,
            "error": "legacy_api_disabled",
            "message": "This legacy Bot Actions endpoint is disabled. Use the secured structured Bot API instead.",
        },
        status=410,
    )


async def _health(_: web.Request) -> web.Response:
    return web.json_response(
        {
            "ok": True,
            "service": "stoney-verify-legacy-bot-actions-disabled",
            "legacy_api_enabled": False,
        }
    )


def create_app(bot: discord.Client) -> web.Application:
    _ = bot
    app = web.Application()
    app.router.add_get("/health", _health)
    app.router.add_get("/api/health", _health)
    app.router.add_post("/api/verify/decision", _gone)
    app.router.add_post("/api/verify/submission", _gone)
    app.router.add_post("/tickets/sync-active", _gone)
    app.router.add_post("/api/tickets/sync-active", _gone)
    return app


async def start_bot_actions_server(bot: discord.Client) -> None:
    """
    Legacy Bot Actions API startup shim.

    Public-safe default: disabled.
    The secured structured Bot API is the supported path now.

    To expose a temporary compatibility health-only listener, set:
      BOT_ACTIONS_COMPAT_HEALTH_ONLY=true

    Do not re-enable legacy decision/submission routes for public hosting.
    """
    mode = _deployment_mode()

    if not _env_bool("BOT_ACTIONS_COMPAT_HEALTH_ONLY", False):
        print(
            "🧯 Legacy Bot Actions API disabled "
            f"(deployment={mode}; secured structured API should be used instead)"
        )
        return

    host = _env("BOT_ACTIONS_HOST", "127.0.0.1")
    port = int(_env("BOT_ACTIONS_PORT", "8080") or "8080")

    app = create_app(bot)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host=host, port=port)
    await site.start()

    print(
        "🧯 Legacy Bot Actions compatibility listener started in health-only mode "
        f"at http://{host}:{port}/health"
    )


__all__ = ["create_app", "start_bot_actions_server"]
