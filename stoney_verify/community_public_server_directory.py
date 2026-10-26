from __future__ import annotations

"""Optional read-only public game-server discovery. Never exposes the API key.

Source: https://battlemetrics.ru/en/api
This provider lists game SERVERS, not people looking for teammates.
No automated joining, advertising, scraping, or third-party writes.
"""

import asyncio
import json
import os
import re
import time
from typing import Any

import aiohttp

from .community_external_lfg import ExternalDiscoveryError

API = "https://battlemetrics.ru/api/v1/servers"
SOURCE = "https://battlemetrics.ru/"
_GAME = re.compile(r"[a-z0-9_-]{2,35}\Z")
_COUNTRY = re.compile(r"[A-Z]{2}\Z")
_MAX_BYTES = 196608
_CACHE_SECONDS = 90.0
_MIN_INTERVAL = 3.0  # <=20 requests/minute per process, below 60/minute/key.
_lock = asyncio.Lock()
_cache: dict[tuple[str, str], tuple[float, tuple[dict[str, Any], ...]]] = {}
_next_request = 0.0
_backoff_until = 0.0


def normalize_servers(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict) or not isinstance(payload.get("servers"), list):
        raise ExternalDiscoveryError("Server directory returned an unfamiliar format.")
    if len(payload["servers"]) > 100:
        raise ExternalDiscoveryError("Server directory returned too many results.")
    output: list[dict[str, Any]] = []
    seen: set[int] = set()
    for item in payload["servers"]:
        if not isinstance(item, dict):
            continue
        sid = item.get("id")
        if type(sid) is not int or sid < 1 or sid in seen:
            continue
        seen.add(sid)
        # Never treat untrusted/duplicated or offline server counts as live.
        if item.get("trust") != "confirmed" or item.get("online") is not True:
            continue
        game = item.get("game")
        if not isinstance(game, str) or not _GAME.fullmatch(game):
            continue
        name = item.get("name")
        if not isinstance(name, str) or not name.strip():
            continue
        players = item.get("players")
        capacity = item.get("max_players")
        if type(players) is not int or players < 0:
            continue
        if type(capacity) is not int or capacity <= 0:
            continue
        # Only construct first-party detail links from validated IDs/game codes.
        output.append({
            "name": " ".join(name.replace("@", "＠").split())[:80],
            "game": game,
            "players": players,
            "capacity": capacity,
            "available_slots": max(0, capacity - players),
            "source": "battlemetrics.ru",
            "url": f"https://battlemetrics.ru/game/{game}/server/{sid}",
            "kind": "public_game_server",
        })
        if len(output) >= 20:
            break
    return output


async def list_public_servers(*, game: str, country: str = "") -> list[dict[str, Any]]:
    """Optional key-gated lookup with bounded caching, upstream pacing and 429 backoff."""
    game = str(game).strip().lower()
    country = str(country).strip().upper()
    if not _GAME.fullmatch(game) or (country and not _COUNTRY.fullmatch(country)):
        raise ExternalDiscoveryError("Choose a supported game and optional two-letter country.")
    key = os.getenv("DANK_LFG_BATTLEMETRICS_KEY", "").strip()
    if not key:
        raise ExternalDiscoveryError("Optional server directory is not configured.")
    cache_key = (game, country)
    global _next_request, _backoff_until
    async with _lock:
        now = time.monotonic()
        cached = _cache.get(cache_key)
        if cached and now < cached[0]:
            return [dict(x) for x in cached[1]]
        if now < max(_next_request, _backoff_until):
            if cached:
                return [dict(x) for x in cached[1]]
            raise ExternalDiscoveryError("Server directory is cooling down. Try again shortly.")
        _next_request = now + _MIN_INTERVAL
        params: dict[str, str | int] = {"game": game, "per_page": 20, "page": 1}
        if country:
            params["country"] = country
        timeout = aiohttp.ClientTimeout(total=8, connect=3)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(
                    API, params=params, allow_redirects=False,
                    headers={"Authorization": f"Bearer {key}", "Accept": "application/json"},
                ) as response:
                    if response.status == 429:
                        raw_delay = response.headers.get("Retry-After", "60")
                        try:
                            delay = max(3.0, min(float(raw_delay), 300.0))
                        except ValueError:
                            delay = 60.0
                        _backoff_until = time.monotonic() + delay
                        raise ExternalDiscoveryError("Server directory is rate limited.")
                    if response.status != 200:
                        raise ExternalDiscoveryError("Server directory is temporarily unavailable.")
                    raw = await response.content.read(_MAX_BYTES + 1)
                    if len(raw) > _MAX_BYTES:
                        raise ExternalDiscoveryError("Server directory response is too large.")
                    payload = json.loads(raw)
        except (aiohttp.ClientError, asyncio.TimeoutError, UnicodeError, json.JSONDecodeError) as exc:
            if cached:
                return [dict(x) for x in cached[1]]
            raise ExternalDiscoveryError("Server directory could not be reached.") from exc
        results = normalize_servers(payload)
        if len(_cache) >= 24:
            _cache.pop(next(iter(_cache)))
        _cache[cache_key] = (time.monotonic() + _CACHE_SECONDS, tuple(results))
        return [dict(x) for x in results]
