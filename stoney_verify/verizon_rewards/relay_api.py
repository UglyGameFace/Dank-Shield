from __future__ import annotations

import hmac
import os
from typing import Any, Optional

from aiohttp import web
import discord

_RUNNER: Optional[web.AppRunner] = None
_SITE: Optional[web.TCPSite] = None


def _log(message: str) -> None:
    try:
        print(f"📲 verizon_rewards.relay {message}")
    except Exception:
        pass


def _warn(message: str) -> None:
    try:
        print(f"⚠️ verizon_rewards.relay {message}")
    except Exception:
        pass


def _env_bool(name: str, default: bool = False) -> bool:
    raw = str(os.getenv(name, "") or "").strip().lower()
    if not raw:
        return bool(default)
    return raw in {"1", "true", "yes", "y", "on"}


def _env_int(name: str, default: int = 0) -> int:
    try:
        raw = str(os.getenv(name, "") or "").strip()
        return int(raw) if raw else int(default)
    except Exception:
        return int(default)


def _env_str(name: str, default: str = "") -> str:
    raw = os.getenv(name)
    if raw is None:
        return default
    text = str(raw).strip()
    return text if text else default


def _shared_secret() -> str:
    return _env_str("VERIZON_RELAY_SHARED_SECRET", _env_str("BOT_API_SHARED_SECRET", ""))


def _extract_auth(request: web.Request) -> list[str]:
    candidates: list[str] = []
    auth = str(request.headers.get("Authorization", "") or "").strip()
    if auth.lower().startswith("bearer "):
        candidates.append(auth[7:].strip())
    for header in ("X-Webhook-Secret", "X-Verizon-Relay-Secret", "X-API-Key"):
        value = str(request.headers.get(header, "") or "").strip()
        if value:
            candidates.append(value)
    query_secret = str(request.query.get("secret", "") or "").strip()
    if query_secret:
        candidates.append(query_secret)
    return candidates


def _authorized(request: web.Request) -> bool:
    secret = _shared_secret()
    if not secret:
        return False
    return any(hmac.compare_digest(candidate, secret) for candidate in _extract_auth(request) if candidate)


def _json_ok(**extra: Any) -> web.Response:
    payload = {"ok": True}
    payload.update(extra)
    return web.json_response(payload)


def _json_error(error: str, status: int = 400, **extra: Any) -> web.Response:
    payload = {"ok": False, "error": error}
    payload.update(extra)
    return web.json_response(payload, status=status)


async def _payload(request: web.Request) -> dict[str, Any]:
    try:
        data = await request.json()
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return {}


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(str(value).strip())
    except Exception:
        return default


async def health(request: web.Request) -> web.Response:
    return _json_ok(api="verizon_reward_relay", auth_configured=bool(_shared_secret()))


async def notification_endpoint(request: web.Request) -> web.Response:
    if not _authorized(request):
        return _json_error("Unauthorized", 401)

    data = await _payload(request)
    guild_id = _safe_int(data.get("guild_id") or request.query.get("guild_id"), 0)
    if guild_id <= 0:
        return _json_error("guild_id required")

    title = str(data.get("title") or data.get("notification_title") or data.get("app_title") or "").strip()
    body = str(data.get("body") or data.get("text") or data.get("notification_body") or data.get("message") or "").strip()
    app_name = str(data.get("app") or data.get("package") or data.get("source") or "android_notification").strip()

    if not title and not body:
        return _json_error("notification title/body required")

    try:
        from ..globals import bot
        from .service import scan_notification

        result = await scan_notification(
            bot=bot,
            guild_id=guild_id,
            title=title,
            body=body,
            source=f"android:{app_name}"[:120],
        )
        return _json_ok(result={k: v for k, v in result.items() if k != "results"})
    except Exception as e:
        _warn(f"notification endpoint failed guild={guild_id}: {type(e).__name__}: {e}")
        return _json_error("Failed to process notification", 500, detail=type(e).__name__)


async def start_relay_api(bot_instance: discord.Client) -> bool:
    global _RUNNER, _SITE

    if not _env_bool("VERIZON_RELAY_API_ENABLED", False):
        return False
    if _RUNNER is not None:
        return True

    secret = _shared_secret()
    if not secret or len(secret) < 16:
        _warn("relay API refused to start; set VERIZON_RELAY_SHARED_SECRET or BOT_API_SHARED_SECRET with at least 16 chars")
        return False

    host = _env_str("VERIZON_RELAY_BIND_HOST", "127.0.0.1")
    port = _env_int("VERIZON_RELAY_PORT", 8082)
    if port <= 0 or port > 65535:
        port = 8082

    app = web.Application()
    app.router.add_get("/health", health)
    app.router.add_post("/verizon/notification", notification_endpoint)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    _RUNNER = runner
    _SITE = site
    _log(f"started on {host}:{port} endpoint=/verizon/notification")
    return True


def attach_relay_listener(bot: discord.Client) -> None:
    if getattr(bot, "_verizon_rewards_relay_attached", False):
        return

    @bot.listen("on_ready")
    async def _verizon_rewards_relay_on_ready() -> None:
        await start_relay_api(bot)

    try:
        setattr(bot, "_verizon_rewards_relay_attached", True)
    except Exception:
        pass
    _log("listener attached")
