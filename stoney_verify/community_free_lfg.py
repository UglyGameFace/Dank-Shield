from __future__ import annotations

"""Approved read-only discovery of public Minecraft LFG player profiles.

No login, contact harvesting, scraping, automatic DMs or outbound posting.
Provider's documented endpoints: https://minecraftlfg.com/developers
Provider returns opted-in *player profiles*, not live Discord sessions.
"""

import asyncio
import json
import re
import time
from typing import Any

import aiohttp

from .community_external_lfg import ExternalDiscoveryError

MINECRAFT_PLAYERS_API = "https://minecraftlfg.com/api/v1/players"
MINECRAFT_SITE_URL = "https://minecraftlfg.com/"
_MAX_BYTES = 160_000
_CACHE_SECONDS = 180.0
_MAX_CACHED = 24
_VALID_EDITIONS = frozenset({"", "JAVA", "BEDROCK", "CROSSPLAY"})
_VALID_LANGUAGE = re.compile(r"[a-z]{2,3}\Z")
_cache_lock = asyncio.Lock()
_cache: dict[tuple[int, str, str], tuple[float, tuple[dict[str, Any], ...], int]] = {}
_NEXT_ALLOWED_REQUEST = 0.0


def _clean(value: Any, *, limit: int = 90) -> str:
    if not isinstance(value, str):
        return ""
    # Avoid mass mentions and newlines in rendered Discord components.
    return " ".join("".join(c if c.isprintable() else " " for c in value).replace("@", "＠").split())[:limit]


def normalize_minecraft_players(
    payload: Any, *, page: int, per_page: int = 20,
) -> tuple[list[dict[str, Any]], int]:
    """Parse only the provider's documented public profile subset."""
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        raise ExternalDiscoveryError("Minecraft LFG returned an unfamiliar player format.")
    records = payload["data"]
    if len(records) > 50:
        raise ExternalDiscoveryError("Minecraft LFG returned an oversized player listing.")
    meta = payload.get("meta") if isinstance(payload.get("meta"), dict) else {}
    try:
        pages = int(meta.get("pages", page))
    except (TypeError, ValueError, OverflowError):
        pages = page
    pages = max(page, min(pages, 1000))
    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for record in records:
        if not isinstance(record, dict):
            continue
        username = _clean(record.get("username"), limit=40)
        display = _clean(record.get("displayName"), limit=50) or username
        edition = _clean(record.get("edition"), limit=16).upper()
        if not username or not display or edition not in _VALID_EDITIONS - {""}:
            continue
        # Normalize safely even when Bedrock names include spaces.
        identifier = username.casefold()
        if identifier in seen:
            continue
        seen.add(identifier)
        headline = _clean(record.get("headline"), limit=160)
        language = _clean(record.get("language"), limit=12)
        modes = record.get("modes") if isinstance(record.get("modes"), list) else []
        modes = [
            _clean(mode, limit=25) for mode in modes[:5] if isinstance(mode, str)
        ]
        result.append({
            "username": username,
            "name": display,
            "edition": edition,
            "headline": headline,
            "language": language,
            "modes": [mode for mode in modes if mode],
            "source": "Minecraft LFG",
        })
        if len(result) >= per_page:
            break
    return result, pages


def provider_navigation() -> tuple[dict[str, str], ...]:
    """Verified human-browsed communities. No automated API reads or posts."""
    return (
        {"label": "Minecraft Buddies", "url": "https://www.reddit.com/r/MinecraftBuddies/", "game": "Minecraft"},
        {"label": "Minecraft Servers", "url": "https://www.reddit.com/r/mcservers/", "game": "Minecraft"},
        {"label": "Gamer Pals", "url": "https://www.reddit.com/r/GamerPals/", "game": "All games"},
        {"label": "Xbox Gamers", "url": "https://www.reddit.com/r/XboxGamers/", "game": "Xbox"},
        {"label": "PlayStation Friends", "url": "https://www.reddit.com/r/PSNFriends/", "game": "PlayStation"},
    )


async def list_minecraft_players(
    *, page: int = 1, edition: str = "", language: str = "",
) -> tuple[list[dict[str, Any]], int]:
    """Read-only server-side request with bounded rate, bytes and cache."""
    page = max(1, min(int(page), 5))
    edition = str(edition or "").upper().strip()
    language = str(language or "").lower().strip()
    if edition not in _VALID_EDITIONS:
        raise ExternalDiscoveryError("Choose Java, Bedrock, cross-play or all editions.")
    if language and not _VALID_LANGUAGE.fullmatch(language):
        raise ExternalDiscoveryError("Language must be a 2–3 letter code.")
    key = (page, edition, language)
    global _NEXT_ALLOWED_REQUEST
    async with _cache_lock:
        now = time.monotonic()
        entry = _cache.get(key)
        if entry and now < entry[0]:
            return [dict(row) for row in entry[1]], entry[2]
        # Never let many guilds turn button presses into an upstream request
        # stampede. A request at most every 3s is far below documented 60/min.
        if now < _NEXT_ALLOWED_REQUEST:
            raise ExternalDiscoveryError(
                "Minecraft LFG is cooling down. Retry in a moment or browse its website."
            )
        _NEXT_ALLOWED_REQUEST = now + 3.0
        params: dict[str, str | int] = {"page": page, "perPage": 20}
        if edition:
            params["edition"] = edition
        if language:
            params["language"] = language
        timeout = aiohttp.ClientTimeout(total=8, connect=3)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as client:
                async with client.get(
                    MINECRAFT_PLAYERS_API,
                    params=params,
                    headers={
                        "Accept": "application/json",
                        "User-Agent": "DankShield-CommunityHub/1.0 (read-only; https://github.com/UglyGameFace/Dank-Shield)",
                    },
                    allow_redirects=False,
                ) as response:
                    if response.status == 429:
                        raise ExternalDiscoveryError(
                            "Minecraft LFG's request limit is active. Try again later."
                        )
                    if response.status != 200:
                        raise ExternalDiscoveryError(
                            "Minecraft LFG is unavailable. Browse its website instead."
                        )
                    raw = await response.content.read(_MAX_BYTES + 1)
                    if len(raw) > _MAX_BYTES:
                        raise ExternalDiscoveryError(
                            "Minecraft LFG returned too much data."
                        )
                    payload = json.loads(raw)
        except (aiohttp.ClientError, asyncio.TimeoutError, UnicodeError, json.JSONDecodeError) as exc:
            raise ExternalDiscoveryError(
                "Minecraft LFG could not be reached. Browse its website instead."
            ) from exc
        rows, pages = normalize_minecraft_players(payload, page=page)
        if len(_cache) >= _MAX_CACHED:
            _cache.pop(next(iter(_cache)))
        _cache[key] = (time.monotonic() + _CACHE_SECONDS, tuple(rows), pages)
        return [dict(row) for row in rows], pages
