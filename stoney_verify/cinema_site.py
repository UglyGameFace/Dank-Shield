from __future__ import annotations

"""Full Dank Cinema website routes.

This is the product shell around the existing Watch player. It owns discovery,
library/search/details/profile/notification web APIs, while MovieNightRoom and
movie_night_web remain the playback/session authority.
"""

import asyncio
import html
import json
import os
import secrets
import time
from datetime import date
from pathlib import Path
from typing import Any, Mapping, Optional
from urllib.parse import urlencode, urlsplit

import aiohttp
import discord
from aiohttp import web

from .cinema_catalog import (
    CinemaEpisode,
    CinemaMedia,
    catalog_home,
    get_details,
    get_next_episode,
    get_season,
    recommendations_for_history,
    search_catalog,
)
from .cinema_library_service import (
    CinemaStorageUnavailable,
    InvalidCinemaState,
    delete_custom_list,
    get_cinema_user,
    library_snapshot,
    list_notifications,
    list_user_media,
    mark_notification_read,
    mark_watched,
    save_custom_list,
    set_custom_list_item,
    set_favorite,
    set_rating,
    set_watchlist,
    update_cinema_preferences,
)
from .cinema_library_intelligence import (
    home_intelligence,
    library_intelligence_snapshot,
)
from .cinema_site_auth import (
    CINEMA_GUILDS_COOKIE,
    CINEMA_GUILDS_TTL_SECONDS,
    CINEMA_IDENTITY_COOKIE,
    CINEMA_IDENTITY_TTL_SECONDS,
    CINEMA_OAUTH_STATE_COOKIE,
    CINEMA_SESSION_COOKIE,
    CINEMA_SESSION_TTL_SECONDS,
    cinema_guilds_value,
    cinema_identity_value,
    cinema_public_base,
    cinema_session_value,
    cinema_site_url,
    validate_cinema_guilds,
    validate_cinema_identity,
    validate_cinema_session,
    validate_cinema_site_access,
)
from .cinema_discovery_service import (
    list_recent_discoveries,
    search_discoveries,
)
from .cinema_feed_service import (
    CinemaFeedConflict,
    feed_state as cinema_feed_state,
    mutate_feed as mutate_cinema_feed,
)
from .cinema_feed_personalization import (
    CinemaFeedRuleError,
    delete_feed_rule,
    list_feed_rules,
    refresh_private_source,
    save_feed_rule,
)
from .media_source_registry import enabled_structured_sources, load_media_source_registry
from .media_source_resolver import (
    preview_custom_media_source,
    search_movie_sources,
)
from .cinema_media_identity import (
    catalog_metadata,
    filter_adult_provider_results,
    filter_outcome_for_catalog,
    looks_explicit_adult,
    parse_episode_query,
)
from .cinema_playback_service import (
    find_catalog_candidate,
    materialize_search_results,
    search_exact_episode_sources,
    search_exact_movie_sources,
    select_preferred_variant,
    start_room_variant,
)
from .movie_night import get_movie_night_manager
from .movie_night_preferences import load_movie_night_preferences
from .movie_night_web import movie_night_watch_url

_ASSET_DIR = Path(__file__).with_name("assets")
_CSS_PATH = _ASSET_DIR / "cinema_site.css"
_JS_PATH = _ASSET_DIR / "cinema_site.js"


_DISCORD_API_BASE = "https://discord.com/api/v10"
_CINEMA_OAUTH_TARGET_COOKIE = "dank_cinema_oauth_target"
_CINEMA_OAUTH_STATE_TTL_SECONDS = 10 * 60
_CINEMA_OAUTH_STATES: dict[str, tuple[float, int]] = {}


_SITE_MEMBER_CACHE_SECONDS = 5 * 60.0
_SITE_MEMBER_UNAVAILABLE_CACHE_SECONDS = 60.0
_SITE_MEMBER_VERIFIED_UNTIL: dict[tuple[int, int], float] = {}
_SITE_MEMBER_UNAVAILABLE_UNTIL: dict[tuple[int, int], float] = {}
_SITE_MEMBER_REVOKED: set[tuple[int, int]] = set()
_SITE_GUILD_CACHE_SECONDS = 5 * 60.0
_SITE_GUILD_ABSENT_CACHE_SECONDS = 60.0
_SITE_GUILD_UNAVAILABLE_CACHE_SECONDS = 30.0
_SITE_GUILD_REST_CACHE: dict[int, tuple[float, Any]] = {}
_SITE_GUILD_ABSENT_UNTIL: dict[int, float] = {}
_SITE_GUILD_UNAVAILABLE_UNTIL: dict[int, float] = {}
_CINEMA_AUTH_CONTRACT = "signed-session-v8-snowflake-safe"


def _clean_env_value(value: Any) -> str:
    text = str(value or "").strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {'"', "'"}:
        text = text[1:-1].strip()
    return text


def _discord_oauth_client_id() -> int:
    configured = _clean_env_value(
        os.getenv("DANK_CINEMA_DISCORD_CLIENT_ID", "")
    )
    try:
        if configured:
            return int(configured)
    except Exception:
        pass
    try:
        from .globals import bot
    except Exception:
        bot = None
    try:
        return int(
            getattr(bot, "application_id", 0)
            or getattr(getattr(bot, "user", None), "id", 0)
            or 0
        )
    except Exception:
        return 0


def _discord_oauth_client_secret() -> str:
    return _clean_env_value(
        os.getenv("DANK_CINEMA_DISCORD_CLIENT_SECRET", "")
        or os.getenv("DISCORD_CLIENT_SECRET", "")
        or ""
    )


def _discord_oauth_redirect_uri() -> str:
    override = _clean_env_value(
        os.getenv("DANK_CINEMA_DISCORD_REDIRECT_URI", "")
    )
    if override:
        try:
            parsed = urlsplit(override)
        except Exception:
            return ""
        if (
            parsed.scheme != "https"
            or not parsed.netloc
            or parsed.path.rstrip("/") != "/cinema/auth/callback"
            or parsed.query
            or parsed.fragment
        ):
            return ""
        return override.rstrip("/")

    base = _clean_env_value(cinema_public_base()).rstrip("/")
    if not base:
        return ""
    try:
        parsed = urlsplit(base)
    except Exception:
        return ""
    if parsed.scheme != "https" or not parsed.netloc:
        return ""
    return f"{base}/cinema/auth/callback"


def _discord_oauth_ready() -> bool:
    return bool(
        _discord_oauth_client_id() > 0
        and _discord_oauth_client_secret()
        and _discord_oauth_redirect_uri()
    )


def cinema_oauth_ready() -> bool:
    return _discord_oauth_ready()


def cinema_oauth_redirect_uri() -> str:
    return _discord_oauth_redirect_uri()


def cinema_oauth_status() -> dict[str, Any]:
    client_id = _discord_oauth_client_id()
    secret = _discord_oauth_client_secret()
    redirect_uri = _discord_oauth_redirect_uri()
    return {
        "ready": bool(client_id > 0 and secret and redirect_uri),
        "client_id_ready": bool(client_id > 0),
        "client_secret_ready": bool(secret),
        "redirect_uri_ready": bool(redirect_uri),
        "redirect_uri": redirect_uri,
    }


def _purge_oauth_states(now: Optional[float] = None) -> None:
    current = time.monotonic() if now is None else float(now)
    expired = [
        key
        for key, (expires_at, _guild_id) in _CINEMA_OAUTH_STATES.items()
        if float(expires_at) <= current
    ]
    for key in expired:
        _CINEMA_OAUTH_STATES.pop(key, None)


def _issue_oauth_state(target_guild: int = 0) -> str:
    _purge_oauth_states()
    state = secrets.token_urlsafe(32)
    _CINEMA_OAUTH_STATES[state] = (
        time.monotonic() + _CINEMA_OAUTH_STATE_TTL_SECONDS,
        max(0, int(target_guild or 0)),
    )
    return state


def _consume_oauth_state(value: str) -> Optional[int]:
    _purge_oauth_states()
    key = str(value or "").strip()
    if not key:
        return None
    row = _CINEMA_OAUTH_STATES.pop(key, None)
    if row is None:
        return None
    expires_at, target_guild = row
    if float(expires_at) <= time.monotonic():
        return None
    return max(0, int(target_guild or 0))


def _bot_client() -> Any:
    try:
        from .globals import bot
    except Exception:
        return None
    return bot


def _bot_guild(guild_id: int) -> Any:
    """Return only the gateway-cached guild, if present."""
    bot = _bot_client()
    if bot is None:
        return None
    try:
        return bot.get_guild(int(guild_id))
    except Exception:
        return None


async def _resolve_bot_guild(guild_id: int) -> tuple[str, Any]:
    """Resolve bot guild membership as present, absent, or unavailable.

    A cache miss from Client.get_guild() is not proof the bot is absent.
    Fall back to Discord REST via Client.fetch_guild() before denying Cinema.
    """

    gid = int(guild_id)
    if gid <= 0:
        return "absent", None

    cached = _bot_guild(gid)
    if cached is not None:
        _SITE_GUILD_REST_CACHE[gid] = (
            time.monotonic() + _SITE_GUILD_CACHE_SECONDS,
            cached,
        )
        _SITE_GUILD_ABSENT_UNTIL.pop(gid, None)
        _SITE_GUILD_UNAVAILABLE_UNTIL.pop(gid, None)
        return "present", cached

    now = time.monotonic()
    cached_row = _SITE_GUILD_REST_CACHE.get(gid)
    if cached_row is not None:
        expires_at, guild = cached_row
        if float(expires_at) > now and guild is not None:
            return "present", guild
        _SITE_GUILD_REST_CACHE.pop(gid, None)

    if _SITE_GUILD_ABSENT_UNTIL.get(gid, 0.0) > now:
        return "absent", None
    if _SITE_GUILD_UNAVAILABLE_UNTIL.get(gid, 0.0) > now:
        return "unavailable", None

    bot = _bot_client()
    fetch_guild = getattr(bot, "fetch_guild", None) if bot is not None else None
    if not callable(fetch_guild):
        _SITE_GUILD_UNAVAILABLE_UNTIL[gid] = now + _SITE_GUILD_UNAVAILABLE_CACHE_SECONDS
        return "unavailable", None

    try:
        guild = await fetch_guild(gid)
    except (discord.NotFound, discord.Forbidden) as exc:
        _SITE_GUILD_ABSENT_UNTIL[gid] = time.monotonic() + _SITE_GUILD_ABSENT_CACHE_SECONDS
        _SITE_GUILD_REST_CACHE.pop(gid, None)
        print(
            "⚠️ cinema_site guild resolution absent "
            f"guild={gid} error={type(exc).__name__}"
        )
        return "absent", None
    except (discord.HTTPException, asyncio.TimeoutError) as exc:
        _SITE_GUILD_UNAVAILABLE_UNTIL[gid] = time.monotonic() + _SITE_GUILD_UNAVAILABLE_CACHE_SECONDS
        print(
            "⚠️ cinema_site guild resolution unavailable "
            f"guild={gid} error={type(exc).__name__}"
        )
        return "unavailable", None
    except Exception as exc:
        _SITE_GUILD_UNAVAILABLE_UNTIL[gid] = time.monotonic() + _SITE_GUILD_UNAVAILABLE_CACHE_SECONDS
        print(
            "⚠️ cinema_site guild resolution unavailable "
            f"guild={gid} error={type(exc).__name__}"
        )
        return "unavailable", None

    if guild is None:
        _SITE_GUILD_UNAVAILABLE_UNTIL[gid] = time.monotonic() + _SITE_GUILD_UNAVAILABLE_CACHE_SECONDS
        return "unavailable", None

    _SITE_GUILD_REST_CACHE[gid] = (
        time.monotonic() + _SITE_GUILD_CACHE_SECONDS,
        guild,
    )
    _SITE_GUILD_ABSENT_UNTIL.pop(gid, None)
    _SITE_GUILD_UNAVAILABLE_UNTIL.pop(gid, None)
    print(f"🎞️ cinema_site guild resolution REST-confirmed guild={gid}")
    return "present", guild


def note_cinema_guild_join(guild_id: int, guild: Any = None) -> None:
    gid = int(guild_id)
    if gid <= 0:
        return
    _SITE_GUILD_ABSENT_UNTIL.pop(gid, None)
    _SITE_GUILD_UNAVAILABLE_UNTIL.pop(gid, None)
    if guild is not None:
        _SITE_GUILD_REST_CACHE[gid] = (
            time.monotonic() + _SITE_GUILD_CACHE_SECONDS,
            guild,
        )


def note_cinema_guild_remove(guild_id: int) -> None:
    gid = int(guild_id)
    if gid <= 0:
        return
    _SITE_GUILD_REST_CACHE.pop(gid, None)
    _SITE_GUILD_UNAVAILABLE_UNTIL.pop(gid, None)
    _SITE_GUILD_ABSENT_UNTIL[gid] = (
        time.monotonic() + _SITE_GUILD_ABSENT_CACHE_SECONDS
    )
    for key in [key for key in _SITE_MEMBER_VERIFIED_UNTIL if key[0] == gid]:
        _SITE_MEMBER_VERIFIED_UNTIL.pop(key, None)
    for key in [key for key in _SITE_MEMBER_UNAVAILABLE_UNTIL if key[0] == gid]:
        _SITE_MEMBER_UNAVAILABLE_UNTIL.pop(key, None)


def note_cinema_member_join(guild_id: int, user_id: int) -> None:
    gid = int(guild_id)
    uid = int(user_id)
    if gid <= 0 or uid <= 0:
        return
    key = (gid, uid)
    _SITE_MEMBER_REVOKED.discard(key)
    _SITE_MEMBER_UNAVAILABLE_UNTIL.pop(key, None)
    _SITE_MEMBER_VERIFIED_UNTIL[key] = time.monotonic() + _SITE_MEMBER_CACHE_SECONDS


def note_cinema_member_remove(guild_id: int, user_id: int) -> None:
    gid = int(guild_id)
    uid = int(user_id)
    if gid <= 0 or uid <= 0:
        return
    key = (gid, uid)
    _SITE_MEMBER_REVOKED.add(key)
    _SITE_MEMBER_VERIFIED_UNTIL.pop(key, None)
    _SITE_MEMBER_UNAVAILABLE_UNTIL.pop(key, None)


def _cinema_member_revoked(guild_id: int, user_id: int) -> bool:
    return (int(guild_id), int(user_id)) in _SITE_MEMBER_REVOKED


async def _site_member_state(guild_id: int, user_id: int) -> str:
    """Return present, absent, or unavailable without conflating REST failures.

    Only a definitive Discord NotFound or a canonical member-remove event means
    the user is absent. Rate limits, forbidden REST reads, timeouts, and other
    transport failures are availability problems, not proof that membership
    disappeared.
    """

    gid = int(guild_id)
    uid = int(user_id)
    if gid <= 0 or uid <= 0:
        return "absent"

    guild_state, guild = await _resolve_bot_guild(gid)
    if guild_state == "absent":
        return "bot_absent"
    if guild_state != "present" or guild is None:
        return "unavailable"

    key = (gid, uid)
    if key in _SITE_MEMBER_REVOKED:
        return "absent"

    now = time.monotonic()
    try:
        cached = guild.get_member(uid)
    except Exception:
        cached = None
    if cached is not None:
        note_cinema_member_join(gid, uid)
        return "present"

    if _SITE_MEMBER_VERIFIED_UNTIL.get(key, 0.0) > now:
        return "present"
    if _SITE_MEMBER_UNAVAILABLE_UNTIL.get(key, 0.0) > now:
        return "unavailable"

    fetch_member = getattr(guild, "fetch_member", None)
    if not callable(fetch_member):
        _SITE_MEMBER_UNAVAILABLE_UNTIL[key] = now + _SITE_MEMBER_UNAVAILABLE_CACHE_SECONDS
        return "unavailable"
    try:
        member = await fetch_member(uid)
    except discord.NotFound:
        # A REST NotFound is authoritative for this request, but it must not
        # become a process-lifetime revocation. Only Discord's canonical
        # on_member_remove event owns durable in-process revocation state.
        _SITE_MEMBER_VERIFIED_UNTIL.pop(key, None)
        _SITE_MEMBER_UNAVAILABLE_UNTIL.pop(key, None)
        return "absent"
    except (discord.Forbidden, discord.HTTPException, asyncio.TimeoutError) as exc:
        _SITE_MEMBER_UNAVAILABLE_UNTIL[key] = time.monotonic() + _SITE_MEMBER_UNAVAILABLE_CACHE_SECONDS
        print(
            "⚠️ cinema_site membership verification unavailable "
            f"guild={gid} user={uid} error={type(exc).__name__}"
        )
        return "unavailable"
    except Exception as exc:
        _SITE_MEMBER_UNAVAILABLE_UNTIL[key] = time.monotonic() + _SITE_MEMBER_UNAVAILABLE_CACHE_SECONDS
        print(
            "⚠️ cinema_site membership verification unavailable "
            f"guild={gid} user={uid} error={type(exc).__name__}"
        )
        return "unavailable"

    if member is None:
        _SITE_MEMBER_UNAVAILABLE_UNTIL[key] = time.monotonic() + _SITE_MEMBER_UNAVAILABLE_CACHE_SECONDS
        return "unavailable"

    note_cinema_member_join(gid, uid)
    return "present"


def _cached_member_guilds(user_id: int) -> list[Any]:
    try:
        from .globals import bot
    except Exception:
        bot = None
    guilds = list(getattr(bot, "guilds", ()) or ()) if bot is not None else []
    output: list[Any] = []
    for guild in guilds:
        try:
            if guild.get_member(int(user_id)) is not None:
                output.append(guild)
        except Exception:
            continue
    return output


def _recent_oauth_guild_proof(
    request: web.Request,
    user_id: int,
    guild_id: int,
) -> bool:
    """Accept the short-lived signed guild list returned by Discord OAuth.

    Standalone Cinema login already asks Discord for the member's current guilds.
    Requiring a second bot REST member lookup on every Home/Search/Profile API
    request made valid standalone browsing depend on bot member-cache/REST health.
    The signed guild proof lasts only CINEMA_GUILDS_TTL_SECONDS and is accepted
    only while Dank Shield itself still shares that guild.
    """

    uid = int(user_id)
    gid = int(guild_id)
    if (
        uid <= 0
        or gid <= 0
        or _cinema_member_revoked(gid, uid)
    ):
        return False
    allowed = validate_cinema_guilds(
        uid,
        str(request.cookies.get(CINEMA_GUILDS_COOKIE, "") or ""),
    )
    return gid in allowed


def _auth_state_label(present: bool, valid: bool) -> str:
    if not present:
        return "missing"
    return "valid" if valid else "invalid"


def _cinema_auth_debug_payload(request: web.Request) -> dict[str, Any]:
    try:
        guild_id = int(request.match_info.get("guild_id") or 0)
    except Exception:
        guild_id = 0

    signed_values = {
        "uid": str(request.query.get("uid", "") or ""),
        "exp": str(request.query.get("exp", "") or ""),
        "sig": str(request.query.get("sig", "") or ""),
    }
    signed_present = any(bool(value) for value in signed_values.values())
    signed_complete = all(bool(value) for value in signed_values.values())
    signed_uid = (
        validate_cinema_site_access(
            guild_id,
            signed_values["uid"],
            signed_values["exp"],
            signed_values["sig"],
        )
        if signed_complete and guild_id > 0
        else None
    )

    session_raw = str(request.cookies.get(CINEMA_SESSION_COOKIE, "") or "")
    session_uid = (
        validate_cinema_session(guild_id, session_raw)
        if session_raw and guild_id > 0
        else None
    )
    identity_raw = str(request.cookies.get(CINEMA_IDENTITY_COOKIE, "") or "")
    identity_uid = validate_cinema_identity(identity_raw) if identity_raw else None
    guilds_raw = str(request.cookies.get(CINEMA_GUILDS_COOKIE, "") or "")

    candidate_uid = signed_uid or session_uid or identity_uid
    guild_proof_valid = False
    if candidate_uid is not None and guild_id > 0 and guilds_raw:
        guild_proof_valid = guild_id in validate_cinema_guilds(
            int(candidate_uid),
            guilds_raw,
        )

    selected_source = (
        "signed"
        if signed_uid is not None
        else "session"
        if session_uid is not None
        else "identity"
        if identity_uid is not None
        else "none"
    )
    revoked = (
        _cinema_member_revoked(guild_id, int(candidate_uid))
        if candidate_uid is not None and guild_id > 0
        else False
    )

    return {
        "contract": _CINEMA_AUTH_CONTRACT,
        "route_guild_valid": guild_id > 0,
        "signed_query": _auth_state_label(signed_present, signed_uid is not None),
        "signed_query_complete": bool(signed_complete),
        "session_cookie": _auth_state_label(bool(session_raw), session_uid is not None),
        "identity_cookie": _auth_state_label(bool(identity_raw), identity_uid is not None),
        "guild_proof_cookie": _auth_state_label(bool(guilds_raw), guild_proof_valid),
        "selected_source": selected_source,
        "bot_guild_present": bool(guild_id > 0 and _bot_guild(guild_id) is not None),
        "member_revoked": bool(revoked),
        "cookie_header_present": bool(str(request.headers.get("Cookie", "") or "")),
        "request_secure": bool(getattr(request, "secure", False)),
        "forwarded_proto": str(request.headers.get("X-Forwarded-Proto", "") or "")[:16],
    }


async def cinema_auth_debug_api(request: web.Request) -> web.Response:
    payload = _cinema_auth_debug_payload(request)
    try:
        guild_id = int(request.match_info.get("guild_id") or 0)
    except Exception:
        guild_id = 0
    cache_present = bool(payload.get("bot_guild_present"))
    guild_state, _guild = await _resolve_bot_guild(guild_id)
    payload["bot_guild_cache_present"] = cache_present
    payload["bot_guild_state"] = guild_state
    payload["bot_guild_present"] = guild_state == "present"
    print(
        "🎞️ cinema_auth_debug "
        f"contract={payload['contract']} "
        f"signed={payload['signed_query']} "
        f"session={payload['session_cookie']} "
        f"identity={payload['identity_cookie']} "
        f"guild_proof={payload['guild_proof_cookie']} "
        f"selected={payload['selected_source']} "
        f"bot_cache={int(cache_present)} "
        f"bot_guild_state={guild_state} "
        f"revoked={int(payload['member_revoked'])} "
        f"cookie_header={int(payload['cookie_header_present'])}"
    )
    return web.json_response(
        payload,
        headers={
            "Cache-Control": "no-store",
            "X-Dank-Cinema-Auth-Contract": _CINEMA_AUTH_CONTRACT,
        },
    )


def _cinema_entry_html(
    *,
    user_id: int = 0,
    guilds: list[Any] | tuple[Any, ...] = (),
    error: str = "",
) -> str:
    rows: list[str] = []
    for guild in guilds:
        try:
            guild_id = int(getattr(guild, "id", 0) or 0)
        except Exception:
            guild_id = 0
        if guild_id <= 0:
            continue
        name = html.escape(str(getattr(guild, "name", "") or f"Server {guild_id}")[:100])
        rows.append(
            f'<a class="btn primary" href="/cinema/open/{guild_id}">'
            f'Open {name}</a>'
        )
    safe_error = html.escape(str(error or "")[:500])
    if user_id > 0:
        body = (
            '<h1>Choose your Dank Cinema</h1>'
            '<p class="section-sub">Only Discord servers you currently share with '
            'Dank Shield can open their Cinema site.</p>'
            + (f'<div class="state-card">{safe_error}</div>' if safe_error else "")
            + (
                '<div class="hero-actions">' + "".join(rows) + "</div>"
                if rows
                else (
                    '<div class="state-card">No eligible Cinema server is currently '
                    'available for this Discord account.</div>'
                    '<div class="hero-actions"><a class="btn primary" href="/cinema/login">'
                    'Refresh Discord access</a></div>'
                )
            )
        )
    else:
        body = (
            '<h1>Dank Cinema</h1>'
            '<p class="section-sub">Sign in with Discord. Access is granted only when '
            'you are currently a member of the exact server whose Cinema you open.</p>'
            + (f'<div class="state-card">{safe_error}</div>' if safe_error else "")
            + (
                '<div class="hero-actions"><a class="btn primary" href="/cinema/login">'
                'Continue with Discord</a></div>'
                if _discord_oauth_ready()
                else (
                    '<div class="state-card">Standalone Discord login is not ready on this deployment. '
                    f'Client ID: <strong>{"ready" if _discord_oauth_client_id() > 0 else "missing"}</strong> • '
                    f'Client secret: <strong>{"loaded" if bool(_discord_oauth_client_secret()) else "missing"}</strong> • '
                    f'Redirect URI: <strong>{html.escape(_discord_oauth_redirect_uri() or "missing/invalid")}</strong>. '
                    'The Discord Developer Portal redirect must match that URI exactly.</div>'
                )
            )
        )
    return f"""<!doctype html>
<html lang="en" data-quality="standard">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1,minimum-scale=1,viewport-fit=cover,interactive-widget=resizes-content">
  <meta name="theme-color" content="#030806">
  <title>Dank Cinema</title>
  <link rel="stylesheet" href="/cinema/assets/site.css?v=8">
</head>
<body>
  <div class="app-shell">
    <main class="page">
      <a class="brand-lockup" href="/cinema" aria-label="Dank Cinema home">
        <img class="brand-lockup-img" src="/movie/assets/dank-cinema-brand.webp?v=art-system-v5"
             alt="Dank Cinema — A feature of The 420 Lobby" width="1200" height="278">
      </a>
      <section class="section">{body}</section>
    </main>
  </div>
</body>
</html>"""


def _cinema_entry_response(
    *,
    user_id: int = 0,
    guilds: list[Any] | tuple[Any, ...] = (),
    error: str = "",
    status: int = 200,
) -> web.Response:
    return web.Response(
        text=_cinema_entry_html(user_id=user_id, guilds=guilds, error=error),
        status=int(status),
        content_type="text/html",
        headers={
            "Cache-Control": "no-store",
            "Content-Security-Policy": (
                "default-src 'self'; "
                "style-src 'self'; "
                "img-src 'self' https://image.tmdb.org; "
                "object-src 'none'; base-uri 'none'; frame-ancestors 'none'"
            ),
            "Referrer-Policy": "no-referrer",
            "X-Content-Type-Options": "nosniff",
        },
    )


async def cinema_entry_page(request: web.Request) -> web.Response:
    user_id = validate_cinema_identity(
        str(request.cookies.get(CINEMA_IDENTITY_COOKIE, "") or "")
    )
    if user_id is None:
        return _cinema_entry_response()

    allowed_ids = validate_cinema_guilds(
        int(user_id),
        str(request.cookies.get(CINEMA_GUILDS_COOKIE, "") or ""),
    )
    guilds = [
        guild
        for guild_id in allowed_ids
        if (guild := _bot_guild(int(guild_id))) is not None
    ]
    if not guilds:
        guilds = _cached_member_guilds(int(user_id))
    return _cinema_entry_response(user_id=int(user_id), guilds=guilds)


async def cinema_oauth_login(request: web.Request) -> web.Response:
    if not _discord_oauth_ready():
        status = cinema_oauth_status()
        return _cinema_entry_response(
            error=(
                "Standalone Discord login is not ready. "
                f"Client ID={'ready' if status['client_id_ready'] else 'missing'}, "
                f"client secret={'loaded' if status['client_secret_ready'] else 'missing'}, "
                f"redirect={status['redirect_uri'] or 'missing/invalid'}."
            ),
            status=503,
        )
    try:
        target_guild = int(str(request.query.get("guild_id", "") or "") or 0)
    except Exception:
        target_guild = 0
    state = _issue_oauth_state(target_guild)
    params = {
        "response_type": "code",
        "client_id": str(_discord_oauth_client_id()),
        "scope": "identify guilds",
        "state": state,
        "redirect_uri": _discord_oauth_redirect_uri(),
    }
    response = web.HTTPFound(
        "https://discord.com/oauth2/authorize?" + urlencode(params)
    )
    # Keep the old cookies only as compatibility breadcrumbs. Validation no
    # longer depends on them, so Discord/mobile browser handoff cannot break
    # the callback merely by returning in a fresh browser context.
    response.set_cookie(
        CINEMA_OAUTH_STATE_COOKIE,
        state,
        max_age=_CINEMA_OAUTH_STATE_TTL_SECONDS,
        httponly=True,
        secure=True,
        samesite="Lax",
        path="/cinema",
    )
    if target_guild > 0:
        response.set_cookie(
            _CINEMA_OAUTH_TARGET_COOKIE,
            str(target_guild),
            max_age=_CINEMA_OAUTH_STATE_TTL_SECONDS,
            httponly=True,
            secure=True,
            samesite="Lax",
            path="/cinema",
        )
    else:
        response.del_cookie(_CINEMA_OAUTH_TARGET_COOKIE, path="/cinema")
    return response


async def cinema_oauth_callback(request: web.Request) -> web.Response:
    returned_state = str(request.query.get("state", "") or "")
    code = str(request.query.get("code", "") or "").strip()
    target_guild = _consume_oauth_state(returned_state)
    if target_guild is None or not code or not _discord_oauth_ready():
        return _cinema_entry_response(
            error=(
                "Discord sign-in expired or could not be verified. "
                "Start again from this Cinema site."
            ),
            status=401,
        )

    timeout = aiohttp.ClientTimeout(total=12.0, connect=4.0, sock_read=8.0)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(
                f"{_DISCORD_API_BASE}/oauth2/token",
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": _discord_oauth_redirect_uri(),
                    "client_id": str(_discord_oauth_client_id()),
                    "client_secret": _discord_oauth_client_secret(),
                },
                headers={"Accept": "application/json"},
            ) as token_response:
                if token_response.status != 200:
                    failure_text = (await token_response.text())[:500]
                    print(
                        "⚠️ Dank Cinema OAuth token exchange rejected "
                        f"status={token_response.status} "
                        f"redirect={_discord_oauth_redirect_uri()} "
                        f"body={failure_text!r}"
                    )
                    return _cinema_entry_response(
                        error=(
                            "Discord rejected the Cinema login callback "
                            f"(HTTP {token_response.status}). Verify the client secret "
                            "and the exact OAuth2 redirect URI shown on /cinema."
                        ),
                        status=502,
                    )
                token_payload = await token_response.json(content_type=None)
            access_token = str(token_payload.get("access_token") or "").strip()
            if not access_token:
                raise RuntimeError("Discord did not return an access token.")
            headers = {
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/json",
            }
            async with session.get(
                f"{_DISCORD_API_BASE}/users/@me",
                headers=headers,
            ) as user_response:
                if user_response.status != 200:
                    raise RuntimeError(
                        f"Discord identity returned HTTP {user_response.status}."
                    )
                user_payload = await user_response.json(content_type=None)
            async with session.get(
                f"{_DISCORD_API_BASE}/users/@me/guilds",
                headers=headers,
            ) as guild_response:
                if guild_response.status != 200:
                    raise RuntimeError(
                        f"Discord guild lookup returned HTTP {guild_response.status}."
                    )
                user_guilds = await guild_response.json(content_type=None)
    except Exception:
        return _cinema_entry_response(
            error="Discord sign-in could not be completed. Try again.",
            status=502,
        )

    try:
        user_id = int(user_payload.get("id") or 0)
    except Exception:
        user_id = 0
    if user_id <= 0:
        return _cinema_entry_response(
            error="Discord did not return a valid account identity.",
            status=401,
        )

    user_guild_ids: set[int] = set()
    shared_ids: list[int] = []
    for row in user_guilds if isinstance(user_guilds, list) else []:
        if not isinstance(row, Mapping):
            continue
        try:
            guild_id = int(row.get("id") or 0)
        except Exception:
            guild_id = 0
        if guild_id <= 0:
            continue
        user_guild_ids.add(guild_id)
        if _bot_guild(guild_id) is not None:
            shared_ids.append(guild_id)

    if target_guild > 0:
        if target_guild not in user_guild_ids:
            return _cinema_entry_response(
                user_id=user_id,
                error=(
                    "This Discord account is not currently a member of that "
                    "Cinema server."
                ),
                status=403,
            )
        guild_state, _guild = await _resolve_bot_guild(target_guild)
        if guild_state == "absent":
            return _cinema_entry_response(
                user_id=user_id,
                error="Dank Shield is not installed in that Discord server.",
                status=403,
            )
        if guild_state != "present":
            return _cinema_entry_response(
                user_id=user_id,
                error="Discord could not verify that Cinema server right now. Try again shortly.",
                status=503,
            )
        shared_ids.append(target_guild)

    shared_ids = sorted(set(shared_ids))[:50]
    for shared_guild_id in shared_ids:
        note_cinema_member_join(shared_guild_id, user_id)

    if target_guild > 0:
        # Discord's fresh /users/@me/guilds response is the authoritative
        # standalone-login membership proof. Do not immediately require a
        # second bot REST member lookup that can fail on cache/REST health.
        response: web.StreamResponse = web.HTTPFound(
            f"/cinema/{target_guild}"
        )
        session_value = cinema_session_value(target_guild, user_id)
        if session_value:
            response.set_cookie(
                CINEMA_SESSION_COOKIE,
                session_value,
                max_age=CINEMA_SESSION_TTL_SECONDS,
                httponly=True,
                secure=True,
                samesite="Lax",
                path=f"/cinema/{target_guild}",
            )
    else:
        response = web.HTTPFound("/cinema")

    identity_value = cinema_identity_value(user_id)
    if identity_value:
        response.set_cookie(
            CINEMA_IDENTITY_COOKIE,
            identity_value,
            max_age=CINEMA_IDENTITY_TTL_SECONDS,
            httponly=True,
            secure=True,
            samesite="Lax",
            path="/cinema",
        )
    guilds_value = cinema_guilds_value(user_id, shared_ids)
    if guilds_value:
        response.set_cookie(
            CINEMA_GUILDS_COOKIE,
            guilds_value,
            max_age=CINEMA_GUILDS_TTL_SECONDS,
            httponly=True,
            secure=True,
            samesite="Lax",
            path="/cinema",
        )
    response.del_cookie(CINEMA_OAUTH_STATE_COOKIE, path="/cinema")
    response.del_cookie(_CINEMA_OAUTH_TARGET_COOKIE, path="/cinema")
    return response


async def cinema_open_guild(request: web.Request) -> web.Response:
    user_id = validate_cinema_identity(
        str(request.cookies.get(CINEMA_IDENTITY_COOKIE, "") or "")
    )
    if user_id is None:
        try:
            guild_id = int(request.match_info.get("guild_id") or 0)
        except Exception:
            guild_id = 0
        raise web.HTTPFound(f"/cinema/login?guild_id={guild_id}")

    try:
        guild_id = int(request.match_info.get("guild_id") or 0)
    except Exception:
        guild_id = 0
    if guild_id <= 0:
        return _cinema_entry_response(
            user_id=int(user_id),
            error="That Dank Cinema server is unavailable.",
            status=404,
        )
    guild_state, _guild = await _resolve_bot_guild(guild_id)
    if guild_state == "absent":
        return _cinema_entry_response(
            user_id=int(user_id),
            error="Dank Shield is not installed in that Discord server.",
            status=403,
        )
    if guild_state != "present":
        return _cinema_entry_response(
            user_id=int(user_id),
            error="Discord could not verify that Cinema server right now. Try again shortly.",
            status=503,
        )
    if not _recent_oauth_guild_proof(request, int(user_id), guild_id):
        membership_state = await _site_member_state(guild_id, int(user_id))
        if membership_state == "bot_absent":
            return _cinema_entry_response(
                user_id=int(user_id),
                error="Dank Shield is not installed in that Discord server.",
                status=403,
            )
        if membership_state == "absent":
            return _cinema_entry_response(
                user_id=int(user_id),
                error="You must currently be a member of that Discord server.",
                status=403,
            )
        if membership_state == "unavailable":
            return _cinema_entry_response(
                user_id=int(user_id),
                error=(
                    "Discord membership verification is temporarily unavailable. "
                    "Open Dank Cinema again from Discord to refresh access."
                ),
                status=503,
            )

    response = web.HTTPFound(f"/cinema/{guild_id}")
    session_value = cinema_session_value(guild_id, int(user_id))
    if session_value:
        response.set_cookie(
            CINEMA_SESSION_COOKIE,
            session_value,
            max_age=CINEMA_SESSION_TTL_SECONDS,
            httponly=True,
            secure=True,
            samesite="Lax",
            path=f"/cinema/{guild_id}",
        )
    return response


async def _site_identity(request: web.Request) -> tuple[int, int]:
    try:
        guild_id = int(request.match_info.get("guild_id") or 0)
    except Exception:
        guild_id = 0
    if guild_id <= 0:
        raise web.HTTPUnauthorized(text="Invalid Dank Cinema server.")

    signed_uid = validate_cinema_site_access(
        guild_id,
        str(request.query.get("uid", "") or ""),
        str(request.query.get("exp", "") or ""),
        str(request.query.get("sig", "") or ""),
    )
    if signed_uid is not None:
        # The Discord command created this guild+user bearer link from a live
        # interaction inside that guild. Let the first browser request exchange
        # that valid signed link for the normal HttpOnly Cinema cookies instead
        # of demanding a second REST member lookup before those cookies exist.
        # The bot must still currently share the target guild.
        guild_state, _guild = await _resolve_bot_guild(guild_id)
        if guild_state == "absent":
            raise web.HTTPForbidden(
                text="Dank Shield is not installed in this Discord server."
            )
        if guild_state != "present":
            raise web.HTTPServiceUnavailable(
                text="Discord could not verify this Cinema server right now."
            )
        if _cinema_member_revoked(guild_id, int(signed_uid)):
            print(
                "⚠️ cinema_site auth denied reason=member_remove_event_signed "
                f"guild={guild_id} user={int(signed_uid)}"
            )
            raise web.HTTPForbidden(
                text="Dank Cinema requires membership in this Discord server."
            )
        note_cinema_member_join(guild_id, int(signed_uid))
        return guild_id, int(signed_uid)

    session_uid = validate_cinema_session(
        guild_id,
        str(request.cookies.get(CINEMA_SESSION_COOKIE, "") or ""),
    )
    if session_uid is not None:
        # This exact-guild session cookie is minted only after one of the
        # authoritative entry proofs succeeds: a signed Discord interaction,
        # Discord OAuth guild membership, or a previously verified guild open.
        # Do not re-run guild.fetch_member() on every Home/Profile/Search API
        # request. That REST call is not an authentication primitive and can
        # fail independently of membership.
        guild_state, _guild = await _resolve_bot_guild(guild_id)
        if guild_state == "absent":
            print(
                "⚠️ cinema_site auth denied reason=bot_not_installed "
                f"guild={guild_id} user={int(session_uid)}"
            )
            raise web.HTTPForbidden(
                text="Dank Shield is not installed in this Discord server."
            )
        if guild_state != "present":
            raise web.HTTPServiceUnavailable(
                text="Discord could not verify this Cinema server right now."
            )
        if _cinema_member_revoked(guild_id, int(session_uid)):
            print(
                "⚠️ cinema_site auth denied reason=member_remove_event "
                f"guild={guild_id} user={int(session_uid)}"
            )
            raise web.HTTPForbidden(
                text="Dank Cinema requires membership in this Discord server."
            )
        return guild_id, int(session_uid)

    uid = validate_cinema_identity(
        str(request.cookies.get(CINEMA_IDENTITY_COOKIE, "") or "")
    )
    if uid is None:
        raise web.HTTPUnauthorized(text="Sign in to Dank Cinema again.")

    if _recent_oauth_guild_proof(request, int(uid), guild_id):
        guild_state, _guild = await _resolve_bot_guild(guild_id)
        if guild_state == "absent":
            raise web.HTTPForbidden(
                text="Dank Shield is not installed in this Discord server."
            )
        if guild_state != "present":
            raise web.HTTPServiceUnavailable(
                text="Discord could not verify this Cinema server right now."
            )
        note_cinema_member_join(guild_id, int(uid))
        return guild_id, int(uid)

    membership_state = await _site_member_state(guild_id, int(uid))
    if membership_state == "bot_absent":
        print(
            "⚠️ cinema_site auth denied reason=bot_not_installed "
            f"guild={guild_id} user={int(uid)}"
        )
        raise web.HTTPForbidden(
            text="Dank Shield is not installed in this Discord server."
        )
    if membership_state == "absent":
        print(
            "⚠️ cinema_site auth denied reason=identity_membership_absent "
            f"guild={guild_id} user={int(uid)}"
        )
        raise web.HTTPForbidden(
            text="Dank Cinema requires membership in this Discord server."
        )
    if membership_state == "unavailable":
        print(
            "⚠️ cinema_site auth deferred reason=identity_membership_unavailable "
            f"guild={guild_id} user={int(uid)}"
        )
        raise web.HTTPServiceUnavailable(
            text=(
                "Discord membership verification is temporarily unavailable. "
                "Open Dank Cinema from Discord to refresh access."
            )
        )
    return guild_id, int(uid)


def _can_manage_cinema(guild_id: int, user_id: int) -> bool:
    try:
        from .globals import bot
    except Exception:
        bot = None
    if bot is None:
        return False
    try:
        guild = bot.get_guild(int(guild_id))
    except Exception:
        guild = None
    if guild is None:
        return False
    if int(getattr(guild, "owner_id", 0) or 0) == int(user_id):
        return True
    try:
        member = guild.get_member(int(user_id))
    except Exception:
        member = None
    if member is None:
        return False
    permissions = getattr(member, "guild_permissions", None)
    return bool(
        getattr(permissions, "administrator", False)
        or getattr(permissions, "manage_guild", False)
    )


def _safe_discord_context(guild_id: int, user_id: int) -> dict[str, Any]:
    try:
        from .globals import bot
    except Exception:
        bot = None

    guild = None
    member = None
    if bot is not None:
        try:
            guild = bot.get_guild(int(guild_id))
        except Exception:
            guild = None
    if guild is not None:
        try:
            member = guild.get_member(int(user_id))
        except Exception:
            member = None
    if member is None and bot is not None:
        try:
            member = bot.get_user(int(user_id))
        except Exception:
            member = None

    avatar_url = ""
    if member is not None:
        try:
            raw = str(getattr(getattr(member, "display_avatar", None), "url", "") or "")
            if raw.startswith(
                ("https://cdn.discordapp.com/", "https://media.discordapp.net/")
            ):
                avatar_url = raw
        except Exception:
            avatar_url = ""

    return {
        "guild_id": int(guild_id),
        "guild_name": str(getattr(guild, "name", "") or "")[:100],
        "user_id": int(user_id),
        "user_name": str(
            getattr(member, "display_name", "")
            or getattr(member, "global_name", "")
            or getattr(member, "name", "")
            or user_id
        )[:80],
        "avatar_url": avatar_url,
        "discord_url": (
            f"https://discord.com/channels/{int(guild_id)}"
            if int(guild_id) > 0
            else ""
        ),
    }


def _row_is_adult(row: Mapping[str, Any]) -> bool:
    metadata = row.get("metadata") if isinstance(row.get("metadata"), Mapping) else {}
    return bool(metadata.get("adult", False))


def _filter_library_snapshot_for_policy(
    snapshot: Mapping[str, Any],
    *,
    adult_enabled: bool,
) -> dict[str, Any]:
    output = dict(snapshot)
    if adult_enabled:
        return output
    for key in (
        "watchlist",
        "continue_watching",
        "recently_watched",
        "watch_again",
        "favorites",
        "rated",
        "series_progress",
    ):
        rows = output.get(key)
        if isinstance(rows, list):
            output[key] = [
                row
                for row in rows
                if not (isinstance(row, Mapping) and _row_is_adult(row))
            ]
    return output


def _media_payload(row: Mapping[str, Any]) -> dict[str, Any]:
    metadata = row.get("metadata") if isinstance(row.get("metadata"), Mapping) else {}
    payload = {
        "media_type": str(row.get("media_type") or ""),
        "tmdb_id": int(row.get("tmdb_id") or 0),
        "season_number": int(row.get("season_number") or 0),
        "episode_number": int(row.get("episode_number") or 0),
        "title": str(row.get("title") or "")[:180],
        "progress_seconds": float(row.get("progress_seconds") or 0.0),
        "duration_seconds": float(row.get("duration_seconds") or 0.0),
        "completed": bool(row.get("completed")),
        "watchlisted": bool(row.get("watchlisted")),
        "favorite": bool(row.get("favorite")),
        "user_rating": int(row.get("rating") or 0),
        "play_count": max(0, int(row.get("play_count") or 0)),
        "first_watched_at": str(row.get("first_watched_at") or ""),
        "last_watched_at": str(row.get("last_watched_at") or ""),
        "last_completed_at": str(row.get("last_completed_at") or ""),
        "watchlisted_at": str(row.get("watchlisted_at") or ""),
        "favorite_at": str(row.get("favorite_at") or ""),
        "rated_at": str(row.get("rated_at") or ""),
        "adult": bool(metadata.get("adult", False)),
        "metadata": dict(metadata),
    }
    if payload["duration_seconds"] > 0:
        payload["progress_ratio"] = min(
            1.0,
            payload["progress_seconds"] / payload["duration_seconds"],
        )
    else:
        payload["progress_ratio"] = 0.0
    return payload


def _discovery_payload(row: Mapping[str, Any]) -> dict[str, Any]:
    metadata = (
        dict(row.get("metadata") or {})
        if isinstance(row.get("metadata"), Mapping)
        else {}
    )
    media_type = str(row.get("media_type") or "").strip().lower()
    try:
        tmdb_id = int(row.get("tmdb_id") or 0)
    except Exception:
        tmdb_id = 0
    return {
        "result_kind": "feed_discovery",
        "title": str(row.get("title") or "")[:180],
        "media_type": media_type,
        "tmdb_id": tmdb_id,
        "poster_url": str(metadata.get("poster_url") or ""),
        "backdrop_url": str(metadata.get("backdrop_url") or ""),
        "overview": str(metadata.get("overview") or "")[:900],
        "year": int(metadata.get("year") or 0),
        "rating": float(metadata.get("rating") or 0.0),
        "source_id": str(row.get("source_id") or ""),
        "source_label": str(metadata.get("source_label") or ""),
        "category": str(metadata.get("category") or "custom"),
        "playable": bool(row.get("playable", True)),
        "first_seen_at": str(row.get("first_seen_at") or ""),
        "metadata": metadata,
    }


def _active_rooms_payload(guild_id: int, user_id: int) -> list[dict[str, Any]]:
    manager = get_movie_night_manager()
    rows: list[dict[str, Any]] = []
    for room in manager.active_rooms_for_guild(int(guild_id)):
        if not manager.user_can_access(room, int(user_id)):
            continue
        candidate = room.current_candidate
        metadata: Mapping[str, Any] = {}
        if candidate is not None and isinstance(candidate.metadata, Mapping):
            catalog = candidate.metadata.get("catalog")
            metadata = catalog if isinstance(catalog, Mapping) else candidate.metadata
        rows.append(
            {
                "room_id": room.room_id,
                "mode": str(room.mode or "watch_party"),
                "is_host": int(room.host_id) == int(user_id),
                "viewer_count": len(manager.active_viewers(room)),
                "playback_state": str(room.playback_state or "paused"),
                "position_seconds": room.current_position(),
                "title": str(
                    metadata.get("title")
                    or getattr(candidate, "title", "")
                    or "Cinema session"
                )[:180],
                "media_type": str(
                    metadata.get("media_type")
                    or ("movie" if metadata.get("catalog_provider") == "tmdb" else "")
                )[:20],
                "tmdb_id": int(
                    metadata.get("tmdb_id")
                    or metadata.get("catalog_id")
                    or 0
                ),
                "series_id": int(metadata.get("series_id") or 0),
                "series_title": str(metadata.get("series_title") or "")[:180],
                "season_number": int(metadata.get("season_number") or 0),
                "episode_number": int(metadata.get("episode_number") or 0),
                "poster_url": str(metadata.get("poster_url") or ""),
                "backdrop_url": str(metadata.get("backdrop_url") or ""),
                "watch_url": movie_night_watch_url(room.room_id, int(user_id)),
            }
        )
    return rows


def _active_group_user_ids(guild_id: int, user_id: int) -> list[int]:
    manager = get_movie_night_manager()
    uid = int(user_id)
    for room in manager.active_rooms_for_guild(int(guild_id)):
        if str(getattr(room, "mode", "watch_party") or "watch_party") != "watch_party":
            continue
        active = {int(value) for value in manager.active_viewers(room)}
        active.add(int(room.host_id))
        if uid not in active or len(active) < 2:
            continue
        return sorted(active)[:20]
    return []


def _watch_party_picks(guild_id: int, user_id: int, *, limit: int = 14) -> list[dict[str, Any]]:
    """Return real canonical titles currently playing or queued in accessible rooms."""

    manager = get_movie_night_manager()
    output: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add_candidate(candidate: Any) -> None:
        if candidate is None or not isinstance(getattr(candidate, "metadata", None), Mapping):
            return
        raw = candidate.metadata.get("catalog")
        metadata = raw if isinstance(raw, Mapping) else {}
        kind = str(metadata.get("media_type") or "").strip().lower()
        tmdb_id = int(metadata.get("tmdb_id") or metadata.get("catalog_id") or 0)
        if kind not in {"movie", "tv", "episode"} or tmdb_id <= 0:
            return
        series_id = int(metadata.get("series_id") or 0)
        if kind == "episode" and series_id <= 0:
            return
        identity = (
            f"episode:{series_id}:{int(metadata.get('season_number') or 0)}:"
            f"{int(metadata.get('episode_number') or 0)}"
            if kind == "episode"
            else f"{kind}:{tmdb_id}"
        )
        if identity in seen:
            return
        seen.add(identity)
        output.append(
            {
                "result_kind": "watch_party_pick",
                "media_type": kind,
                "tmdb_id": tmdb_id,
                "series_id": series_id,
                "series_title": str(metadata.get("series_title") or "")[:180],
                "season_number": int(metadata.get("season_number") or 0),
                "episode_number": int(metadata.get("episode_number") or 0),
                "title": str(metadata.get("title") or getattr(candidate, "title", "") or "Cinema title")[:180],
                "year": int(metadata.get("year") or 0),
                "rating": float(metadata.get("rating") or 0.0),
                "overview": str(metadata.get("overview") or "")[:900],
                "poster_url": str(metadata.get("poster_url") or ""),
                "backdrop_url": str(metadata.get("backdrop_url") or ""),
                "watch_party_active": True,
            }
        )

    for room in manager.active_rooms_for_guild(int(guild_id)):
        if not manager.user_can_access(room, int(user_id)):
            continue
        current = (
            room.candidates.get(str(room.current_candidate_id))
            if room.current_candidate_id
            else None
        )
        add_candidate(current)
        for candidate_id in list(room.queue):
            add_candidate(room.candidates.get(str(candidate_id or "")))
            if len(output) >= max(1, min(int(limit), 30)):
                return output
    return output


async def _feed_discovery(guild_id: int, *, limit: int = 14) -> list[dict[str, Any]]:
    try:
        _raw, registry = await load_media_source_registry(int(guild_id), refresh=False)
    except Exception:
        return []
    sources = enabled_structured_sources(registry)[:5]
    if not sources:
        return []

    semaphore = asyncio.Semaphore(3)

    async def one(source: Any) -> list[dict[str, Any]]:
        async with semaphore:
            try:
                result = await preview_custom_media_source(source, query="movie", limit=4)
            except Exception:
                return []
        rows: list[dict[str, Any]] = []
        for variant in result.variants:
            rows.append(
                {
                    "title": variant.title,
                    "source_id": variant.source_id,
                    "source_label": variant.source_label,
                    "category": source.category,
                    "playable": True,
                    "metadata": dict(variant.metadata or {}),
                }
            )
        return rows

    groups = await asyncio.gather(*(one(source) for source in sources))
    output: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for group in groups:
        for row in group:
            key = (str(row["title"]).casefold(), str(row["source_id"]))
            if key in seen:
                continue
            seen.add(key)
            output.append(row)
            if len(output) >= limit:
                return output
    return output


async def _next_episode_rows(library: Mapping[str, Any]) -> list[dict[str, Any]]:
    progress_rows = list(library.get("series_progress") or [])[:8]
    if not progress_rows:
        return []

    async def one(row: Mapping[str, Any]) -> Optional[dict[str, Any]]:
        metadata = row.get("metadata") if isinstance(row.get("metadata"), Mapping) else {}
        series_id = int(metadata.get("series_id") or 0)
        current_season = int(row.get("season_number") or 0)
        current_episode = int(row.get("episode_number") or 0)
        if series_id <= 0:
            return None
        try:
            details = await get_details("tv", series_id)
            season_numbers = [
                int(item.get("season_number") or 0)
                for item in details.seasons
                if int(item.get("season_number") or 0) > 0
            ]
            candidates: list[CinemaEpisode] = []
            for season_number in sorted(set(season_numbers)):
                if season_number < current_season:
                    continue
                episodes = await get_season(series_id, season_number)
                for episode in episodes:
                    if season_number == current_season and episode.episode_number <= current_episode:
                        continue
                    candidates.append(episode)
                if candidates:
                    break
            if not candidates:
                return None
            episode = candidates[0]
            if episode.air_date:
                try:
                    if date.fromisoformat(episode.air_date) > date.today():
                        return None
                except Exception:
                    pass
            return {
                **episode.to_payload(),
                "series_title": details.media.title,
                "series_poster_url": details.media.poster_url,
            }
        except Exception:
            return None

    results = await asyncio.gather(*(one(row) for row in progress_rows))
    return [row for row in results if row is not None]


def _hero_from_sections(
    active_rooms: list[dict[str, Any]],
    continue_rows: list[Mapping[str, Any]],
    trending: list[dict[str, Any]],
) -> Optional[dict[str, Any]]:
    if active_rooms:
        room = dict(active_rooms[0])
        return {
            "kind": "session",
            "title": room.get("title") or "Continue your Cinema session",
            "subtitle": "Return to the live session without losing synchronization.",
            "backdrop_url": room.get("backdrop_url") or "",
            "poster_url": room.get("poster_url") or "",
            "action": {"kind": "watch_url", "url": room.get("watch_url") or "", "label": "Return to Theater"},
            "secondary": {"kind": "session", "room_id": room.get("room_id")},
        }
    if continue_rows:
        row = dict(continue_rows[0])
        metadata = row.get("metadata") if isinstance(row.get("metadata"), Mapping) else {}
        return {
            "kind": "resume",
            "title": row.get("title") or "Continue Watching",
            "subtitle": "Pick up where you left off.",
            "backdrop_url": str(metadata.get("backdrop_url") or ""),
            "poster_url": str(metadata.get("poster_url") or ""),
            "action": {
                "kind": "details",
                "media_type": row.get("media_type"),
                "tmdb_id": row.get("tmdb_id"),
                "label": "Resume",
            },
        }
    if trending:
        row = dict(trending[0])
        return {
            "kind": "discover",
            "title": row.get("title") or "Trending now",
            "subtitle": row.get("overview") or "Explore what people are watching.",
            "backdrop_url": row.get("backdrop_url") or "",
            "poster_url": row.get("poster_url") or "",
            "action": {
                "kind": "details",
                "media_type": row.get("media_type"),
                "tmdb_id": row.get("tmdb_id"),
                "label": "More Info",
            },
        }
    return None


async def cinema_home_api(request: web.Request) -> web.Response:
    guild_id, user_id = await _site_identity(request)
    try:
        library_task = asyncio.create_task(library_snapshot(user_id))
        profile_task = asyncio.create_task(get_cinema_user(user_id))
        catalog_task = asyncio.create_task(catalog_home())
        feeds_task = asyncio.create_task(_feed_discovery(guild_id))
        recent_added_task = asyncio.create_task(
            list_recent_discoveries(guild_id, limit=30)
        )
        notifications_task = asyncio.create_task(
            list_notifications(
                user_id,
                guild_id=guild_id,
                unread_only=True,
                limit=20,
            )
        )
        library, profile, catalog, feeds, recent_added, notifications = await asyncio.gather(
            library_task,
            profile_task,
            catalog_task,
            feeds_task,
            recent_added_task,
            notifications_task,
        )
    except CinemaStorageUnavailable as exc:
        raise web.HTTPServiceUnavailable(
            text="Dank Cinema library storage is unavailable. Your theater session still works."
        ) from exc
    except RuntimeError as exc:
        raise web.HTTPServiceUnavailable(text=str(exc)) from exc

    adult_enabled = await _guild_adult_content_enabled(guild_id)
    library = _filter_library_snapshot_for_policy(
        library,
        adult_enabled=adult_enabled,
    )
    active_rooms = _active_rooms_payload(guild_id, user_id)
    group_user_ids = _active_group_user_ids(guild_id, user_id)
    try:
        intelligence = await home_intelligence(
            guild_id,
            user_id,
            include_adult=adult_enabled,
            group_user_ids=group_user_ids,
        )
    except Exception:
        intelligence = {
            "because_you_watched": [],
            "recommended": [],
            "upcoming": [],
            "group_recommendations": [],
        }

    sections: list[dict[str, Any]] = []

    def add(key: str, title: str, rows: Any) -> None:
        clean = list(rows or [])
        if clean:
            sections.append({"key": key, "title": title, "items": clean})

    add(
        "continue",
        "Continue Watching",
        [_media_payload(row) for row in library.get("continue_watching") or []],
    )
    add(
        "upcoming",
        "Upcoming & Available From Your Shows",
        intelligence.get("upcoming") or [],
    )
    add(
        "trending",
        "Trending",
        [item.to_payload() for item in catalog.get("trending", ())],
    )
    add(
        "popular_movies",
        "Popular Movies",
        [item.to_payload() for item in catalog.get("popular_movies", ())],
    )
    add(
        "popular_tv",
        "Popular TV",
        [item.to_payload() for item in catalog.get("popular_tv", ())],
    )
    add(
        "recently_added",
        "Recently Added",
        [_discovery_payload(row) for row in recent_added],
    )
    add(
        "watchlist",
        "Your Watchlist",
        [_media_payload(row) for row in library.get("watchlist") or []],
    )
    add(
        "watch_again",
        "Watch Again",
        [_media_payload(row) for row in library.get("watch_again") or []],
    )
    add(
        "group_recommendations",
        "Good for the Room",
        intelligence.get("group_recommendations") or [],
    )
    add("feeds", "From Your Feeds", feeds)
    add(
        "because_you_watched",
        "Because You Watched",
        intelligence.get("because_you_watched") or [],
    )
    add(
        "recommended",
        "Recommended For You",
        intelligence.get("recommended") or [],
    )
    add(
        "recently_watched",
        "Recently Watched",
        [_media_payload(row) for row in library.get("recently_watched") or []],
    )

    trending_payload = [
        item.to_payload() for item in catalog.get("trending", ())
    ]
    hero = _hero_from_sections(
        active_rooms,
        list(library.get("continue_watching") or []),
        trending_payload,
    )
    return web.json_response(
        {
            "discord": _safe_discord_context(guild_id, user_id),
            "can_manage_cinema": _can_manage_cinema(guild_id, user_id),
            "profile": profile,
            "notifications_unread": len(notifications),
            "active_sessions": active_rooms,
            "hero": hero,
            "sections": sections,
        }
    )


async def _guild_adult_content_enabled(guild_id: int) -> bool:
    try:
        _raw, preferences = await load_movie_night_preferences(int(guild_id), refresh=False)
        return bool(preferences.adult_content_enabled)
    except Exception:
        return False


async def _search_episode_query(
    query: str,
    *,
    include_adult: bool = False,
) -> list[dict[str, Any]]:
    parsed = parse_episode_query(query)
    if parsed is None:
        return []
    series_query, season_number, episode_number = parsed
    try:
        matches = await search_catalog(
            series_query,
            limit=8,
            include_adult=bool(include_adult),
        )
    except Exception:
        return []
    series = next((item for item in matches if item.media_type == "tv"), None)
    if series is None:
        return []
    try:
        episodes = await get_season(series.tmdb_id, season_number)
    except Exception:
        return []
    episode = next(
        (
            item
            for item in episodes
            if int(item.episode_number) == int(episode_number)
        ),
        None,
    )
    if episode is None:
        return []
    return [
        {
            **episode.to_payload(),
            "result_kind": "episode",
            "series_title": series.title,
            "series_poster_url": series.poster_url,
            "metadata": {
                "series_id": int(series.tmdb_id),
                "series_title": series.title,
                "poster_url": series.poster_url,
                "backdrop_url": episode.still_url or series.backdrop_url,
            },
        }
    ]


async def cinema_search_api(request: web.Request) -> web.Response:
    guild_id, user_id = await _site_identity(request)
    query = " ".join(str(request.query.get("q", "") or "").split())[:180]
    if not query:
        return web.json_response({"query": "", "results": []})

    adult_enabled = await _guild_adult_content_enabled(guild_id)
    if not adult_enabled and looks_explicit_adult(query):
        return web.json_response(
            {
                "query": query,
                "results": [],
                "adult_content_enabled": False,
                "notice": "Adult-content Cinema search is disabled for this server.",
            }
        )

    catalog_task = asyncio.create_task(
        search_catalog(query, limit=30, include_adult=adult_enabled)
    )
    episode_task = asyncio.create_task(
        _search_episode_query(query, include_adult=adult_enabled)
    )
    media_task = asyncio.create_task(list_user_media(user_id))
    source_task = asyncio.create_task(search_movie_sources(guild_id, query))
    discovery_task = asyncio.create_task(
        search_discoveries(guild_id, query, limit=20)
    )
    catalog_rows, episode_rows, user_rows, source_result, discovery_rows = await asyncio.gather(
        catalog_task,
        episode_task,
        media_task,
        source_task,
        discovery_task,
        return_exceptions=True,
    )

    results: list[dict[str, Any]] = []
    seen: set[str] = set()

    if not isinstance(catalog_rows, Exception):
        for media in catalog_rows:
            payload = media.to_payload()
            payload["result_kind"] = "catalog"
            results.append(payload)
            seen.add(media.key)

    if not isinstance(episode_rows, Exception):
        for payload in episode_rows:
            key = str(payload.get("key") or "")
            if key and key not in seen:
                seen.add(key)
                results.append(payload)

    needle = query.casefold()
    if not isinstance(user_rows, Exception):
        for row in user_rows:
            if not adult_enabled and _row_is_adult(row):
                continue
            title = str(row.get("title") or "")
            if needle not in title.casefold():
                continue
            payload = _media_payload(row)
            key = (
                f"{payload['media_type']}:{payload['tmdb_id']}:"
                f"{payload['season_number']}:{payload['episode_number']}"
            )
            if key in seen:
                continue
            payload["result_kind"] = (
                "episode" if payload["media_type"] == "episode" else "library"
            )
            results.append(payload)
            seen.add(key)

    if not isinstance(source_result, Exception):
        source_result = filter_adult_provider_results(
            source_result,
            enabled=adult_enabled,
        )
        for variant in source_result.variants[:20]:
            key = f"source:{variant.source_id}:{variant.title.casefold()}"
            if key in seen:
                continue
            seen.add(key)
            results.append(
                {
                    "result_kind": "playable_source",
                    "title": variant.title,
                    "source_id": variant.source_id,
                    "source_label": variant.source_label,
                    "playable": True,
                    "metadata": dict(variant.metadata or {}),
                }
            )

    if not isinstance(discovery_rows, Exception):
        for row in discovery_rows:
            payload = _discovery_payload(row)
            key = (
                f"{payload.get('media_type')}:{payload.get('tmdb_id')}"
                if payload.get("tmdb_id")
                else f"feed:{payload.get('source_id')}:{str(payload.get('title') or '').casefold()}"
            )
            if key in seen:
                continue
            seen.add(key)
            results.append(payload)

    return web.json_response(
        {
            "query": query,
            "results": results[:60],
            "adult_content_enabled": adult_enabled,
        }
    )


async def cinema_details_api(request: web.Request) -> web.Response:
    _guild_id, user_id = await _site_identity(request)
    media_type = str(request.match_info.get("media_type") or "").strip().lower()
    try:
        tmdb_id = int(request.match_info.get("tmdb_id") or 0)
    except Exception:
        tmdb_id = 0
    if media_type not in {"movie", "tv"} or tmdb_id <= 0:
        raise web.HTTPBadRequest(text="Invalid Cinema title.")

    details_task = asyncio.create_task(get_details(media_type, tmdb_id))
    library_task = asyncio.create_task(list_user_media(user_id))
    details, rows = await asyncio.gather(details_task, library_task)
    adult_enabled = await _guild_adult_content_enabled(_guild_id)
    if bool(details.media.adult) and not adult_enabled:
        raise web.HTTPNotFound(
            text="This Cinema title is unavailable under the server content setting."
        )

    source_rows: list[dict[str, Any]] = []
    if media_type == "movie":
        try:
            source_outcome = await search_movie_sources(
                int(_guild_id),
                str(details.media.title),
                catalog_metadata=catalog_metadata(details.media),
            )
            source_outcome = filter_adult_provider_results(
                source_outcome,
                enabled=adult_enabled,
            )
            source_outcome = filter_outcome_for_catalog(
                source_outcome,
                catalog_metadata(details.media),
            )
            for variant in source_outcome.variants[:8]:
                seeds = max(0, int(variant.seeds or 0))
                leechers = max(0, int(variant.leechers or 0))
                if seeds >= 20:
                    health = "Strong"
                elif seeds >= 5:
                    health = "Good"
                elif seeds > 0:
                    health = "Limited"
                else:
                    health = "No active seeds reported"
                source_rows.append(
                    {
                        "source_id": str(variant.source_id or ""),
                        "source_label": str(variant.source_label or "Cinema source"),
                        "title": str(variant.title or "")[:180],
                        "file_size": int(variant.file_size or 0),
                        "seeds": seeds,
                        "leechers": leechers,
                        "health": health,
                        "playable": True,
                    }
                )
        except Exception:
            source_rows = []

    matching = [
        _media_payload(row)
        for row in rows
        if str(row.get("media_type") or "") == media_type
        and int(row.get("tmdb_id") or 0) == tmdb_id
        and int(row.get("season_number") or 0) == 0
        and int(row.get("episode_number") or 0) == 0
    ]
    episode_progress = [
        _media_payload(row)
        for row in rows
        if media_type == "tv"
        and str(row.get("media_type") or "") == "episode"
        and int(
            (
                row.get("metadata")
                if isinstance(row.get("metadata"), Mapping)
                else {}
            ).get("series_id")
            or 0
        )
        == tmdb_id
    ]
    active_sessions = _active_rooms_payload(_guild_id, user_id)
    if media_type == "movie":
        active_session = next(
            (
                room
                for room in active_sessions
                if str(room.get("media_type") or "") == "movie"
                and int(room.get("tmdb_id") or 0) == tmdb_id
            ),
            None,
        )
    else:
        active_session = next(
            (
                room
                for room in active_sessions
                if str(room.get("media_type") or "") == "episode"
                and int(room.get("series_id") or 0) == tmdb_id
            ),
            None,
        )
    host_session = next(
        (room for room in active_sessions if bool(room.get("is_host"))),
        None,
    )

    continue_episode: Optional[dict[str, Any]] = None
    if media_type == "tv" and episode_progress:
        latest = dict(episode_progress[0])
        if not bool(latest.get("completed")):
            latest_meta = (
                latest.get("metadata")
                if isinstance(latest.get("metadata"), Mapping)
                else {}
            )
            continue_episode = {
                "media_type": "episode",
                "series_id": tmdb_id,
                "series_title": details.media.title,
                "season_number": int(latest.get("season_number") or 0),
                "episode_number": int(latest.get("episode_number") or 0),
                "tmdb_id": int(latest.get("tmdb_id") or 0),
                "title": str(
                    latest_meta.get("episode_title")
                    or latest.get("title")
                    or "Episode"
                )[:180],
                "progress_seconds": float(latest.get("progress_seconds") or 0.0),
                "completed": False,
            }
        else:
            try:
                next_episode = await get_next_episode(
                    tmdb_id,
                    int(latest.get("season_number") or 0),
                    int(latest.get("episode_number") or 0),
                )
            except Exception:
                next_episode = None
            if next_episode is not None:
                continue_episode = {
                    **next_episode.to_payload(),
                    "series_title": details.media.title,
                    "completed": False,
                    "progress_seconds": 0.0,
                }

    return web.json_response(
        {
            "details": details.to_payload(),
            "library": matching[0] if matching else None,
            "episode_progress": episode_progress,
            "continue_episode": continue_episode,
            "sources": source_rows,
            "active_session": active_session,
            "host_session": host_session,
            "discord": _safe_discord_context(_guild_id, user_id),
        }
    )


async def cinema_season_api(request: web.Request) -> web.Response:
    _guild_id, user_id = await _site_identity(request)
    try:
        series_id = int(request.match_info.get("series_id") or 0)
        season_number = int(request.match_info.get("season_number") or 0)
    except Exception:
        raise web.HTTPBadRequest(text="Invalid season.")
    episodes_task = asyncio.create_task(get_season(series_id, season_number))
    media_task = asyncio.create_task(list_user_media(user_id))
    episodes, rows = await asyncio.gather(episodes_task, media_task)

    progress = {
        (
            int(row.get("season_number") or 0),
            int(row.get("episode_number") or 0),
        ): _media_payload(row)
        for row in rows
        if str(row.get("media_type") or "") == "episode"
        and int(
            (
                row.get("metadata")
                if isinstance(row.get("metadata"), Mapping)
                else {}
            ).get("series_id")
            or 0
        )
        == series_id
    }
    output: list[dict[str, Any]] = []
    for episode in episodes:
        payload = episode.to_payload()
        payload["progress"] = progress.get(
            (episode.season_number, episode.episode_number)
        )
        output.append(payload)
    return web.json_response({"episodes": output})


async def cinema_play_api(request: web.Request) -> web.Response:
    guild_id, user_id = await _site_identity(request)
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    if not isinstance(payload, Mapping):
        payload = {}

    room_id = str(payload.get("room_id") or "").strip()
    media_type = str(payload.get("media_type") or "").strip().lower()
    manager = get_movie_night_manager()
    room = manager.get(room_id)
    if (
        not room_id
        or room is None
        or room.ended
        or int(room.guild_id) != int(guild_id)
        or int(room.host_id) != int(user_id)
        or not manager.user_can_access(room, int(user_id))
    ):
        raise web.HTTPForbidden(
            text="A current Cinema room that you host is required to start playback from the website."
        )

    baseline = (
        str(room.stream_token or ""),
        str(room.current_candidate_id or ""),
        str(room.current_variant_id or ""),
    )

    if media_type == "movie":
        try:
            tmdb_id = int(payload.get("tmdb_id") or 0)
        except Exception:
            tmdb_id = 0
        if tmdb_id <= 0:
            raise web.HTTPBadRequest(text="Invalid movie identity.")
        try:
            details = await get_details("movie", tmdb_id)
            if bool(details.media.adult) and not await _guild_adult_content_enabled(guild_id):
                raise web.HTTPForbidden(
                    text="Adult-content Cinema playback is disabled for this server."
                )
            metadata, query, outcome = await search_exact_movie_sources(
                int(guild_id),
                media=details.media,
            )
        except web.HTTPException:
            raise
        except Exception as exc:
            raise web.HTTPServiceUnavailable(
                text="Cinema source search is temporarily unavailable."
            ) from exc
    elif media_type == "episode":
        try:
            series_id = int(payload.get("series_id") or 0)
            season_number = int(payload.get("season_number") or 0)
            episode_number = int(payload.get("episode_number") or 0)
        except Exception:
            series_id = season_number = episode_number = 0
        if series_id <= 0 or season_number < 0 or episode_number <= 0:
            raise web.HTTPBadRequest(text="Invalid TV episode identity.")
        try:
            details = await get_details("tv", series_id)
            episodes = await get_season(series_id, season_number)
            episode = next(
                (
                    item
                    for item in episodes
                    if int(item.episode_number) == episode_number
                ),
                None,
            )
            if episode is None:
                raise web.HTTPNotFound(text="That TV episode is not available in the catalog.")
            requested_tmdb_id = int(payload.get("tmdb_id") or 0)
            if requested_tmdb_id > 0 and int(episode.tmdb_id) != requested_tmdb_id:
                raise web.HTTPConflict(text="The episode identity changed. Refresh Cinema and try again.")
            metadata, query, outcome = await search_exact_episode_sources(
                int(guild_id),
                series=details.media,
                episode=episode,
            )
        except web.HTTPException:
            raise
        except Exception as exc:
            raise web.HTTPServiceUnavailable(
                text="Cinema episode source search is temporarily unavailable."
            ) from exc
    else:
        raise web.HTTPBadRequest(text="Cinema can start a movie or a specific TV episode.")

    variants = tuple(outcome.variants or ())
    if not variants:
        raise web.HTTPConflict(
            text="No playable source currently matches this exact Cinema title."
        )

    latest = manager.get(room_id)
    if (
        latest is None
        or latest.ended
        or int(latest.host_id) != int(user_id)
        or (
            str(latest.stream_token or ""),
            str(latest.current_candidate_id or ""),
            str(latest.current_variant_id or ""),
        )
        != baseline
    ):
        raise web.HTTPConflict(
            text="Cinema changed while sources were loading. Retry from the current session."
        )

    manager.join_room(room_id, user_id=int(user_id))
    materialize_search_results(
        latest,
        outcome,
        proposer_id=int(user_id),
        query=query,
        catalog_metadata=metadata,
    )
    candidate = find_catalog_candidate(latest, metadata)
    if candidate is None:
        raise web.HTTPConflict(text="Cinema could not attach that title to the current room.")

    allowed_refs = {str(item.source_ref or "") for item in variants}
    ranked = [
        item
        for item in manager.ranked_variants(room_id, candidate.candidate_id)
        if str(item.source_ref or "") in allowed_refs
    ]
    if not ranked:
        raise web.HTTPConflict(text="No playable release remains for this title.")

    selected = await select_preferred_variant(user_id, ranked)
    if selected is None:
        raise web.HTTPConflict(text="No playable release remains for this title.")

    try:
        playback = await start_room_variant(
            room_id,
            actor_id=int(user_id),
            candidate_id=candidate.candidate_id,
            variant_id=selected.variant_id,
        )
    except Exception as exc:
        raise web.HTTPBadGateway(
            text="The selected Cinema source could not be started."
        ) from exc

    return web.json_response(
        {
            "ok": True,
            "room_id": playback.room.room_id,
            "watch_url": movie_night_watch_url(playback.room.room_id, int(user_id)),
            "media": metadata,
            "source": {
                "source_id": str(selected.source_id or ""),
                "source_label": str(selected.source_label or "Cinema source"),
            },
        }
    )


async def _canonical_library_target(
    payload: Mapping[str, Any],
    *,
    adult_enabled: bool,
) -> dict[str, Any]:
    media_type = str(payload.get("media_type") or "").strip().lower()
    if media_type in {"movie", "tv"}:
        try:
            tmdb_id = int(payload.get("tmdb_id") or 0)
        except Exception:
            tmdb_id = 0
        if tmdb_id <= 0:
            raise InvalidCinemaState("Choose a canonical Cinema title.")
        try:
            details = await get_details(media_type, tmdb_id)
        except Exception as exc:
            raise web.HTTPServiceUnavailable(
                text="Cinema metadata is temporarily unavailable."
            ) from exc
        if bool(details.media.adult) and not adult_enabled:
            raise web.HTTPForbidden(
                text="Adult-content Cinema titles are disabled for this server."
            )
        return {
            "media_type": media_type,
            "tmdb_id": int(details.media.tmdb_id),
            "season_number": 0,
            "episode_number": 0,
            "title": str(details.media.title),
            "metadata": {
                "poster_url": str(details.media.poster_url or ""),
                "backdrop_url": str(details.media.backdrop_url or ""),
                "year": int(details.media.year or 0),
                "media_type": media_type,
                "adult": bool(details.media.adult),
                "genres": list(details.genres)[:12],
                "studios": list(details.studios)[:16],
                "franchises": list(details.franchises)[:8],
            },
        }

    if media_type == "episode":
        try:
            series_id = int(
                payload.get("series_id")
                or (
                    payload.get("metadata", {}).get("series_id")
                    if isinstance(payload.get("metadata"), Mapping)
                    else 0
                )
                or 0
            )
            season_number = max(0, int(payload.get("season_number") or 0))
            episode_number = max(0, int(payload.get("episode_number") or 0))
        except Exception:
            series_id = 0
            season_number = 0
            episode_number = 0
        if series_id <= 0 or episode_number <= 0:
            raise InvalidCinemaState("Choose a canonical Cinema episode.")
        try:
            details, episodes = await asyncio.gather(
                get_details("tv", series_id),
                get_season(series_id, season_number),
            )
        except Exception as exc:
            raise web.HTTPServiceUnavailable(
                text="Cinema episode metadata is temporarily unavailable."
            ) from exc
        if bool(details.media.adult) and not adult_enabled:
            raise web.HTTPForbidden(
                text="Adult-content Cinema titles are disabled for this server."
            )
        episode = next(
            (
                item
                for item in episodes
                if int(item.episode_number) == episode_number
            ),
            None,
        )
        if episode is None:
            raise InvalidCinemaState("That Cinema episode could not be resolved.")
        return {
            "media_type": "episode",
            "tmdb_id": int(episode.tmdb_id),
            "season_number": int(episode.season_number),
            "episode_number": int(episode.episode_number),
            "title": (
                f"{details.media.title} • "
                f"S{int(episode.season_number):02d}E{int(episode.episode_number):02d} "
                f"{episode.title}"
            )[:180],
            "metadata": {
                "series_id": int(series_id),
                "series_title": str(details.media.title)[:180],
                "episode_title": str(episode.title)[:180],
                "poster_url": str(details.media.poster_url or ""),
                "series_poster_url": str(details.media.poster_url or ""),
                "backdrop_url": str(
                    episode.still_url
                    or details.media.backdrop_url
                    or ""
                ),
                "still_url": str(episode.still_url or ""),
                "year": int(details.media.year or 0),
                "media_type": "episode",
                "adult": bool(details.media.adult),
                "genres": list(details.genres)[:12],
                "studios": list(details.studios)[:16],
                "franchises": list(details.franchises)[:8],
            },
        }

    raise InvalidCinemaState("Unsupported Cinema Library media type.")


async def cinema_library_api(request: web.Request) -> web.Response:
    guild_id, user_id = await _site_identity(request)
    adult_enabled = await _guild_adult_content_enabled(guild_id)

    if request.method == "GET":
        try:
            snapshot = await library_intelligence_snapshot(
                guild_id,
                user_id,
                include_adult=adult_enabled,
            )
        except CinemaStorageUnavailable as exc:
            raise web.HTTPServiceUnavailable(
                text="Dank Cinema library storage is unavailable."
            ) from exc
        return web.json_response(snapshot)

    try:
        payload = await request.json()
    except Exception:
        payload = {}
    if not isinstance(payload, Mapping):
        payload = {}
    action = str(payload.get("action") or "").strip().lower()

    try:
        if action == "save_list":
            try:
                list_position = max(0, int(payload.get("position") or 0))
            except Exception as exc:
                raise InvalidCinemaState("Cinema list position is invalid.") from exc
            row = await save_custom_list(
                user_id,
                name=str(payload.get("name") or ""),
                description=str(payload.get("description") or ""),
                list_id=str(payload.get("list_id") or ""),
                position=list_position,
            )
            return web.json_response({"ok": True, "list": row})

        if action == "delete_list":
            await delete_custom_list(
                user_id,
                str(payload.get("list_id") or ""),
            )
            return web.json_response({"ok": True, "deleted": True})

        target = await _canonical_library_target(
            payload,
            adult_enabled=adult_enabled,
        )

        if action == "watchlist":
            if target["media_type"] not in {"movie", "tv"}:
                raise InvalidCinemaState(
                    "Add the TV series, not an episode, to Watchlist."
                )
            row = await set_watchlist(
                user_id,
                media_type=str(target["media_type"]),
                tmdb_id=int(target["tmdb_id"]),
                title=str(target["title"]),
                metadata=target["metadata"],
                enabled=bool(payload.get("enabled", True)),
            )
        elif action == "favorite":
            row = await set_favorite(
                user_id,
                media_type=str(target["media_type"]),
                tmdb_id=int(target["tmdb_id"]),
                season_number=int(target["season_number"]),
                episode_number=int(target["episode_number"]),
                title=str(target["title"]),
                metadata=target["metadata"],
                enabled=bool(payload.get("enabled", True)),
            )
        elif action == "rating":
            raw_rating = payload.get("rating")
            rating = None
            if raw_rating not in (None, "", 0, "0"):
                try:
                    rating = int(raw_rating)
                except Exception as exc:
                    raise InvalidCinemaState(
                        "Cinema ratings must be between 1 and 10."
                    ) from exc
            row = await set_rating(
                user_id,
                media_type=str(target["media_type"]),
                tmdb_id=int(target["tmdb_id"]),
                season_number=int(target["season_number"]),
                episode_number=int(target["episode_number"]),
                title=str(target["title"]),
                metadata=target["metadata"],
                rating=rating,
            )
        elif action == "watched":
            if target["media_type"] == "tv":
                raise InvalidCinemaState(
                    "Mark specific TV episodes watched instead of the whole series."
                )
            row = await mark_watched(
                user_id,
                media_type=str(target["media_type"]),
                tmdb_id=int(target["tmdb_id"]),
                season_number=int(target["season_number"]),
                episode_number=int(target["episode_number"]),
                title=str(target["title"]),
                metadata=target["metadata"],
                watched=bool(payload.get("watched", True)),
            )
        elif action == "list_item":
            try:
                item_position = max(0, int(payload.get("position") or 0))
            except Exception as exc:
                raise InvalidCinemaState("Cinema list item position is invalid.") from exc
            row = await set_custom_list_item(
                user_id,
                list_id=str(payload.get("list_id") or ""),
                media_type=str(target["media_type"]),
                tmdb_id=int(target["tmdb_id"]),
                season_number=int(target["season_number"]),
                episode_number=int(target["episode_number"]),
                title=str(target["title"]),
                metadata=target["metadata"],
                enabled=bool(payload.get("enabled", True)),
                position=item_position,
            )
        else:
            raise InvalidCinemaState("Unsupported Cinema library action.")
    except InvalidCinemaState as exc:
        raise web.HTTPBadRequest(text=str(exc)) from exc
    except CinemaStorageUnavailable as exc:
        raise web.HTTPServiceUnavailable(
            text="Dank Cinema library storage is unavailable."
        ) from exc

    return web.json_response({"ok": True, "item": row})

async def cinema_profile_api(request: web.Request) -> web.Response:
    guild_id, user_id = await _site_identity(request)
    if request.method == "GET":
        row = await get_cinema_user(user_id)
        return web.json_response(
            {
                "discord": _safe_discord_context(guild_id, user_id),
                "preferences": row.get("preferences") or {},
            }
        )
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    if not isinstance(payload, Mapping):
        payload = {}
    row = await update_cinema_preferences(user_id, payload)
    return web.json_response(
        {
            "ok": True,
            "preferences": row.get("preferences") or {},
        }
    )


async def cinema_feeds_api(request: web.Request) -> web.Response:
    guild_id, user_id = await _site_identity(request)
    can_manage = _can_manage_cinema(guild_id, user_id)

    if request.method == "GET":
        query = " ".join(str(request.query.get("q") or "").split())[:120]
        try:
            page = max(1, int(request.query.get("page") or 1))
        except Exception:
            page = 1
        try:
            page_size = max(1, min(int(request.query.get("page_size") or 8), 24))
        except Exception:
            page_size = 8
        return web.json_response(
            await cinema_feed_state(
                guild_id,
                can_manage=can_manage,
                user_id=user_id,
                refresh=False,
                query=query,
                page=page,
                page_size=page_size,
            )
        )

    if not can_manage:
        raise web.HTTPForbidden(
            text="Manage Server permission is required to change Cinema sources."
        )
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    if not isinstance(payload, Mapping):
        payload = {}
    try:
        await mutate_cinema_feed(
            guild_id,
            actor_id=user_id,
            action=str(payload.get("action") or ""),
            payload=payload,
        )
    except LookupError as exc:
        raise web.HTTPNotFound(text=str(exc))
    except CinemaFeedConflict as exc:
        raise web.HTTPConflict(text=str(exc))
    except ValueError as exc:
        raise web.HTTPBadRequest(text=str(exc))

    return web.json_response(
        await cinema_feed_state(
            guild_id,
            can_manage=True,
            user_id=user_id,
            refresh=False,
            query="",
            page=1,
            page_size=8,
        )
    )


async def cinema_feed_queue_api(request: web.Request) -> web.Response:
    """Add one Feed Center suggestion to the current host-owned Cinema queue."""

    guild_id, user_id = await _site_identity(request)
    manager = get_movie_night_manager()
    rooms = [
        room
        for room in manager.active_rooms_for_guild(guild_id)
        if not room.ended and int(room.host_id) == int(user_id)
    ]
    if not rooms:
        raise web.HTTPConflict(
            text="Start or host a Cinema session before adding a feed match to Up Next."
        )
    room = max(rooms, key=lambda item: float(item.created_at))

    try:
        payload = await request.json()
    except Exception:
        payload = {}
    if not isinstance(payload, Mapping):
        payload = {}

    media_type = str(payload.get("media_type") or "").strip().lower()
    try:
        tmdb_id = int(payload.get("tmdb_id") or 0)
        season_number = int(payload.get("season_number") or 0)
        episode_number = int(payload.get("episode_number") or 0)
    except (TypeError, ValueError) as exc:
        raise web.HTTPBadRequest(text="Invalid feed queue identity.") from exc

    if tmdb_id <= 0 or media_type not in {"movie", "tv"}:
        raise web.HTTPBadRequest(
            text="This feed result needs a canonical Cinema identity before it can be queued."
        )

    try:
        _raw, cinema_preferences = await load_movie_night_preferences(
            int(guild_id),
            refresh=False,
        )
        adult_enabled = bool(cinema_preferences.adult_content_enabled)
    except Exception:
        adult_enabled = False

    if media_type == "movie":
        details = await get_details("movie", tmdb_id)
        if bool(details.media.adult) and not adult_enabled:
            raise web.HTTPForbidden(
                text="Adult-content Cinema playback is disabled for this server."
            )
        metadata, query, outcome = await search_exact_movie_sources(
            int(guild_id),
            media=details.media,
        )
    else:
        if episode_number <= 0:
            raise web.HTTPBadRequest(
                text="TV feed queue suggestions need an exact season and episode."
            )
        details = await get_details("tv", tmdb_id)
        if bool(details.media.adult) and not adult_enabled:
            raise web.HTTPForbidden(
                text="Adult-content Cinema playback is disabled for this server."
            )
        episodes = await get_season(tmdb_id, season_number)
        episode = next(
            (
                item
                for item in episodes
                if int(item.episode_number) == int(episode_number)
            ),
            None,
        )
        if episode is None:
            raise web.HTTPNotFound(text="That feed episode is no longer in the Cinema catalog.")
        metadata, query, outcome = await search_exact_episode_sources(
            int(guild_id),
            series=details.media,
            episode=episode,
        )

    if not tuple(outcome.variants or ()):
        raise web.HTTPConflict(
            text="No playable source currently matches this feed suggestion."
        )

    latest = manager.get(room.room_id)
    if (
        latest is None
        or latest.ended
        or int(latest.host_id) != int(user_id)
    ):
        raise web.HTTPConflict(
            text="Your Cinema session changed while that feed match was being prepared."
        )

    manager.join_room(latest.room_id, user_id=int(user_id))
    materialize_search_results(
        latest,
        outcome,
        proposer_id=int(user_id),
        query=query,
        catalog_metadata=metadata,
    )
    candidate = find_catalog_candidate(latest, metadata)
    if candidate is None:
        raise web.HTTPConflict(
            text="Cinema could not attach that feed match to the queue."
        )
    if str(candidate.candidate_id) == str(latest.current_candidate_id or ""):
        raise web.HTTPConflict(text="That title is already playing.")

    manager.queue_winner(
        latest.room_id,
        candidate_id=candidate.candidate_id,
    )
    return web.json_response(
        {
            "ok": True,
            "room_id": str(latest.room_id),
            "title": str(candidate.title or ""),
            "queue_count": len(latest.queue),
        }
    )


async def cinema_feed_rules_api(request: web.Request) -> web.Response:
    guild_id, user_id = await _site_identity(request)
    can_manage = _can_manage_cinema(guild_id, user_id)

    if request.method == "GET":
        try:
            rules = await list_feed_rules(
                guild_id,
                user_id,
                include_disabled=True,
            )
        except CinemaStorageUnavailable as exc:
            raise web.HTTPServiceUnavailable(
                text="Cinema Feed Rules storage is unavailable."
            ) from exc
        return web.json_response(
            {
                "rules": rules,
                "can_manage": bool(can_manage),
            }
        )

    try:
        payload = await request.json()
    except Exception:
        payload = {}
    if not isinstance(payload, Mapping):
        payload = {}

    action = str(payload.get("action") or "save").strip().lower()
    try:
        if action == "save":
            saved = await save_feed_rule(
                guild_id,
                user_id,
                payload,
                can_manage=can_manage,
            )
            result = {"saved": saved}
        elif action == "delete":
            await delete_feed_rule(
                guild_id,
                user_id,
                str(payload.get("id") or ""),
                can_manage=can_manage,
            )
            result = {"deleted": True}
        elif action == "refresh_private":
            result = await refresh_private_source(
                guild_id,
                user_id,
                str(payload.get("id") or ""),
            )
        else:
            raise CinemaFeedRuleError("Unsupported Feed Rule action.")
    except PermissionError as exc:
        raise web.HTTPForbidden(text=str(exc))
    except LookupError as exc:
        raise web.HTTPNotFound(text=str(exc))
    except CinemaFeedRuleError as exc:
        raise web.HTTPBadRequest(text=str(exc))
    except CinemaStorageUnavailable as exc:
        raise web.HTTPServiceUnavailable(
            text="Cinema Feed Rules storage is unavailable."
        ) from exc

    state = await cinema_feed_state(
        guild_id,
        can_manage=can_manage,
        user_id=user_id,
        refresh=False,
        query="",
        page=1,
        page_size=8,
    )
    return web.json_response(
        {
            "ok": True,
            **result,
            "feed_state": state,
        }
    )


async def cinema_notifications_api(request: web.Request) -> web.Response:
    _guild_id, user_id = await _site_identity(request)
    if request.method == "GET":
        rows = await list_notifications(
            user_id,
            guild_id=_guild_id,
            limit=50,
        )
        manager = get_movie_night_manager()
        output: list[dict[str, Any]] = []
        for raw in rows:
            row = dict(raw)
            action = (
                dict(row.get("action") or {})
                if isinstance(row.get("action"), Mapping)
                else {}
            )
            if str(action.get("kind") or "") == "room":
                room_id = str(action.get("room_id") or "").strip()
                room = manager.get(room_id) if room_id else None
                if (
                    room is not None
                    and not room.ended
                    and int(room.guild_id) == int(_guild_id)
                    and manager.user_can_access(room, int(user_id))
                ):
                    action["watch_url"] = movie_night_watch_url(
                        room.room_id,
                        int(user_id),
                    )
                    action["available"] = True
                else:
                    action["available"] = False
            row["action"] = action
            output.append(row)
        return web.json_response({"notifications": output})
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    if not isinstance(payload, Mapping):
        payload = {}
    notification_id = str(payload.get("notification_id") or "").strip()
    if not notification_id:
        raise web.HTTPBadRequest(text="Choose a notification.")
    await mark_notification_read(user_id, notification_id)
    return web.json_response({"ok": True})


def _site_html(guild_id: int, user_id: int) -> str:
    boot = json.dumps(
        {
            "guildId": str(int(guild_id)),
            "userId": str(int(user_id)),
        },
        separators=(",", ":"),
    ).replace("</", "<\/")
    return f"""<!doctype html>
<html lang="en" data-quality="standard">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
  <meta name="theme-color" content="#030806">
  <title>Dank Cinema</title>
  <link rel="stylesheet" href="/cinema/assets/site.css?v=8">
</head>
<body>
  <div id="app" class="app-shell" aria-live="polite"></div>
  <script>window.__DANK_CINEMA_BOOT__={boot};</script>
  <script src="/cinema/assets/site.js?v=12" defer></script>
</body>
</html>"""


async def cinema_site_page(request: web.Request) -> web.Response:
    try:
        guild_id, user_id = await _site_identity(request)
    except web.HTTPUnauthorized:
        try:
            guild_id = int(request.match_info.get("guild_id") or 0)
        except Exception:
            guild_id = 0
        if guild_id > 0 and _discord_oauth_ready():
            raise web.HTTPFound(f"/cinema/login?guild_id={guild_id}")
        raise
    response = web.Response(
        text=_site_html(guild_id, user_id),
        content_type="text/html",
        headers={
            "Cache-Control": "no-store",
            "Content-Security-Policy": (
                "default-src 'self'; "
                "script-src 'self' 'unsafe-inline'; "
                "style-src 'self'; "
                "img-src 'self' data: https://image.tmdb.org https://cdn.discordapp.com https://media.discordapp.net; "
                "connect-src 'self'; "
                "frame-src https://www.youtube-nocookie.com; "
                "object-src 'none'; base-uri 'none'; frame-ancestors 'none'"
            ),
            "Referrer-Policy": "no-referrer",
            "X-Content-Type-Options": "nosniff",
        },
    )
    session = cinema_session_value(guild_id, user_id)
    if session:
        response.set_cookie(
            CINEMA_SESSION_COOKIE,
            session,
            max_age=CINEMA_SESSION_TTL_SECONDS,
            httponly=True,
            secure=True,
            samesite="Lax",
            path=f"/cinema/{int(guild_id)}",
        )

    identity = cinema_identity_value(user_id)
    if identity:
        response.set_cookie(
            CINEMA_IDENTITY_COOKIE,
            identity,
            max_age=CINEMA_IDENTITY_TTL_SECONDS,
            httponly=True,
            secure=True,
            samesite="Lax",
            path="/cinema",
        )

    remembered = set(
        validate_cinema_guilds(
            user_id,
            str(request.cookies.get(CINEMA_GUILDS_COOKIE, "") or ""),
        )
    )
    remembered.add(int(guild_id))
    guilds_value = cinema_guilds_value(user_id, sorted(remembered))
    if guilds_value:
        response.set_cookie(
            CINEMA_GUILDS_COOKIE,
            guilds_value,
            max_age=CINEMA_GUILDS_TTL_SECONDS,
            httponly=True,
            secure=True,
            samesite="Lax",
            path="/cinema",
        )
    return response


async def cinema_site_asset(request: web.Request) -> web.Response:
    name = str(request.match_info.get("name") or "")
    path: Optional[Path]
    content_type: str
    if name == "site.css":
        path = _CSS_PATH
        content_type = "text/css"
    elif name == "site.js":
        path = _JS_PATH
        content_type = "application/javascript"
    else:
        raise web.HTTPNotFound(text="Cinema asset not found.")
    try:
        payload = path.read_bytes()
    except OSError:
        raise web.HTTPNotFound(text="Cinema asset unavailable.")
    return web.Response(
        body=payload,
        content_type=content_type,
        headers={
            "Cache-Control": "public, max-age=300",
            "X-Content-Type-Options": "nosniff",
        },
    )


def register_cinema_site_routes(app: web.Application) -> None:
    app.router.add_get("/cinema", cinema_entry_page)
    app.router.add_get("/cinema/login", cinema_oauth_login)
    app.router.add_get("/cinema/auth/callback", cinema_oauth_callback)
    app.router.add_get("/cinema/open/{guild_id}", cinema_open_guild)
    app.router.add_get("/cinema/assets/{name}", cinema_site_asset)
    app.router.add_get("/cinema/{guild_id}", cinema_site_page)
    app.router.add_get(
        "/cinema/{guild_id}/api/auth-debug",
        cinema_auth_debug_api,
    )
    app.router.add_get("/cinema/{guild_id}/api/home", cinema_home_api)
    app.router.add_get("/cinema/{guild_id}/api/search", cinema_search_api)
    app.router.add_get(
        "/cinema/{guild_id}/api/details/{media_type}/{tmdb_id}",
        cinema_details_api,
    )
    app.router.add_get(
        "/cinema/{guild_id}/api/season/{series_id}/{season_number}",
        cinema_season_api,
    )
    app.router.add_post("/cinema/{guild_id}/api/play", cinema_play_api)
    app.router.add_get("/cinema/{guild_id}/api/library", cinema_library_api)
    app.router.add_post("/cinema/{guild_id}/api/library", cinema_library_api)
    app.router.add_get("/cinema/{guild_id}/api/profile", cinema_profile_api)
    app.router.add_post("/cinema/{guild_id}/api/profile", cinema_profile_api)
    app.router.add_get("/cinema/{guild_id}/api/feeds", cinema_feeds_api)
    app.router.add_post("/cinema/{guild_id}/api/feeds", cinema_feeds_api)
    app.router.add_post(
        "/cinema/{guild_id}/api/feed-queue",
        cinema_feed_queue_api,
    )
    app.router.add_get(
        "/cinema/{guild_id}/api/feed-rules",
        cinema_feed_rules_api,
    )
    app.router.add_post(
        "/cinema/{guild_id}/api/feed-rules",
        cinema_feed_rules_api,
    )
    app.router.add_get(
        "/cinema/{guild_id}/api/notifications",
        cinema_notifications_api,
    )
    app.router.add_post(
        "/cinema/{guild_id}/api/notifications",
        cinema_notifications_api,
    )


__all__ = [
    "cinema_feed_queue_api",
    "cinema_feed_rules_api",
    "cinema_feeds_api",
    "cinema_home_api",
    "cinema_library_api",
    "cinema_notifications_api",
    "cinema_play_api",
    "cinema_profile_api",
    "cinema_search_api",
    "cinema_season_api",
    "cinema_site_page",
    "cinema_site_url",
    "register_cinema_site_routes",
]
