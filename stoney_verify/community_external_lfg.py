from __future__ import annotations

"""Read-only external gaming discovery for Community Hub.

This provider never joins or creates third-party sessions. Community Hub's
own database remains the authority for all Discord-hosted gaming groups.
"""

import asyncio
import json
import os
import re
from typing import Any

import aiohttp


THE100_API = "https://www.the100.io/api/v2/gaming_sessions"
THE100_TOKEN_ENV = "DANK_COMMUNITY_THE100_API_TOKEN"
_MAX_BYTES = 131072
_MAX_RESULTS = 25
_VALID_ID = re.compile(r"[1-9][0-9]{0,18}\Z")


class ExternalDiscoveryError(Exception):
    """User-safe external provider failure (never includes credentials)."""


def the100_configured() -> bool:
    return bool(os.getenv(THE100_TOKEN_ENV, "").strip())


def _safe_label(value: Any, *, length: int = 90) -> str:
    if not isinstance(value, str):
        return ""
    clean = "".join(char for char in value if char.isprintable())
    return " ".join(clean.replace("@", "＠").split())[:length]


def normalize_the100_sessions(payload: Any, *, limit: int = _MAX_RESULTS) -> list[dict[str, str]]:
    """Normalize only documented upcoming-session records to safe external links.

    Reject unknown envelopes instead of quietly reporting an empty directory.
    Provider IDs are digits only; no untrusted URL reaches a Discord link.
    """
    if isinstance(payload, list):
        source = payload
    elif isinstance(payload, dict):
        source = next((payload[key] for key in ("gaming_sessions", "data", "sessions")
                       if isinstance(payload.get(key), list)), None)
    else:
        source = None
    if source is None:
        raise ExternalDiscoveryError("The100.io returned an unfamiliar session format.")

    result: list[dict[str, str]] = []
    seen: set[str] = set()
    for row in source[:100]:
        if not isinstance(row, dict):
            continue
        sid = str(row.get("id") or "").strip()
        if not _VALID_ID.fullmatch(sid) or sid in seen:
            continue
        game_obj = row.get("game") if isinstance(row.get("game"), dict) else {}
        activity = row.get("game_activity") if isinstance(row.get("game_activity"), dict) else {}
        game = _safe_label(row.get("game_name") or game_obj.get("name") or row.get("game_title")) or "Gaming Session"
        title = _safe_label(row.get("title") or activity.get("name") or row.get("name")) or game
        start = _safe_label(row.get("starts_at") or row.get("start_time") or "", length=48)
        seen.add(sid)
        result.append({
            "source": "The100.io",
            "game": game,
            "title": title,
            "starts_at": start,
            "url": f"https://www.the100.io/gaming_sessions/{sid}",
        })
        if len(result) >= max(1, min(int(limit), _MAX_RESULTS)):
            break
    return result


async def list_the100_sessions(*, limit: int = _MAX_RESULTS) -> list[dict[str, str]]:
    """One bounded provider read with no retries, cache, or automatic pings."""
    token = os.getenv(THE100_TOKEN_ENV, "").strip()
    if not token:
        raise ExternalDiscoveryError("The100.io is not configured on this bot host.")

    timeout = aiohttp.ClientTimeout(total=7, connect=3)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(
                THE100_API,
                headers={"Authorization": f"Token token={token}", "Accept": "application/json"},
            ) as response:
                if response.status in (401, 403):
                    raise ExternalDiscoveryError("The100.io API access was denied. Check the server-side token.")
                if response.status != 200:
                    raise ExternalDiscoveryError("The100.io is temporarily unavailable.")
                raw = await response.content.read(_MAX_BYTES + 1)
                if len(raw) > _MAX_BYTES:
                    raise ExternalDiscoveryError("The100.io returned too much data.")
                payload = json.loads(raw)
    except (aiohttp.ClientError, asyncio.TimeoutError, UnicodeError, json.JSONDecodeError) as exc:
        raise ExternalDiscoveryError("Could not read The100.io sessions right now.") from exc
    return normalize_the100_sessions(payload, limit=limit)
