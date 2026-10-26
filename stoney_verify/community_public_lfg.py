from __future__ import annotations

"""Privacy-minimal, public recruitment for the existing Community Hub.

This is deliberately a read-only projection. It cannot create sessions,
join guilds, DM members, enumerate Discord users, or expose member identities.
"""

from collections import deque
from html import escape
import asyncio
import os
import re
import time
from urllib.parse import urlsplit
from typing import Any

from aiohttp import web

from stoney_verify.community_lfg_discovery import game_search_key

_DISCORD_INVITE = re.compile(
    r"https://(?:discord[.]gg/|discord[.]com/invite/)[A-Za-z0-9-]{2,64}\Z"
)
_ONE_MINUTE = 60.0
_per_ip: dict[str, deque[float]] = {}
_global_calls: deque[float] = deque()
_directory_slots = asyncio.Semaphore(3)


def verified_invite_url(raw: Any) -> str:
    """Never render arbitrary web redirects or Discord deep links."""
    value = str(raw or "").strip()
    if not _DISCORD_INVITE.fullmatch(value):
        return ""
    return value


def public_lfg_base_url() -> str:
    from stoney_verify.share_router_public_server import media_public_base_url

    value = str(
        os.getenv("DANK_LFG_PUBLIC_BASE_URL", "").strip()
        or media_public_base_url()
    ).rstrip("/")
    parsed = urlsplit(value)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        return ""
    if parsed.query or parsed.fragment or parsed.path not in ("", "/"):
        return ""
    return value


def _rate_allowed(remote: str, *, now: float) -> bool:
    # Use peer address, not a caller-supplied X-Forwarded-For header.
    key = str(remote or "unknown")[:100]
    if key not in _per_ip and len(_per_ip) >= 1024:
        return False
    for queue in (_global_calls, _per_ip.setdefault(key, deque())):
        while queue and queue[0] <= now - _ONE_MINUTE:
            queue.popleft()
    if len(_global_calls) >= 120 or len(_per_ip[key]) >= 20:
        return False
    _global_calls.append(now)
    _per_ip[key].append(now)
    if len(_per_ip) > 1024:
        for k in list(_per_ip):
            if not _per_ip[k]:
                _per_ip.pop(k, None)
    return True


def sanitize_directory(rows: list[dict[str, Any]], *, game: str = "") -> list[dict[str, Any]]:
    """Only declassified structured fields from the DB projection survive."""
    search = game_search_key(game)[:60]
    safe: list[dict[str, Any]] = []
    for row in rows[:100]:
        if not isinstance(row, dict):
            continue
        invite = verified_invite_url(row.get("invite_url"))
        name = str(row.get("game_name") or "").strip()
        # Filter original structured name only, not participant notes.
        name = " ".join("".join(ch for ch in name if ch.isprintable()).split())[:80]
        if not invite or not name:
            continue
        if search and search not in game_search_key(name):
            continue
        try:
            capacity = int(row.get("capacity") or 0)
        except (TypeError, ValueError):
            continue
        if capacity < 2 or capacity > 99:
            continue
        safe.append({
            "game": name,
            "capacity": capacity,
            "join_url": invite,
        })
        if len(safe) >= 40:
            break
    return safe


def _page(entries: list[dict[str, Any]], game: str) -> str:
    search = escape(game[:60], quote=True)
    cards = "".join(
        '<article class="card"><h2>' + escape(row["game"]) + '</h2>'
        + '<p>Public gaming group · Up to ' + str(row["capacity"]) + ' players</p>'
        + '<a rel="nofollow noopener noreferrer" href="'
        + escape(row["join_url"], quote=True) + '">Join the Discord community</a></article>'
        for row in entries
    )
    if not cards:
        cards = '<p class="empty">No public gaming groups match right now. Check back later.</p>'
    return """<!doctype html><html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="dark">
<title>Find Gaming Groups | Dank Shield LFG</title>
<meta name="description" content="Discover public, opt-in gaming groups and join their Discord communities.">
<style>
:root{color-scheme:dark;font-family:system-ui,-apple-system,sans-serif;background:#081410;color:#f1f9f3}
*{box-sizing:border-box}body{margin:0;padding:0 1rem 4rem}
main{max-width:900px;margin:0 auto}header{padding:3rem 0 1.3rem}
h1{font-size:clamp(1.8rem,5vw,3rem);margin:.25rem 0;color:#b2ffc5}
p{line-height:1.5;color:#c1d5c6}form{display:flex;gap:.65rem;flex-wrap:wrap;margin:1.8rem 0}
input,button{font:inherit;padding:.8rem 1rem;border-radius:.7rem}
input{background:#14281e;color:white;border:1px solid #456b53;flex:1;min-width:12rem}
button,.card a{background:#b2ffc5;color:#07180d;border:0;font-weight:700;cursor:pointer}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(235px,1fr));gap:1rem}
.card{background:#11271a;border:1px solid #31523a;padding:1.3rem;border-radius:1.1rem}
.card h2{margin-top:0;overflow-wrap:anywhere}.card a{display:inline-block;padding:.75rem 1rem;border-radius:.7rem;text-decoration:none;margin-top:.7rem}
.empty{padding:2rem 0}footer{margin-top:3rem;border-top:1px solid #284536;padding-top:1rem}
</style></head><body><main><header><p>🎮 COMMUNITY HUB</p>
<h1>Find players beyond your server</h1>
<p>These group listings were deliberately shared by their host and server staff. Joining the Discord community does not automatically join the gaming group. Respect each server's rules and age requirements.</p>
</header><form action="/lfg" method="get"><input type="search" name="game" maxlength="60"
aria-label="Search game" placeholder="Search games, e.g. Minecraft" value="""" + search + """">
<button type="submit">Find groups</button></form>
<section class="grid" aria-label="Public gaming groups">""" + cards + """</section>
<footer><p>Powered by Dank Shield Community Hub. No private groups, user profiles, or member lists are published.</p></footer>
</main></body></html>"""


async def public_lfg_directory(request: web.Request) -> web.Response:
    if not _rate_allowed(request.remote or "", now=time.monotonic()):
        raise web.HTTPTooManyRequests(text="Too many requests; retry later.")
    game = str(request.query.get("game", "")).strip()[:60]
    from stoney_verify import community_hub_service as hub
    try:
        async with _directory_slots:
            rows = await asyncio.wait_for(hub.list_public_lfg_directory(limit=100), timeout=5.0)
    except (Exception, asyncio.TimeoutError):
        # Missing migration or DB outage: fail closed, never produce faux data.
        return web.Response(
            status=503, text="Public gaming groups are temporarily unavailable.",
            headers={"Cache-Control": "no-store"},
        )
    safe = sanitize_directory(rows, game=game)
    headers = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}
    if request.path == "/lfg.json":
        return web.json_response(
            {"groups": safe, "count": len(safe)},
            headers=headers,
        )
    headers["Content-Security-Policy"] = (
        "default-src 'none'; style-src 'unsafe-inline'; "
        "form-action 'self'; base-uri 'none'; frame-ancestors 'none'"
    )
    return web.Response(
        text=_page(safe, game),
        content_type="text/html",
        charset="utf-8",
        headers=headers,
    )


def register_public_lfg_routes(app: web.Application) -> None:
    app.router.add_get("/lfg", public_lfg_directory)
    app.router.add_get("/lfg.json", public_lfg_directory)
