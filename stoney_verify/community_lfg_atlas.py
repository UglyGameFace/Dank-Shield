from __future__ import annotations

"""Keyless, bounded, read-only discovery from LFG Atlas public posts.

Docs: https://lfgatlas.pages.dev/developers/
This does NOT create posts or contact outside users. Posting is an independent
consent-sensitive write API and is deliberately outside this adapter.
"""

import asyncio
import json
import re
import time
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlsplit

import aiohttp

from .community_external_lfg import ExternalDiscoveryError

API = "https://lfgatlas-bot.jonsimm36.workers.dev/api/posts"
SITE = "https://lfgatlas.pages.dev/"
_MAX_BYTES = 196608
_MAX_RESULTS = 25
_CACHE_SECONDS = 90.0  # Provider documents ~60 second upstream caching.
_cache_lock = asyncio.Lock()
_cache: dict[tuple[str, str, bool, str], tuple[float, tuple[dict[str, Any], ...]]] = {}
_next_allowed_request = 0.0
_ALLOWED_SOURCE_HOSTS = frozenset({
    "lfgatlas.pages.dev",
    "lfgatlas-bot.jonsimm36.workers.dev",
})
ATLAS_TYPE_VALUES = ("lfg", "lfm", "server", "clan", "squad")
_TYPE_FILTERS = frozenset(("", *ATLAS_TYPE_VALUES))
_GAME_FILTER_RE = re.compile(r"[a-zA-Z0-9 +._'-]{0,55}\Z")
_REGION_FILTER_RE = re.compile(r"[a-zA-Z0-9 +_-]{0,40}\Z")


def _clean(value: Any, *, limit: int = 90) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join("".join(c if c.isprintable() else " " for c in value).replace("@", "＠").split())[:limit]


def _safe_details_url(value: Any) -> str:
    if not isinstance(value, str) or len(value) > 500:
        return SITE
    try:
        parts = urlsplit(value)
    except ValueError:
        return SITE
    try:
        allowed_port = parts.port in (None, 443)
    except ValueError:
        return SITE
    if (parts.scheme != "https" or parts.hostname not in _ALLOWED_SOURCE_HOSTS
            or parts.username or parts.password or not allowed_port
            or parts.fragment or "\\" in value):
        return SITE
    return value


def normalize_atlas_posts(payload: Any, *, limit: int = _MAX_RESULTS) -> list[dict[str, Any]]:
    """Defensively parse supported list envelopes; never use contact/user URLs."""
    if isinstance(payload, list):
        rows = payload
    elif isinstance(payload, dict):
        rows = next(
            (payload[key] for key in ("posts", "data", "results", "items")
             if isinstance(payload.get(key), list)), None,
        )
        # Tolerate a documented-style top-level data wrapper if its inner
        # payload is a list collection; unknown objects still fail closed.
        nested = payload.get("data")
        if rows is None and isinstance(nested, dict):
            rows = next(
                (nested[k] for k in ("posts", "items", "results")
                 if isinstance(nested.get(k), list)), None,
            )
    else:
        rows = None
    if rows is None:
        raise ExternalDiscoveryError("LFG Atlas returned an unfamiliar posts format.")
    if len(rows) > 100:
        raise ExternalDiscoveryError("LFG Atlas returned an oversized posts list.")
    now = datetime.now(timezone.utc)
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for post in rows:
        if not isinstance(post, dict):
            continue
        # Posts originate with unknown internet users. Do not display raw
        # contact, usernames, arbitrary descriptions, invite or party codes.
        title = _clean(post.get("title"), limit=80)
        if not title:
            continue
        expiration = post.get("expires_at")
        if isinstance(expiration, str) and expiration:
            try:
                ends = datetime.fromisoformat(expiration.replace("Z", "+00:00"))
                if ends.tzinfo is None or ends.astimezone(timezone.utc) <= now:
                    continue
            except ValueError:
                continue
        pid = str(post.get("id") or "").strip()[:100]
        unique = pid or title.casefold()
        if unique in seen:
            continue
        seen.add(unique)
        name = _clean(post.get("game_name") or post.get("game"), limit=65) or "Game not specified"
        if isinstance(post.get("game"), dict):
            name = _clean(post["game"].get("name"), limit=65) or name
        kind = _clean(post.get("type"), limit=20).upper()
        region = _clean(post.get("region"), limit=45)
        mode = _clean(post.get("mode"), limit=45)
        source = _clean(post.get("source"), limit=22) or "Atlas"
        ready = post.get("ready_now") is True
        # Any arbitrary URL from the post/contact MUST NOT become a Discord
        # action. Only the provider's own fixed origins can be navigated.
        link = _safe_details_url(post.get("url"))
        normalized.append({
            "game": name, "title": title, "type": kind, "region": region,
            "mode": mode, "source": source, "ready_now": ready, "url": link,
        })
        if len(normalized) >= max(1, min(int(limit), _MAX_RESULTS)):
            break
    return normalized


async def list_atlas_posts(
    *, game: str = "", region: str = "", ready_now: bool = False,
    group_type: str = "", limit: int = _MAX_RESULTS,
) -> list[dict[str, Any]]:
    """One explicitly user-initiated provider fetch, no writes or background jobs."""
    game = str(game or "").strip()
    region = str(region or "").strip()
    group_type = str(group_type or "").lower().strip()
    if not _GAME_FILTER_RE.fullmatch(game):
        raise ExternalDiscoveryError("Enter a normal game title (letters or numbers).")
    if not _REGION_FILTER_RE.fullmatch(region):
        raise ExternalDiscoveryError("Enter a short region, such as NA or EU West.")
    if group_type not in _TYPE_FILTERS:
        raise ExternalDiscoveryError("Choose a supported group type.")
    limit = max(1, min(int(limit), _MAX_RESULTS))
    cache_key = (game.lower(), region.lower(), bool(ready_now), group_type)
    global _next_allowed_request
    async with _cache_lock:
        now = time.monotonic()
        hit = _cache.get(cache_key)
        if hit and hit[0] > now:
            return [dict(row) for row in hit[1][:limit]]
        # One live call at most every 3 seconds across the whole process.
        if now < _next_allowed_request:
            raise ExternalDiscoveryError("LFG Atlas is cooling down. Retry shortly.")
        _next_allowed_request = now + 3.0
        params: dict[str, str | int] = {"limit": _MAX_RESULTS}
        if game:
            params["game"] = game
        if region:
            params["region"] = region
        if ready_now:
            params["ready_now"] = 1
        if group_type:
            params["type"] = group_type
        timeout = aiohttp.ClientTimeout(total=8, connect=3)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as client:
                async with client.get(
                    API, params=params, allow_redirects=False,
                    headers={
                        "Accept": "application/json",
                        "User-Agent": "DankShield-CommunityHub/1.0 (read-only; https://github.com/UglyGameFace/Dank-Shield)",
                    },
                ) as response:
                    if response.status == 429:
                        raise ExternalDiscoveryError("LFG Atlas is rate limiting requests. Try later.")
                    if response.status != 200:
                        raise ExternalDiscoveryError("LFG Atlas is unavailable. Open its website instead.")
                    body = await response.content.read(_MAX_BYTES + 1)
                    if len(body) > _MAX_BYTES:
                        raise ExternalDiscoveryError("LFG Atlas sent too much data.")
                    payload = json.loads(body)
        except (aiohttp.ClientError, asyncio.TimeoutError, UnicodeError, json.JSONDecodeError) as exc:
            raise ExternalDiscoveryError("Couldn't reach LFG Atlas. Open its website instead.") from exc
        result = normalize_atlas_posts(payload, limit=_MAX_RESULTS)
        if len(_cache) >= 24:
            _cache.pop(next(iter(_cache)))
        _cache[cache_key] = (time.monotonic() + _CACHE_SECONDS, tuple(result))
        return [dict(row) for row in result[:limit]]
