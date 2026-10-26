from __future__ import annotations

"""Explicit host publishing to LFG Atlas, with private removal credentials.

Official API: https://lfgatlas.pages.dev/developers/
No automatic repost, scraping or contact harvesting. HTTP writes occur ONLY for
an explicitly submitted host/co-host job or removal of an existing owned post.
"""

import asyncio
import json
import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlsplit, parse_qs

import aiohttp

from . import community_hub_service as hub
from .community_public_lfg import verified_invite_url

API_ROOT = "https://lfgatlas-bot.jonsimm36.workers.dev"
API_POSTS = API_ROOT + "/api/posts"
SITE_URL = "https://lfgatlas.pages.dev/"
TABLE = "dank_community_atlas_publications"
_STATES = frozenset({"queued","publishing","posted","removing","removed","unknown","failed"})
_ID_RE = re.compile(r"[a-zA-Z0-9_-]{3,128}\Z")
_REGION_RE = re.compile(r"[A-Za-z0-9 +_-]{2,40}\Z")
_ALLOWED_WEB_HOSTS = frozenset({"lfgatlas.pages.dev", "lfgatlas-bot.jonsimm36.workers.dev"})
_GAME_SLUGS = (
    "cs2","dota2","pubg","fortnite","lol","valorant","apex",
    "marvel-rivals","r6","overwatch","deadlock","wardogs",
    "rocket-league","aion2","arc-raiders","destiny2","wow","ffxiv",
    "warframe","helldivers2","minecraft","valheim","rust","zomboid",
    "osrs","rs3","dragonwilds","enshrouded",
)
_GAME_ALIASES = {
    "counter strike 2": "cs2", "counter strike": "cs2",
    "dota 2": "dota2", "playerunknowns battlegrounds": "pubg",
    "league of legends": "lol", "apex legends": "apex",
    "marvel rivals": "marvel-rivals", "rainbow six siege": "r6",
    "overwatch 2": "overwatch", "rocket league": "rocket-league",
    "arc raiders": "arc-raiders", "destiny 2": "destiny2",
    "world of warcraft": "wow", "final fantasy xiv": "ffxiv",
    "helldivers 2": "helldivers2", "project zomboid": "zomboid",
    "runescape": "rs3", "old school runescape": "osrs",
    "minecraft java": "minecraft", "minecraft bedrock": "minecraft",
}


class AtlasPublishError(RuntimeError):
    pass


def atlas_game_slug(name: Any) -> str:
    cleaned = str(name or "").casefold().strip()
    normalized = re.sub(r"[^a-z0-9]+", " ", cleaned).strip()
    slug = _GAME_ALIASES.get(normalized, normalized.replace(" ", "-"))
    if slug in _GAME_SLUGS:
        return slug
    compact = normalized.replace(" ", "")
    return compact if compact in _GAME_SLUGS else ""


def _safe_public_url(raw: Any) -> str:
    url = str(raw or "").strip()
    if not 9 < len(url) < 400:
        return ""
    try:
        parts = urlsplit(url)
        if (parts.scheme != "https" or parts.hostname not in _ALLOWED_WEB_HOSTS
                or parts.username or parts.password or parts.port not in (None, 443)
                or parts.fragment or "\\" in url):
            return ""
    except ValueError:
        return ""
    if (parts.query or not re.fullmatch(r"/[a-zA-Z0-9/_-]{1,300}", parts.path)
            or any(ch in url for ch in ("@", "(", ")", "`", "\n", "\r"))):
        return ""
    return url


def _extract_post_receipt(payload: Any) -> tuple[str, str, str]:
    if not isinstance(payload, dict):
        raise AtlasPublishError("Provider returned an unexpected publishing response.")
    post = payload.get("post") if isinstance(payload.get("post"), dict) else payload
    manage = payload.get("manage")
    if not isinstance(manage, dict):
        raise AtlasPublishError("Provider did not return private deletion credentials.")
    token = str(manage.get("edit_token") or "")
    if not 10 <= len(token) <= 512 or not token.isprintable():
        raise AtlasPublishError("Provider did not return a usable private deletion token.")
    public_url = _safe_public_url(post.get("url") or payload.get("url"))
    if not public_url:
        raise AtlasPublishError("Provider did not return a trusted public post URL.")
    remote_id = str(post.get("id") or payload.get("id") or "").strip()
    if not _ID_RE.fullmatch(remote_id):
        # The published docs return a manage.delete_url and may omit id.
        # Use its ID only, never send a request to an arbitrary returned URL.
        manage_url = str(manage.get("delete_url") or "")
        try:
            parsed = urlsplit(manage_url)
            if parsed.scheme == "https" and parsed.hostname in _ALLOWED_WEB_HOSTS:
                match = re.search(r"/api/posts/([a-zA-Z0-9_-]{3,128})(?:/)?$", parsed.path)
                remote_id = match.group(1) if match else ""
        except ValueError:
            remote_id = ""
    if not _ID_RE.fullmatch(remote_id):
        raise AtlasPublishError("Provider did not identify the post for future removal.")
    return remote_id, public_url, token


def _db() -> Any:
    return hub._require_supabase()


async def _work(f: Any) -> Any:
    return await hub._db_call(f)


async def enqueue_host_publication(
    session_id: str, guild_id: int, actor_id: int, *,
    region: str, hours: int, adult_only: bool = False,
) -> dict[str, Any]:
    if not _REGION_RE.fullmatch(region.strip()) or hours not in (1, 2, 4, 12, 24):
        raise AtlasPublishError("Use a normal region (2–40 characters) and 1, 2, 4, 12 or 24 hours.")

    def run() -> list[dict[str, Any]]:
        result = _db().rpc("dank_queue_atlas_publication", {
            "p_session": str(session_id),
            "p_guild_id": str(guild_id),
            "p_actor_id": str(actor_id),
            "p_region": region.strip(),
            "p_hours": hours,
            "p_adult": bool(adult_only),
        }).execute()
        return [dict(x) for x in (result.data or [])]

    try:
        rows = await _work(run)
    except Exception as exc:
        raise AtlasPublishError("External publishing database is unavailable. Nothing was posted.") from exc
    if not rows:
        raise AtlasPublishError(
            "Nothing was posted. The session must be active, publicly shared, "
            "and hosted/co-hosted by you, with server recruiting enabled. "
            "A published or unresolved post may already exist, or the server has reached its external-post limit."
        )
    return public_status(rows[0])


def public_status(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if not row:
        return None
    return {
        "state": str(row.get("state") or "unknown"),
        "url": _safe_public_url(row.get("remote_url")),
        "region": str(row.get("region") or "")[:40],
        "expires_at": str(row.get("expires_at") or ""),
        "last_error": str(row.get("last_error") or "")[:140],
        "revoke_requested": row.get("revoke_requested") is True,
    }


async def get_publication_status(session_id: str, guild_id: int) -> dict[str, Any] | None:
    def read() -> dict[str, Any] | None:
        rows = _db().table(TABLE).select(
            "state,remote_url,region,expires_at,last_error,revoke_requested"
        ).eq("session_id", str(session_id)).eq("guild_id", str(guild_id)).limit(1).execute().data
        return dict(rows[0]) if rows else None
    try:
        return public_status(await _work(read))
    except Exception as exc:
        raise AtlasPublishError("External publication status is unavailable.") from exc


async def request_removal(session_id: str, guild_id: int) -> bool:
    def update() -> list[dict[str, Any]]:
        rows = _db().table(TABLE).update({
            "revoke_requested": True,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }).eq("session_id", str(session_id)).eq("guild_id", str(guild_id)).in_(
            "state", ["queued","publishing","posted","removing"]
        ).execute().data
        return [dict(x) for x in (rows or [])]
    try:
        return bool(await _work(update))
    except Exception as exc:
        raise AtlasPublishError("Could not request deletion from LFG Atlas.") from exc


async def _read_json(client: aiohttp.ClientSession, *, method: str,
                     url: str, payload: dict[str, Any]) -> tuple[int, Any]:
    async with client.request(
        method, url, json=payload, allow_redirects=False,
        headers={"Accept": "application/json",
                 "User-Agent": "DankShield-LFG/1.0 (+https://github.com/UglyGameFace/Dank-Shield)"},
    ) as response:
        raw = await response.content.read(24_577)
        if len(raw) > 24_576:
            raise AtlasPublishError("Provider returned an oversized response.")
        try:
            result = json.loads(raw) if raw else {}
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise AtlasPublishError("Provider returned invalid JSON.") from exc
        return response.status, result


async def send_post(row: dict[str, Any]) -> tuple[str, str, str]:
    if not verified_invite_url(row.get("contact")):
        raise AtlasPublishError("The server invitation is not valid for outside advertising.")
    slug = atlas_game_slug(row.get("game"))
    if not slug:
        raise AtlasPublishError("This game isn't currently supported by LFG Atlas. Use the public board instead.")
    expiry = datetime.fromisoformat(str(row["expires_at"]).replace("Z", "+00:00"))
    if expiry.tzinfo is None or expiry <= datetime.now(timezone.utc):
        raise AtlasPublishError("The gaming session is already expiring.")
    # No member IDs, session notes, party codes, arbitrary contacts, or secrets.
    payload = {
        "consent": True, "source": "dankshield", "game": slug,
        "type": "lfm", "title": str(row["title"])[:80],
        "adult": row.get("adult_only") is True,
        "region": str(row["region"])[:40], "contact": row["contact"],
        "hours": min(24, max(1, int((expiry-datetime.now(timezone.utc)).total_seconds()/3600)+1)),
    }
    timeout = aiohttp.ClientTimeout(total=9, connect=3)
    async with aiohttp.ClientSession(timeout=timeout) as client:
        status, result = await _read_json(client, method="POST", url=API_POSTS, payload=payload)
    if status in (200, 201):
        # A 200 duplicate without edit credentials is NOT under our control.
        return _extract_post_receipt(result)
    if status == 429:
        raise AtlasPublishError("Atlas's publishing rate limit is active. This request was not automatically retried.")
    if 400 <= status < 500:
        raise AtlasPublishError("LFG Atlas rejected the gaming group. Check the game, region, and provider rules.")
    raise AtlasPublishError("LFG Atlas could not confirm the post.")


async def delete_post(remote_id: str, token: str) -> bool:
    if not _ID_RE.fullmatch(str(remote_id or "")):
        raise AtlasPublishError("Stored Atlas post ID is invalid; deletion has been stopped.")
    if not isinstance(token, str) or len(token) < 10 or len(token) > 512:
        raise AtlasPublishError("Stored private deletion token is unavailable.")
    timeout = aiohttp.ClientTimeout(total=9, connect=3)
    async with aiohttp.ClientSession(timeout=timeout) as client:
        status, _ = await _read_json(
            client, method="DELETE",
            url=API_POSTS + "/" + remote_id,
            payload={"edit_token": token},
        )
    if status in (200, 202, 204, 404, 410):
        return True
    raise AtlasPublishError("The provider could not confirm that the advert was removed.")
