from __future__ import annotations

"""Signed synchronized web player for Dank Shield Movie Night."""

import base64
import hashlib
from io import BytesIO
import hmac
import json
import os
import time
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlencode

from aiohttp import web
from PIL import Image, ImageDraw, ImageFilter

from stoney_verify.cinema_feed_service import (
    CinemaFeedConflict,
    feed_state as cinema_feed_state,
    mutate_feed as mutate_cinema_feed,
    runtime_state as cinema_feed_runtime_state,
)
from stoney_verify.cinema_library_service import (
    CinemaStorageUnavailable,
    get_cinema_user,
    record_progress,
    update_cinema_preferences,
)
from stoney_verify.movie_night import MovieNightRoom, get_movie_night_manager
from stoney_verify.movie_night_session import (
    ensure_movie_night_cleanup_task,
    terminate_movie_night_room,
)
from stoney_verify.torrent_streaming import (
    TorrentSessionUnavailableError,
    get_torrent_manager,
    media_content_type,
)


def _secret() -> str:
    return str(os.getenv("DANK_TORRENT_STREAM_SECRET", "") or "").strip()


def _public_base() -> str:
    return str(os.getenv("DANK_MEDIA_PUBLIC_BASE_URL", "") or "").strip().rstrip("/")


_BRAND_ASSET_PATH = (
    Path(__file__).with_name("assets") / "dank_cinema_brand_500.webp.b64"
)
_BRAND_ASSET_VERSION = "art-system-v3"

_FEED_RUNTIME_STATE = cinema_feed_runtime_state()



@lru_cache(maxsize=1)
def _dank_cinema_brand_source_bytes() -> bytes:
    encoded = _BRAND_ASSET_PATH.read_text(encoding="ascii").strip()
    return base64.b64decode(encoded, validate=True)


@lru_cache(maxsize=1)
def _dank_cinema_brand_rgba() -> Image.Image:
    """Build one transparent master from the approved repository artwork.

    The historical export has the correct art but includes a connected black
    rectangle. Remove only that edge-connected matte, preserve enclosed dark
    reel details, and keep the transparent master cached. The website then
    composes responsive emblem/wordmark variants instead of pasting a banner.
    """

    try:
        source = Image.open(BytesIO(_dank_cinema_brand_source_bytes())).convert("RGBA")
    except Exception as exc:
        raise ValueError("Dank Cinema source brand asset is unreadable.") from exc

    work = source.copy()
    marker = (255, 0, 255, 255)
    for point in (
        (0, 0),
        (max(0, work.width - 1), 0),
        (0, max(0, work.height - 1)),
        (max(0, work.width - 1), max(0, work.height - 1)),
    ):
        ImageDraw.floodfill(work, point, marker, thresh=34)

    alpha = Image.new("L", work.size, 255)
    alpha_pixels = alpha.load()
    pixels = work.load()
    for y in range(work.height):
        for x in range(work.width):
            r, g, b, _a = pixels[x, y]
            if (r, g, b) == marker[:3]:
                alpha_pixels[x, y] = 0
    alpha = alpha.filter(ImageFilter.GaussianBlur(radius=0.42))
    source.putalpha(alpha)
    return source


def _trim_transparent(image: Image.Image, *, padding: int = 2) -> Image.Image:
    alpha = image.getchannel("A")
    bbox = alpha.getbbox()
    if not bbox:
        return image
    left, top, right, bottom = bbox
    left = max(0, left - padding)
    top = max(0, top - padding)
    right = min(image.width, right + padding)
    bottom = min(image.height, bottom + padding)
    return image.crop((left, top, right, bottom))


@lru_cache(maxsize=8)
def _dank_cinema_brand_variant(kind: str) -> bytes:
    source = _dank_cinema_brand_rgba()
    normalized = str(kind or "full").strip().lower()

    if normalized == "mark":
        # Crowned reel + smoke emblem.
        crop = source.crop((0, 0, max(1, round(source.width * 0.33)), source.height))
        target_width = 320
    elif normalized == "wordmark":
        # DANK CINEMA plus "A feature of The 420 Lobby" lockup.
        crop = source.crop((max(0, round(source.width * 0.255)), 0, source.width, source.height))
        target_width = 1040
    elif normalized == "mono":
        crop = source.copy()
        target_width = 1040
        alpha = crop.getchannel("A")
        monochrome = Image.new("RGBA", crop.size, (246, 248, 247, 0))
        monochrome.putalpha(alpha)
        crop = monochrome
    else:
        crop = source.copy()
        target_width = 1200

    crop = _trim_transparent(crop, padding=2)
    if crop.width > 0 and crop.width != target_width:
        target_height = max(1, round(crop.height * target_width / crop.width))
        crop = crop.resize((target_width, target_height), Image.Resampling.LANCZOS)
        crop = crop.filter(ImageFilter.UnsharpMask(radius=0.55, percent=108, threshold=3))

    output = BytesIO()
    crop.save(output, format="WEBP", quality=91, method=6, lossless=False)
    return output.getvalue()


async def dank_cinema_brand_asset(request: web.Request) -> web.Response:
    variant = str(request.match_info.get("variant") or "full").strip().lower()
    if variant not in {"full", "mark", "wordmark", "mono"}:
        raise web.HTTPNotFound(text="Dank Cinema brand variant not found.")
    try:
        payload = _dank_cinema_brand_variant(variant)
    except (OSError, ValueError):
        raise web.HTTPNotFound(text="Dank Cinema brand asset unavailable.")
    return web.Response(
        body=payload,
        content_type="image/webp",
        headers={
            "Cache-Control": "public, max-age=31536000, immutable",
            "X-Content-Type-Options": "nosniff",
        },
    )

def _signature(room_id: str, user_id: int, expires: int) -> str:
    key = _secret().encode("utf-8")
    payload = f"movie-night:{room_id}:{int(user_id)}:{int(expires)}".encode("utf-8")
    return hmac.new(key, payload, hashlib.sha256).hexdigest()


def movie_night_watch_url(
    room_id: str,
    user_id: int,
    *,
    ttl_seconds: int = 6 * 60 * 60,
) -> str:
    base = _public_base()
    secret = _secret()
    if not base or not secret or not room_id or int(user_id) <= 0:
        return ""
    expires = int(time.time()) + max(300, min(int(ttl_seconds), 21600))
    query = urlencode(
        {
            "uid": int(user_id),
            "exp": expires,
            "sig": _signature(str(room_id), int(user_id), expires),
        }
    )
    return f"{base}/movie/{room_id}/watch?{query}"


def _validate_access(
    room_id: str,
    user_id: str,
    expires: str,
    signature: str,
) -> Optional[int]:
    secret = _secret()
    if not secret:
        return None
    try:
        uid = int(str(user_id or "").strip())
        exp = int(str(expires or "").strip())
    except Exception:
        return None
    now = int(time.time())
    if uid <= 0 or exp < now or exp > now + 21660:
        return None
    expected = _signature(str(room_id), uid, exp)
    if not signature or not hmac.compare_digest(expected, str(signature)):
        return None
    return uid


def _request_identity(request: web.Request) -> tuple[str, Optional[int]]:
    room_id = str(request.match_info.get("room_id", "") or "")
    uid = _validate_access(
        room_id,
        str(request.query.get("uid", "") or ""),
        str(request.query.get("exp", "") or ""),
        str(request.query.get("sig", "") or ""),
    )
    return room_id, uid


async def _room_and_user(
    request: web.Request,
) -> tuple[MovieNightRoom, int]:
    room_id, uid = _request_identity(request)
    if uid is None:
        raise web.HTTPUnauthorized(text="Invalid or expired Dank Cinema link.")
    manager = get_movie_night_manager()
    room = manager.get(room_id)
    if room is None:
        raise web.HTTPNotFound(text="Dank Cinema session not found.")
    if not manager.user_can_access(room, uid):
        raise web.HTTPForbidden(text="This is a private Dank Cinema viewing session.")
    return room, uid


def _open_vote(room: MovieNightRoom) -> Optional[dict[str, Any]]:
    unresolved = [vote for vote in room.votes.values() if not vote.resolved]
    if not unresolved:
        return None
    vote = max(unresolved, key=lambda item: float(item.created_at))
    manager = get_movie_night_manager()
    return {
        "vote_id": vote.vote_id,
        "action": vote.action,
        "yes": len(vote.yes & manager.active_viewers(room)),
        "no": len(vote.no & manager.active_viewers(room)),
        "required_yes": manager.required_yes_votes(room),
        "expires_at_monotonic": vote.expires_at,
    }


def _swarm_display(torrent_status: dict[str, Any], variant: Any) -> dict[str, Any]:
    live_seeds = max(0, int(torrent_status.get("seeds", 0) or 0))
    live_peers = max(0, int(torrent_status.get("peers", 0) or 0))
    live_leechers = max(
        0,
        int(
            torrent_status.get(
                "leechers",
                max(0, live_peers - live_seeds),
            )
            or 0
        ),
    )
    if live_seeds or live_peers or live_leechers:
        return {
            "seeds": live_seeds,
            "leechers": live_leechers,
            "peers": max(live_peers, live_seeds + live_leechers),
            "source": "live",
        }

    if variant is not None:
        try:
            health = dict(variant.swarm_health)
        except Exception:
            health = {}
        reported_seeds = max(0, int(health.get("seeds", 0) or 0))
        reported_leechers = max(0, int(health.get("leechers", 0) or 0))
        reported_peers = max(
            reported_seeds + reported_leechers,
            int(health.get("peers", 0) or 0),
        )
        if reported_seeds or reported_peers or reported_leechers:
            return {
                "seeds": reported_seeds,
                "leechers": reported_leechers,
                "peers": reported_peers,
                "source": "provider",
            }

    return {"seeds": 0, "leechers": 0, "peers": 0, "source": ""}


def _safe_movie_art_url(value: Any) -> str:
    cleaned = str(value or "").strip()
    if cleaned.startswith("https://image.tmdb.org/"):
        return cleaned
    return ""


def _safe_discord_avatar_url(value: Any) -> str:
    cleaned = str(value or "").strip()
    if (
        cleaned.startswith("https://cdn.discordapp.com/")
        or cleaned.startswith("https://media.discordapp.net/")
    ):
        return cleaned
    return ""


def _discord_viewer_summaries(
    room: MovieNightRoom,
    viewer_ids: set[int],
) -> list[dict[str, Any]]:
    try:
        from stoney_verify.globals import bot
    except Exception:
        bot = None

    guild = None
    if bot is not None:
        try:
            guild = bot.get_guild(int(room.guild_id))
        except Exception:
            guild = None

    ordered = sorted(
        (int(uid) for uid in viewer_ids),
        key=lambda uid: (uid != int(room.host_id), uid),
    )
    summaries: list[dict[str, Any]] = []
    for uid in ordered:
        entity = None
        if guild is not None:
            try:
                entity = guild.get_member(uid)
            except Exception:
                entity = None
        if entity is None and bot is not None:
            try:
                entity = bot.get_user(uid)
            except Exception:
                entity = None

        display_name = ""
        avatar_url = ""
        if entity is not None:
            display_name = str(
                getattr(entity, "display_name", "")
                or getattr(entity, "global_name", "")
                or getattr(entity, "name", "")
                or ""
            ).strip()[:80]
            try:
                avatar_url = _safe_discord_avatar_url(
                    getattr(getattr(entity, "display_avatar", None), "url", "")
                )
            except Exception:
                avatar_url = ""

        summaries.append(
            {
                "user_id": uid,
                "display_name": display_name or str(uid),
                "avatar_url": avatar_url,
                "is_host": uid == int(room.host_id),
            }
        )
    return summaries


def _discord_invite_options(
    room: MovieNightRoom,
    query: str = "",
    *,
    limit: int = 20,
) -> list[dict[str, Any]]:
    try:
        from stoney_verify.globals import bot
    except Exception:
        bot = None
    if bot is None:
        return []

    try:
        guild = bot.get_guild(int(room.guild_id))
    except Exception:
        guild = None
    if guild is None:
        return []

    needle = " ".join(str(query or "").casefold().split())[:80]
    rows: list[dict[str, Any]] = []
    for member in tuple(getattr(guild, "members", ()) or ()):
        uid = int(getattr(member, "id", 0) or 0)
        if uid <= 0 or uid == int(room.host_id) or bool(getattr(member, "bot", False)):
            continue
        display_name = str(
            getattr(member, "display_name", "")
            or getattr(member, "global_name", "")
            or getattr(member, "name", "")
            or uid
        ).strip()[:80]
        username = str(getattr(member, "name", "") or "").strip()[:80]
        searchable = f"{display_name} {username} {uid}".casefold()
        if needle and needle not in searchable:
            continue
        avatar_url = ""
        try:
            avatar_url = _safe_discord_avatar_url(
                getattr(getattr(member, "display_avatar", None), "url", "")
            )
        except Exception:
            avatar_url = ""
        rows.append(
            {
                "user_id": uid,
                "display_name": display_name or str(uid),
                "username": username,
                "avatar_url": avatar_url,
            }
        )
        if len(rows) >= max(1, min(int(limit), 20)):
            break
    return rows


def _discord_invite_target(
    room: MovieNightRoom,
    user_id: int,
) -> Optional[dict[str, Any]]:
    try:
        from stoney_verify.globals import bot
    except Exception:
        bot = None
    if bot is None:
        return None
    try:
        guild = bot.get_guild(int(room.guild_id))
    except Exception:
        guild = None
    if guild is None:
        return None
    try:
        member = guild.get_member(int(user_id))
    except Exception:
        member = None
    if member is None or bool(getattr(member, "bot", False)):
        return None
    uid = int(getattr(member, "id", 0) or 0)
    if uid <= 0 or uid == int(room.host_id):
        return None
    display_name = str(
        getattr(member, "display_name", "")
        or getattr(member, "global_name", "")
        or getattr(member, "name", "")
        or uid
    ).strip()[:80]
    username = str(getattr(member, "name", "") or "").strip()[:80]
    avatar_url = ""
    try:
        avatar_url = _safe_discord_avatar_url(
            getattr(getattr(member, "display_avatar", None), "url", "")
        )
    except Exception:
        avatar_url = ""
    return {
        "user_id": uid,
        "display_name": display_name or str(uid),
        "username": username,
        "avatar_url": avatar_url,
    }


async def _announce_watch_party_promotion(
    room: MovieNightRoom,
    *,
    invitee_id: int,
) -> bool:
    try:
        from stoney_verify.globals import bot
    except Exception:
        bot = None
    if bot is None:
        return False
    try:
        guild = bot.get_guild(int(room.guild_id))
    except Exception:
        guild = None
    if guild is None:
        return False
    try:
        channel = guild.get_channel(int(room.channel_id))
    except Exception:
        channel = None
    if channel is None or not hasattr(channel, "send"):
        return False
    try:
        await channel.send(
            (
                f"🍿 <@{int(invitee_id)}> was invited. "
                "This Dank Cinema room is now a **Watch Party** without restarting playback."
            ),
            allowed_mentions=__import__("discord").AllowedMentions(users=True, roles=False, everyone=False),
        )
        return True
    except Exception:
        return False


async def _dm_watch_party_invite(
    room: MovieNightRoom,
    *,
    user_id: int,
    watch_url: str,
) -> bool:
    if not watch_url:
        return False
    try:
        from stoney_verify.globals import bot
    except Exception:
        bot = None
    if bot is None:
        return False

    target = None
    try:
        guild = bot.get_guild(int(room.guild_id))
    except Exception:
        guild = None
    if guild is not None:
        try:
            target = guild.get_member(int(user_id))
        except Exception:
            target = None
    if target is None:
        try:
            target = bot.get_user(int(user_id))
        except Exception:
            target = None
    if target is None or bool(getattr(target, "bot", False)):
        return False

    try:
        await target.send(
            "🍿 **Dank Cinema Watch Party invite**\n"
            "You were invited to join a live Dank Cinema session.\n"
            f"{watch_url}"
        )
        return True
    except Exception:
        return False


def _discord_room_context(
    room: MovieNightRoom,
    user_id: int,
) -> dict[str, Any]:
    """Return only cached Discord context already owned by the bot.

    The signed Watch URL remains the authorization boundary. This helper makes
    that Discord relationship visible on the website without adding OAuth or a
    second identity system.
    """

    try:
        from stoney_verify.globals import bot
    except Exception:
        bot = None

    guild = None
    channel = None
    user = None
    if bot is not None:
        try:
            guild = bot.get_guild(int(room.guild_id))
        except Exception:
            guild = None
        if guild is not None:
            try:
                channel = guild.get_channel(int(room.channel_id))
            except Exception:
                channel = None
            try:
                user = guild.get_member(int(user_id))
            except Exception:
                user = None
        if user is None:
            try:
                user = bot.get_user(int(user_id))
            except Exception:
                user = None

    user_name = ""
    avatar_url = ""
    if user is not None:
        user_name = str(
            getattr(user, "display_name", "")
            or getattr(user, "global_name", "")
            or getattr(user, "name", "")
            or ""
        ).strip()[:80]
        try:
            avatar_url = _safe_discord_avatar_url(
                getattr(getattr(user, "display_avatar", None), "url", "")
            )
        except Exception:
            avatar_url = ""

    guild_name = str(getattr(guild, "name", "") or "").strip()[:100]
    channel_name = str(getattr(channel, "name", "") or "").strip()[:100]
    return {
        "connected": bool(guild is not None),
        "guild_name": guild_name,
        "channel_name": channel_name,
        "user_id": int(user_id),
        "user_name": user_name or str(int(user_id)),
        "avatar_url": avatar_url,
    }


def _candidate_web_metadata(candidate: Any) -> dict[str, Any]:
    metadata = dict(getattr(candidate, "metadata", {}) or {}) if candidate is not None else {}
    catalog = (
        dict(metadata.get("catalog") or {})
        if isinstance(metadata.get("catalog"), dict)
        else {}
    )

    # Canonical Movie Night candidates intentionally wrap TMDB identity under
    # metadata["catalog"] so provider/search metadata can coexist without
    # collisions. The Watch page must read that canonical catalog envelope
    # instead of assuming TMDB fields were flattened onto the candidate.
    source = catalog or metadata

    year = 0
    try:
        year = max(0, int(source.get("year") or 0))
    except Exception:
        year = 0
    media_type = str(source.get("media_type") or "").strip().lower()
    if media_type not in {"movie", "tv", "episode"}:
        media_type = ""
    try:
        tmdb_id = max(
            0,
            int(source.get("tmdb_id") or source.get("catalog_id") or 0),
        )
    except Exception:
        tmdb_id = 0
    try:
        series_id = max(0, int(source.get("series_id") or 0))
    except Exception:
        series_id = 0
    try:
        season_number = max(0, int(source.get("season_number") or 0))
    except Exception:
        season_number = 0
    try:
        episode_number = max(0, int(source.get("episode_number") or 0))
    except Exception:
        episode_number = 0
    return {
        "title": str(
            source.get("title")
            or getattr(candidate, "title", "")
            or ""
        ),
        "year": year,
        "overview": str(source.get("overview") or "").strip()[:1200],
        "poster_url": _safe_movie_art_url(source.get("poster_url")),
        "backdrop_url": _safe_movie_art_url(source.get("backdrop_url")),
        "media_type": media_type,
        "tmdb_id": tmdb_id,
        "series_id": series_id,
        "series_title": str(source.get("series_title") or "").strip()[:180],
        "season_number": season_number,
        "episode_number": episode_number,
        "episode_title": str(source.get("episode_title") or "").strip()[:180],
        "runtime_minutes": max(0, int(source.get("runtime") or 0)) if str(source.get("runtime") or "").isdigit() else 0,
    }


async def _state_payload(room: MovieNightRoom, user_id: int) -> dict[str, Any]:
    movie_manager = get_movie_night_manager()
    torrent_manager = get_torrent_manager()
    room_mode = str(getattr(room, "mode", "watch_party") or "watch_party")
    private_mode = room_mode == "private"
    session_fallback_title = "Private Session" if private_mode else "Watch Party"
    session = await torrent_manager.get(room.stream_token) if room.stream_token else None
    if session is not None and not torrent_manager.session_usable(session):
        await torrent_manager.discard_unusable_session(room.stream_token)
        session = None

    candidate = room.candidates.get(room.current_candidate_id) if room.current_candidate_id else None
    variant = (
        candidate.variants.get(room.current_variant_id)
        if candidate is not None and room.current_variant_id
        else None
    )

    title = candidate.title if candidate is not None else (
        session.release_metadata.get("title")
        if session is not None and isinstance(session.release_metadata, dict)
        else ""
    )
    source = ""
    if variant is not None and isinstance(variant.metadata, dict):
        release = variant.metadata.get("release_name")
        if isinstance(release, dict):
            source = str(release.get("source") or "")

    viewer = room.viewers.get(int(user_id))
    consumer_key = ""
    cast_consumer_key = ""
    if session is not None:
        page_session = str(getattr(viewer, "client_session_id", "") or "").strip()[:64]
        consumer_key = f"movie:{int(user_id)}:{page_session or 'legacy'}"
        cast_consumer_key = f"cast:{int(user_id)}:{page_session or 'legacy'}"
    media_missing = bool(room.stream_token and session is None)
    stream_url = (
        torrent_manager.stream_url(
            session,
            ttl_seconds=21600,
            consumer_key=consumer_key,
        )
        if session is not None
        else ""
    )
    cast_stream_url = (
        torrent_manager.stream_url(
            session,
            ttl_seconds=21600,
            consumer_key=cast_consumer_key,
        )
        if session is not None
        else ""
    )
    try:
        torrent_status = torrent_manager.status(session) if session is not None else {}
    except TorrentSessionUnavailableError:
        if room.stream_token:
            await torrent_manager.discard_unusable_session(room.stream_token)
        session = None
        stream_url = ""
        consumer_key = ""
        torrent_status = {}
    swarm = _swarm_display(torrent_status, variant)
    sync_ready = bool(
        int(user_id) == int(room.host_id)
        or (viewer is not None and viewer.sync_ready)
    )
    sync_status = (
        "host"
        if int(user_id) == int(room.host_id)
        else "synced"
        if sync_ready
        else "joining"
    )
    sync_requested = bool(
        viewer is not None
        and viewer.sync_requested
    )
    sync_target = room.current_position()
    if (
        viewer is not None
        and not sync_ready
        and viewer.sync_requested
    ):
        sync_target = float(viewer.sync_target_position)
    active_viewer_ids = movie_manager.active_viewers(room)
    buffer_quorum = movie_manager.buffer_quorum_viewers(room)
    viewer_summaries = _discord_viewer_summaries(room, active_viewer_ids)
    discord_context = _discord_room_context(room, int(user_id))
    movie_metadata = _candidate_web_metadata(candidate)
    if not movie_metadata["title"]:
        movie_metadata["title"] = str(title or session_fallback_title)

    queue_items: list[dict[str, Any]] = []
    for queued_id in list(room.queue)[:12]:
        queued = room.candidates.get(str(queued_id))
        if queued is None:
            continue
        queued_meta = _candidate_web_metadata(queued)
        queue_items.append(
            {
                "candidate_id": str(queued.candidate_id),
                "title": queued_meta["title"] or "Untitled",
                "year": queued_meta["year"],
                "poster_url": queued_meta["poster_url"],
                "is_current": str(queued.candidate_id) == str(room.current_candidate_id or ""),
            }
        )

    return {
        "ok": True,
        "room_id": room.room_id,
        "mode": room_mode,
        "private": private_mode,
        "title": str(title or session_fallback_title),
        "movie": movie_metadata,
        "queue": queue_items,
        "release_source": source,
        "state": room.playback_state,
        "position_seconds": round(room.current_position(), 3),
        "is_host": int(user_id) == int(room.host_id),
        "host_active": movie_manager.host_active(room),
        "viewer_count": len(active_viewer_ids),
        "viewers": viewer_summaries,
        "discord": discord_context,
        "buffer_quorum_count": len(buffer_quorum),
        "sync_status": sync_status,
        "sync_ready": sync_ready,
        "sync_requested": sync_requested,
        "sync_target_position": round(sync_target, 3),
        "stream_token": room.stream_token,
        "stream_consumer": consumer_key,
        "stream_url": stream_url,
        "cast_stream_url": cast_stream_url,
        "media_content_type": (
            media_content_type(session.file_name)
            if session is not None
            else ""
        ),
        "cast_supported_media": (
            media_content_type(session.file_name)
            in {"video/mp4", "video/webm", "video/mp2t", "video/mpeg"}
            if session is not None
            else False
        ),
        "discord_url": (
            f"https://discord.com/channels/{int(room.guild_id)}/{int(room.channel_id)}"
            if int(room.guild_id) > 0 and int(room.channel_id) > 0
            else ""
        ),
        "media_missing": media_missing,
        "torrent": {
            "name": torrent_status.get("name", ""),
            "size": torrent_status.get("size", 0),
            "downloaded": torrent_status.get("downloaded", 0),
            "progress": torrent_status.get("progress", 0.0),
            "download_rate": torrent_status.get("download_rate", 0),
            "peers": swarm["peers"],
            "seeds": swarm["seeds"],
            "leechers": swarm["leechers"],
            "swarm_source": swarm["source"],
            "buffer": torrent_status.get("buffer", {}),
        },
        "open_vote": _open_vote(room),
        "ended": bool(room.ended),
    }


async def movie_night_state(request: web.Request) -> web.Response:
    room, uid = await _room_and_user(request)
    return web.json_response(await _state_payload(room, uid))


def _float(value: Any, default: float = 0.0) -> float:
    try:
        return max(0.0, float(value))
    except Exception:
        return float(default)


def _preserve_refresh_telemetry(
    viewer: Any,
    *,
    position: float,
    duration: float,
    buffered: float,
    paused: bool,
    client_session_id: str,
    has_stream: bool,
) -> tuple[float, float, float, bool, bool]:
    same_client = bool(
        viewer is not None
        and client_session_id
        and str(getattr(viewer, "client_session_id", "") or "") == client_session_id
    )
    previous_duration = float(getattr(viewer, "media_duration_seconds", 0.0) or 0.0)
    warming_after_refresh = bool(
        has_stream
        and same_client
        and duration <= 0.0
        and previous_duration > 0.0
    )
    if not warming_after_refresh:
        return position, duration, buffered, paused, False

    previous_position = max(
        0.0,
        float(getattr(viewer, "position_seconds", position) or 0.0),
    )
    previous_buffered = max(
        previous_position,
        float(getattr(viewer, "buffered_until_seconds", previous_position) or 0.0),
    )
    return (
        previous_position,
        previous_duration,
        previous_buffered,
        bool(getattr(viewer, "paused", paused)),
        True,
    )


async def movie_night_heartbeat(request: web.Request) -> web.Response:
    room, uid = await _room_and_user(request)
    if room.ended:
        return web.json_response(await _state_payload(room, uid))
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}

    position = _float(payload.get("position_seconds"))
    duration = _float(payload.get("duration_seconds"))
    buffered = max(position, _float(payload.get("buffered_until_seconds"), position))
    paused = bool(payload.get("paused", True))
    client_session_id = str(payload.get("client_session_id") or "").strip()[:96]
    sync_requested = bool(payload.get("sync_requested", False))

    torrent_manager = get_torrent_manager()
    movie_manager = get_movie_night_manager()
    session = await torrent_manager.get(room.stream_token) if room.stream_token else None
    if session is not None and not torrent_manager.session_usable(session):
        await torrent_manager.discard_unusable_session(room.stream_token)
        session = None
    viewer_before = room.viewers.get(int(uid))
    position, duration, buffered, paused, refresh_warmup = _preserve_refresh_telemetry(
        viewer_before,
        position=position,
        duration=duration,
        buffered=buffered,
        paused=paused,
        client_session_id=client_session_id,
        has_stream=session is not None,
    )
    joining = bool(
        session is not None
        and int(uid) != int(room.host_id)
        and (viewer_before is None or not viewer_before.sync_ready)
    )

    byte_position = 0
    buffered_byte = 0
    sync_buffer_target_seconds = 0.0
    if session is not None and refresh_warmup and viewer_before is not None:
        byte_position = max(0, int(viewer_before.byte_position))
        buffered_byte = max(
            byte_position,
            int(viewer_before.buffered_until_byte),
        )
    elif session is not None and duration > 0:
        byte_position = int(min(1.0, position / duration) * session.file_size)
        buffered_byte = int(min(1.0, buffered / duration) * session.file_size)

    if session is not None and duration > 0:
        if joining:
            target_seconds = max(0.0, room.current_position())
            target_byte = int(
                min(1.0, target_seconds / duration) * session.file_size
            )
            target_end = min(
                session.file_size - 1,
                target_byte + 1024 * 1024 - 1,
            )
            plan = torrent_manager.prepare_playback_request(
                session,
                target_byte,
                target_end,
            )
            sync_buffer_target_seconds = min(
                15.0,
                max(8.0, float(getattr(plan, "target_seconds", 8.0) or 8.0)),
            )
            prioritize_end = min(
                session.file_size - 1,
                target_byte + max(
                    1024 * 1024,
                    int(getattr(plan, "target_bytes", 0) or 0),
                ),
            )
            torrent_manager.prioritize_range(
                session,
                target_byte,
                prioritize_end,
                readahead_bytes=max(
                    1024 * 1024,
                    int(getattr(plan, "target_bytes", 0) or 0),
                ),
            )

    movie_manager.heartbeat(
        room.room_id,
        user_id=uid,
        position_seconds=position,
        byte_position=byte_position,
        buffered_until_byte=buffered_byte,
        paused=paused,
        buffered_until_seconds=buffered,
        media_duration_seconds=duration,
        sync_buffer_target_seconds=sync_buffer_target_seconds,
        client_session_id=client_session_id,
        sync_requested=sync_requested,
    )

    if session is not None:
        corridor = movie_manager.group_buffer_corridor(room.room_id)
        if corridor is not None:
            start, weakest_buffer_end, leader = corridor
            request_end = min(
                session.file_size - 1,
                max(start, weakest_buffer_end, leader),
            )
            plan = torrent_manager.prepare_playback_request(
                session,
                start,
                min(session.file_size - 1, start + 1024 * 1024 - 1),
            )
            target_end = min(
                session.file_size - 1,
                max(request_end, start + int(plan.target_bytes)),
            )
            torrent_manager.prioritize_range(
                session,
                start,
                target_end,
                readahead_bytes=plan.target_bytes,
            )

    return web.json_response(await _state_payload(room, uid))


async def movie_night_action(request: web.Request) -> web.Response:
    room, uid = await _room_and_user(request)
    if int(uid) != int(room.host_id):
        raise web.HTTPForbidden(text="Only the active Cinema host controls playback.")

    try:
        payload = await request.json()
    except Exception:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}

    action = str(payload.get("action") or "").strip().lower()
    if action not in {"pause", "resume", "seek", "end"}:
        raise web.HTTPBadRequest(text="Unsupported Cinema playback action.")

    action_payload: dict[str, Any] = {}
    if action == "seek":
        action_payload["seconds"] = _float(payload.get("seconds"))

    manager = get_movie_night_manager()
    manager.join_room(room.room_id, user_id=uid)
    manager.apply_host_action(
        room.room_id,
        host_id=uid,
        action=action,
        payload=action_payload,
    )
    if action == "end":
        await terminate_movie_night_room(room)
    return web.json_response(await _state_payload(room, uid))


async def movie_night_transfer_host(request: web.Request) -> web.Response:
    room, uid = await _room_and_user(request)
    if int(uid) != int(room.host_id):
        raise web.HTTPForbidden(text="Only the active Cinema host can pass host.")

    try:
        payload = await request.json()
    except Exception:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}

    try:
        new_host_id = int(payload.get("new_host_id") or 0)
    except Exception:
        new_host_id = 0
    if new_host_id <= 0:
        raise web.HTTPBadRequest(text="Choose an active viewer to receive host control.")

    manager = get_movie_night_manager()
    try:
        manager.transfer_host(
            room.room_id,
            current_host_id=uid,
            new_host_id=new_host_id,
        )
    except PermissionError as exc:
        raise web.HTTPForbidden(text=str(exc))
    except ValueError as exc:
        raise web.HTTPBadRequest(text=str(exc))

    return web.json_response(await _state_payload(room, uid))


async def movie_night_invite_options(request: web.Request) -> web.Response:
    room, uid = await _room_and_user(request)
    if int(uid) != int(room.host_id):
        raise web.HTTPForbidden(text="Only the Cinema host can invite a Discord user.")
    if str(getattr(room, "mode", "watch_party") or "watch_party") != "private":
        raise web.HTTPBadRequest(text="This Cinema session is already a Watch Party.")

    query = str(request.query.get("q", "") or "").strip()
    return web.json_response(
        {
            "members": _discord_invite_options(room, query),
            "discord_url": (
                f"https://discord.com/channels/{int(room.guild_id)}/{int(room.channel_id)}"
                if int(room.guild_id) > 0 and int(room.channel_id) > 0
                else ""
            ),
        }
    )


async def movie_night_promote_watch_party(request: web.Request) -> web.Response:
    room, uid = await _room_and_user(request)
    if int(uid) != int(room.host_id):
        raise web.HTTPForbidden(text="Only the Private Session host can start a Watch Party.")
    if str(getattr(room, "mode", "watch_party") or "watch_party") != "private":
        raise web.HTTPBadRequest(text="This Cinema session is already a Watch Party.")

    try:
        payload = await request.json()
    except Exception:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}

    try:
        invitee_id = int(payload.get("user_id") or 0)
    except Exception:
        invitee_id = 0
    if invitee_id <= 0 or invitee_id == int(room.host_id):
        raise web.HTTPBadRequest(text="Choose another Discord member to invite.")

    target = _discord_invite_target(room, invitee_id)
    if target is None:
        raise web.HTTPBadRequest(
            text="That Discord member is not available in the bot's current server cache. "
            "Use the Discord Cinema picker instead."
        )

    manager = get_movie_night_manager()
    manager.promote_private_to_watch_party(
        room.room_id,
        host_id=uid,
    )
    watch_url = movie_night_watch_url(room.room_id, invitee_id)
    dm_sent = await _dm_watch_party_invite(
        room,
        user_id=invitee_id,
        watch_url=watch_url,
    )
    announced = await _announce_watch_party_promotion(
        room,
        invitee_id=invitee_id,
    )

    state = await _state_payload(room, uid)
    state["invite"] = {
        "user_id": invitee_id,
        "display_name": str(target.get("display_name") or invitee_id),
        "watch_url": watch_url,
        "dm_sent": dm_sent,
        "announced": announced,
    }
    return web.json_response(state)


async def _media_source_state(room: MovieNightRoom, uid: int) -> dict[str, Any]:
    is_host = int(uid) == int(room.host_id)
    state = await cinema_feed_state(
        int(room.guild_id),
        can_manage=is_host,
        refresh=False,
    )
    # Keep the historical key for the Watch-page client while the shared
    # service uses the clearer permission name.
    state["is_host"] = is_host
    return state


async def movie_night_sources(request: web.Request) -> web.Response:
    room, uid = await _room_and_user(request)
    return web.json_response(await _media_source_state(room, uid))


async def movie_night_source_action(request: web.Request) -> web.Response:
    room, uid = await _room_and_user(request)
    if int(uid) != int(room.host_id):
        raise web.HTTPForbidden(text="Only the Cinema host can manage media sources.")

    try:
        payload = await request.json()
    except Exception:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}

    try:
        await mutate_cinema_feed(
            int(room.guild_id),
            actor_id=int(uid),
            action=str(payload.get("action") or ""),
            payload=payload,
        )
    except LookupError as exc:
        raise web.HTTPNotFound(text=str(exc))
    except CinemaFeedConflict as exc:
        raise web.HTTPConflict(text=str(exc))
    except ValueError as exc:
        raise web.HTTPBadRequest(text=str(exc))

    return web.json_response(await _media_source_state(room, uid))


async def movie_night_queue_action(request: web.Request) -> web.Response:
    room, uid = await _room_and_user(request)
    if int(uid) != int(room.host_id):
        raise web.HTTPForbidden(text="Only the active Cinema host can manage the queue.")

    try:
        payload = await request.json()
    except Exception:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}

    action = str(payload.get("action") or "").strip().lower()
    candidate_id = str(payload.get("candidate_id") or "").strip()
    manager = get_movie_night_manager()

    try:
        if action == "remove":
            manager.remove_queued(
                room.room_id,
                host_id=uid,
                candidate_id=candidate_id,
            )
        elif action == "move_up":
            manager.move_queued(
                room.room_id,
                host_id=uid,
                candidate_id=candidate_id,
                offset=-1,
            )
        elif action == "move_down":
            manager.move_queued(
                room.room_id,
                host_id=uid,
                candidate_id=candidate_id,
                offset=1,
            )
        elif action == "clear":
            manager.clear_queue(room.room_id, host_id=uid)
        else:
            raise web.HTTPBadRequest(text="Unsupported Cinema queue action.")
    except PermissionError as exc:
        raise web.HTTPForbidden(text=str(exc))
    except LookupError as exc:
        raise web.HTTPNotFound(text=str(exc))

    return web.json_response(await _state_payload(room, uid))


def _watch_html(room_id: str, uid: int, query: str) -> str:
    boot = json.dumps(
        {
            "roomId": room_id,
            "uid": uid,
            "query": query,
        },
        separators=(",", ":"),
    ).replace("</", "<\\/")

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="color-scheme" content="dark">
<meta name="theme-color" content="#030806">
<link rel="icon" type="image/webp" href="/movie/assets/dank-cinema-brand-mark.webp?v={_BRAND_ASSET_VERSION}">
<title>Dank Cinema • The 420 Lobby</title>
<style>
@import url("https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700;800;900&display=swap");
:root {{
  color-scheme:dark;
  font-family:"Inter",ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;
  --bg:#030806;
  --bg-raised:#07110e;
  --panel:#0a1512;
  --panel-2:#0e1c18;
  --panel-3:#12231d;
  --line:rgba(190,255,145,.14);
  --line-strong:rgba(163,255,94,.46);
  --lime:#a7ff64;
  --lime-2:#79ef45;
  --discord:#8ea0ff;
  --text:#f6f8f7;
  --muted:#9fada7;
  --muted-2:#75827d;
  --danger:#ff6672;
  --focus:0 0 0 3px rgba(167,255,100,.22);
  --shadow:0 24px 70px rgba(0,0,0,.38);
  --shadow-soft:0 14px 38px rgba(0,0,0,.26);
  --radius-xl:22px;
  --radius-lg:16px;
  --radius-md:12px;
  --motion-fast:140ms;
  --motion-medium:240ms;
}}
* {{ box-sizing:border-box; }}
html {{ background:var(--bg); scroll-behavior:smooth; }}
body {{
  margin:0;
  min-height:100vh;
  overflow-x:hidden;
  color:var(--text);
  background:
    radial-gradient(70% 40% at 78% -8%,rgba(40,125,77,.22),transparent 68%),
    radial-gradient(55% 38% at -8% 30%,rgba(111,255,69,.065),transparent 72%),
    linear-gradient(180deg,#06110e 0%,#050d0b 46%,#020504 100%);
}}
body::before {{
  content:"";
  position:fixed;inset:0;z-index:-1;pointer-events:none;
  opacity:.07;
  background-image:
    linear-gradient(90deg,rgba(255,255,255,.014) 1px,transparent 1px),
    linear-gradient(rgba(255,255,255,.012) 1px,transparent 1px);
  background-size:9px 9px,13px 13px;
  mask-image:linear-gradient(180deg,#000 0%,rgba(0,0,0,.7) 55%,transparent 100%);
}}
button,input {{ font:inherit; }}
button {{ -webkit-tap-highlight-color:transparent; }}
button,input,select,summary {{ outline:none; }}
button:focus-visible,input:focus-visible,select:focus-visible,summary:focus-visible {{
  box-shadow:var(--focus);
}}
button {{ cursor:pointer; }}
.shell {{ width:min(1480px,100%); margin:0 auto; padding:0 22px 160px; overflow-x:hidden; }}
.site-header {{
  position:relative; z-index:20;
  margin:0 -22px;
  padding:14px 22px 6px;
  overflow:hidden;
  background:
    radial-gradient(420px 150px at 105px 58px,rgba(89,174,67,.12),transparent 74%),
    linear-gradient(180deg,rgba(2,7,6,.88) 0%,rgba(5,15,12,.36) 78%,transparent 100%);
}}
.site-header::after {{
  content:"";
  position:absolute;left:0;right:0;bottom:0;height:1px;
  background:linear-gradient(90deg,transparent,rgba(165,255,103,.24),transparent);
  pointer-events:none;
}}
.brand-row {{ display:flex; align-items:center; justify-content:space-between; width:100%; min-width:0; }}
.brand {{
  position:relative;width:min(790px,100%);min-width:0;overflow:visible;
  isolation:isolate;
}}
.brand::before {{
  content:"";
  position:absolute;inset:8% -5% -10% -4%;z-index:-1;
  background:
    radial-gradient(ellipse at 17% 50%,rgba(91,191,66,.12),transparent 44%),
    radial-gradient(ellipse at 58% 50%,rgba(130,255,75,.055),transparent 58%);
  filter:blur(17px);pointer-events:none;
}}
.brand-art {{
  display:grid;
  grid-template-columns:clamp(92px,15vw,150px) minmax(0,1fr);
  align-items:center;
  gap:clamp(3px,.7vw,10px);
  width:100%;
}}
.brand-mark-art,.brand-wordmark-art {{
  display:block;max-width:100%;height:auto;object-fit:contain;
  filter:drop-shadow(0 10px 28px rgba(0,0,0,.42));
  transform:translateZ(0);
}}
.brand-mark-art {{ width:100%;justify-self:start; }}
.brand-wordmark-art {{ width:100%;justify-self:start; }}

.nav {{
  display:flex; align-items:center; gap:5px;
  overflow-x:auto; scrollbar-width:none; margin:15px -4px 9px; padding:0 4px 5px;
}}
.nav::-webkit-scrollbar {{ display:none; }}
.nav-item {{
  display:flex; align-items:center; gap:8px; flex:0 0 auto;
  border:1px solid transparent; border-radius:999px;
  padding:9px 13px; color:#c6cfcb; background:transparent; font-weight:750;
}}
.nav-item svg {{ width:18px; height:18px; }}
.nav-item.active {{
  color:var(--lime);
  border-color:rgba(131,255,66,.45);
  background:linear-gradient(180deg,rgba(87,178,51,.23),rgba(47,93,35,.18));
  box-shadow:inset 0 0 22px rgba(108,255,48,.06);
}}
.theater-grid {{
  display:grid;
  grid-template-columns:minmax(0,1fr);
  gap:14px 22px;
  align-items:start;
}}
.theater-primary {{ min-width:0; }}
.theater-sidecar {{ min-width:0; }}
.theater-sidecar > :first-child {{ margin-top:0; }}
.theater {{
  position:relative;
  overflow:hidden;
  border:1px solid rgba(207,255,190,.28);
  border-radius:20px;
  background:#000;
  box-shadow:var(--shadow);
}}
.video-stage {{
  position:relative;
  aspect-ratio:16/9;
  min-height:228px;
  overflow:hidden;
  contain:layout paint;
  isolation:isolate;
  background-color:#000;
  background-image:
    linear-gradient(180deg,rgba(0,0,0,.06),rgba(0,0,0,.14)),
    var(--backdrop-image,none);
  background-size:cover;
  background-position:center;
}}
.video-stage::before {{
  content:"";
  position:absolute; inset:0;
  pointer-events:none;
  background:linear-gradient(180deg,rgba(0,0,0,.04) 0%,rgba(0,0,0,.08) 48%,rgba(0,0,0,.38) 100%);
  z-index:1;
}}
video {{
  position:absolute; inset:0; z-index:0;
  display:block; width:100%; height:100%;
  max-width:none; max-height:none;
  object-fit:contain; background:#000;
  transform:translateZ(0);
  backface-visibility:hidden;
}}
.video-stage:fullscreen,
.video-stage:-webkit-full-screen {{
  width:100vw;
  height:100vh;
  min-height:100vh;
  max-height:none;
  aspect-ratio:auto;
  border-radius:0;
  background:#000;
}}
.video-stage:fullscreen video,
.video-stage:-webkit-full-screen video {{
  inset:0;
  width:100vw;
  height:100vh;
  object-fit:contain;
}}
.video-stage:fullscreen .stage-top,
.video-stage:-webkit-full-screen .stage-top {{
  inset:calc(12px + env(safe-area-inset-top)) 18px auto 18px;
}}
.video-stage:fullscreen .player-chrome,
.video-stage:-webkit-full-screen .player-chrome {{
  padding:52px max(22px,env(safe-area-inset-right)) calc(18px + env(safe-area-inset-bottom)) max(22px,env(safe-area-inset-left));
}}
.stage-top {{
  position:absolute; inset:12px 12px auto 12px;
  display:flex; align-items:center; justify-content:space-between; gap:8px;
  pointer-events:none;
  z-index:4;
  transition:opacity .18s ease,transform .18s ease;
}}
.room-pill {{
  display:flex; align-items:center; gap:8px;
  min-width:0; max-width:calc(100% - 58px); padding:8px 11px;
  border:1px solid rgba(255,255,255,.12);
  border-radius:12px;
  background:rgba(3,10,8,.78);
  backdrop-filter:blur(12px);
  font-size:.78rem; font-weight:800;
}}
.room-pill .live-dot {{ width:8px;height:8px;border-radius:50%;background:var(--lime);box-shadow:0 0 12px rgba(159,255,86,.75); }}
#role {{ color:#d7dfdc; min-width:0; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }}
.cast {{
  pointer-events:auto;
  position:relative;
  width:46px;height:46px; display:grid; place-items:center;
  border-radius:50%; border:1px solid rgba(255,255,255,.18);
  color:#fff; background:rgba(3,10,8,.72); backdrop-filter:blur(12px);
}}
.cast[hidden] {{ display:none !important; }}
.cast:disabled {{ opacity:.35; }}
.cast.unavailable {{ opacity:.5; border-style:dashed; }}
.cast.unavailable::after {{
  content:""; position:absolute; width:28px; height:2px;
  background:currentColor; transform:rotate(-43deg); border-radius:999px;
}}
.cast.connected {{ color:var(--lime); border-color:var(--line-strong); }}
.cast svg {{ width:23px;height:23px; }}
.center-play {{
  position:absolute; inset:50% auto auto 50%; transform:translate(-50%,-50%);
  width:84px;height:84px; display:grid; place-items:center;
  border-radius:50%; border:2px solid rgba(255,255,255,.6);
  color:#fff; background:rgba(5,12,10,.5); backdrop-filter:blur(8px);
  box-shadow:0 10px 38px rgba(0,0,0,.32);
  z-index:4;
  transition:opacity .18s ease,transform .18s ease;
}}
.center-play svg {{ width:34px;height:34px; }}
.player-chrome {{
  position:absolute; inset:auto 0 0;
  padding:44px 14px 13px;
  background:linear-gradient(180deg,transparent 0%,rgba(0,0,0,.58) 32%,rgba(0,0,0,.94) 100%);
  z-index:4;
  transition:opacity .18s ease,transform .18s ease;
}}
.video-stage.controls-hidden {{ cursor:none; }}
.video-stage.controls-hidden .stage-top,
.video-stage.controls-hidden .center-play,
.video-stage.controls-hidden .player-chrome {{
  opacity:0;
  pointer-events:none;
}}
.video-stage.controls-hidden .stage-top {{ transform:translateY(-6px); }}
.video-stage.controls-hidden .center-play {{ transform:translate(-50%,-50%) scale(.92); }}
.video-stage.controls-hidden .player-chrome {{ transform:translateY(9px); }}
.tap-skip-feedback {{
  position:absolute;top:50%;z-index:5;
  min-width:70px;padding:12px 14px;
  border-radius:999px;
  display:grid;place-items:center;
  color:#fff;background:rgba(5,12,10,.72);
  border:1px solid rgba(255,255,255,.18);
  backdrop-filter:blur(8px);
  font-size:.82rem;font-weight:900;
  opacity:0;transform:translateY(-50%) scale(.88);
  pointer-events:none;
  transition:opacity .16s ease,transform .16s ease;
}}
.tap-skip-feedback.left {{ left:9%; }}
.tap-skip-feedback.right {{ right:9%; }}
.tap-skip-feedback.show {{ opacity:1;transform:translateY(-50%) scale(1); }}
.timeline-row {{ display:block; }}
.time-row {{ display:flex;align-items:center;justify-content:space-between;margin-top:6px;font-size:.72rem;font-weight:750; }}
.timeline {{
  width:100%; appearance:none; height:4px; border-radius:999px; outline:none;
  background:linear-gradient(90deg,var(--lime) 0 var(--progress,0%),rgba(255,255,255,.38) var(--progress,0%) 100%);
}}
.timeline::-webkit-slider-thumb {{ appearance:none; width:16px;height:16px;border-radius:50%;background:var(--lime);border:0;box-shadow:0 0 0 4px rgba(164,255,96,.12); }}
.timeline::-moz-range-thumb {{ width:16px;height:16px;border-radius:50%;background:var(--lime);border:0; }}
.control-row {{ display:flex; align-items:center; justify-content:center; gap:13px; margin-top:10px; }}
.player-button {{
  width:36px;height:36px; display:grid;place-items:center;
  border:0;border-radius:50%; color:#fff;background:transparent;
}}
.player-button svg {{ width:22px;height:22px; }}
.player-button.primary {{ width:44px;height:44px; }}
.player-button:disabled {{ opacity:.32; }}
.player-button[hidden] {{ display:none !important; }}
.player-button.active {{ color:var(--lime);background:rgba(120,220,67,.11); }}
.control-spacer {{ flex:1; }}
.volume-wrap {{ display:flex;align-items:center;gap:6px; }}
.volume {{ width:70px; accent-color:var(--lime); }}
.info {{
  display:grid;
  grid-template-columns:90px minmax(0,1fr);
  gap:15px;
  padding:20px 3px 4px;
}}
.info.no-poster {{ grid-template-columns:1fr; }}
.poster {{
  width:90px; aspect-ratio:2/3; border-radius:12px; overflow:hidden;
  border:1px solid rgba(255,255,255,.12); background:linear-gradient(145deg,#183529,#091410);
  box-shadow:0 12px 30px rgba(0,0,0,.28);
}}
.poster img {{ width:100%;height:100%;object-fit:cover;display:block; }}
.meta-main {{ min-width:0; }}
.title-row {{ display:flex;align-items:flex-start;justify-content:space-between;gap:10px;flex-wrap:wrap; }}
.movie-title {{
  margin:0; font-family:Georgia,"Times New Roman",serif;
  font-size:clamp(2rem,8vw,3rem); line-height:.95; letter-spacing:-.04em;
}}
.movie-meta {{ color:#aab5b0; margin-top:8px; font-size:.9rem; }}
.synopsis {{ color:#d4dbd8; margin:11px 0 0; line-height:1.46; font-size:.92rem; }}
.health {{
  display:flex;align-items:center;gap:7px;
  max-width:100%;
  border:1px solid rgba(152,255,82,.34);border-radius:999px;
  padding:8px 11px;color:var(--lime);font-size:.76rem;font-weight:850;
  background:rgba(72,128,44,.08);white-space:normal;overflow-wrap:anywhere;
}}
.health-dot {{ width:8px;height:8px;border-radius:50%;background:var(--lime);box-shadow:0 0 10px rgba(159,255,86,.6); }}
.viewer-strip {{ display:flex;align-items:center;justify-content:space-between;gap:10px;margin-top:14px;flex-wrap:wrap; }}
.viewer-cluster {{ display:flex;align-items:center;min-height:32px; }}
.viewer-avatar {{
  width:32px;height:32px;margin-left:-7px;border-radius:50%;
  display:grid;place-items:center;overflow:hidden;
  border:2px solid #08110e;background:#183229;color:#f1f7f4;
  font-size:.66rem;font-weight:900;text-transform:uppercase;
}}
.viewer-avatar:first-child {{ margin-left:0; }}
.viewer-avatar.host {{ box-shadow:0 0 0 1px var(--lime),0 0 14px rgba(167,255,100,.18); }}
.viewer-avatar img {{ width:100%;height:100%;object-fit:cover;display:block; }}
.watchers {{ display:flex;align-items:center;gap:6px;color:#dbe2df;font-size:.8rem; }}
.session-viewer-list {{ display:grid;gap:8px;margin-top:12px; }}
.session-viewer {{
  display:flex;align-items:center;gap:10px;padding:9px;
  border:1px solid rgba(255,255,255,.07);border-radius:12px;background:#0b1714;
}}
.session-viewer-copy {{ min-width:0;flex:1; }}
.session-viewer-name {{ font-weight:850;white-space:nowrap;overflow:hidden;text-overflow:ellipsis; }}
.session-viewer-role {{ margin-top:2px;color:#96a49e;font-size:.7rem; }}
.session-viewer-action {{
  border:1px solid rgba(151,255,84,.34);border-radius:999px;
  background:rgba(75,135,45,.12);color:var(--lime);
  padding:7px 10px;font-size:.7rem;font-weight:850;
}}
.quick-tabs {{
  display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:6px;
  margin-top:16px;padding:7px;
  border:1px solid rgba(255,255,255,.09);border-radius:16px;background:rgba(10,21,17,.72);
}}
.tab {{
  min-width:0;
  border:0;border-radius:12px;padding:10px 6px;color:#d8dfdc;background:transparent;
  font-size:clamp(.6rem,2.2vw,.72rem);font-weight:850;
  white-space:normal;overflow-wrap:anywhere;line-height:1.15;text-align:center;
}}
.tab.active {{ color:var(--lime); background:linear-gradient(180deg,rgba(86,176,50,.22),rgba(38,77,30,.22)); box-shadow:inset 0 0 0 1px rgba(148,255,80,.28); }}
#notice {{ min-height:1.35em; margin:12px 3px 0;color:#bdc8c3;font-size:.82rem; }}
.queue-panel,.diagnostics {{
  margin-top:12px;padding:15px;
  border:1px solid rgba(255,255,255,.09);border-radius:16px;
  background:rgba(8,18,14,.72);
}}
.feed-panel[hidden] {{ display:none !important; }}
.feed-toolbar {{ display:flex;align-items:center;justify-content:space-between;gap:10px;flex-wrap:wrap; }}
.feed-toolbar h2 {{ margin:0;font-size:1rem; }}
.feed-toolbar-copy {{ color:var(--muted);font-size:.7rem;line-height:1.35;max-width:50ch; }}
.feed-groups {{ display:grid;gap:12px;margin-top:12px; }}
.feed-group {{ display:grid;gap:7px; }}
.feed-group-title {{
  display:flex;align-items:center;justify-content:space-between;gap:8px;
  color:#dce5e1;font-size:.72rem;font-weight:900;text-transform:uppercase;letter-spacing:.08em;
}}
.feed-card {{
  border:1px solid rgba(255,255,255,.075);border-radius:13px;background:#0c1915;padding:10px;
}}
.feed-card-top {{ display:flex;align-items:flex-start;justify-content:space-between;gap:9px; }}
.feed-card-name {{ min-width:0;font-size:.82rem;font-weight:900;overflow-wrap:anywhere; }}
.feed-badges {{ display:flex;gap:5px;flex-wrap:wrap;margin-top:5px; }}
.feed-badge {{
  display:inline-flex;align-items:center;gap:4px;border:1px solid rgba(255,255,255,.09);
  border-radius:999px;padding:3px 7px;color:#aebbb5;font-size:.61rem;font-weight:800;
}}
.feed-badge.good {{ color:var(--lime);border-color:rgba(167,255,100,.25); }}
.feed-badge.off {{ color:#909b96; }}
.feed-meta {{ margin-top:7px;color:#85938d;font-size:.66rem;line-height:1.4;overflow-wrap:anywhere; }}
.feed-discovered {{ margin-top:8px;display:flex;gap:5px;flex-wrap:wrap; }}
.feed-title-chip {{
  max-width:100%;border-radius:999px;background:#10241d;padding:4px 7px;
  color:#cdd7d2;font-size:.63rem;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;
}}
.feed-actions {{ display:flex;gap:5px;flex-wrap:wrap;margin-top:9px; }}
.feed-action {{
  border:1px solid rgba(255,255,255,.09);border-radius:9px;background:#10201b;
  color:#dce5e1;padding:7px 8px;font-size:.66rem;font-weight:850;
}}
.feed-action.primary {{ color:var(--lime);border-color:rgba(167,255,100,.28); }}
.feed-action.danger {{ color:#ff7a84;border-color:rgba(255,102,114,.24); }}
.feed-empty {{ padding:14px;border:1px dashed rgba(255,255,255,.1);border-radius:12px;color:#8d9a94;font-size:.74rem; }}
.feed-form {{
  display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-top:12px;padding-top:12px;
  border-top:1px solid rgba(255,255,255,.07);
}}
.feed-form .wide {{ grid-column:1 / -1; }}
.feed-field {{ display:grid;gap:4px; }}
.feed-field label {{ color:#93a19b;font-size:.63rem;font-weight:800;text-transform:uppercase;letter-spacing:.05em; }}
.feed-input,.feed-select {{
  min-width:0;width:100%;box-sizing:border-box;border:1px solid rgba(255,255,255,.11);
  border-radius:10px;background:#0d1b17;color:#f4f7f5;padding:9px;font-size:.72rem;
}}
.feed-form-actions {{ grid-column:1 / -1;display:flex;gap:7px;justify-content:flex-end; }}

.section-head {{ display:flex;align-items:center;justify-content:space-between;gap:10px;margin-bottom:12px; }}
.section-head h2 {{ margin:0;font-size:1.04rem; }}
.section-head span {{ color:#9ca8a3;font-size:.76rem; }}
#queueList {{ display:grid;gap:8px; }}
.queue-empty {{
  display:grid;grid-template-columns:auto minmax(0,1fr) auto;align-items:center;gap:11px;
  padding:12px;border:1px dashed rgba(177,255,125,.16);border-radius:12px;
  background:linear-gradient(135deg,rgba(18,36,29,.64),rgba(7,16,13,.66));
}}
.queue-empty-mark {{
  width:38px;height:38px;border-radius:11px;display:grid;place-items:center;
  border:1px solid rgba(167,255,100,.18);color:var(--lime);
  background:rgba(90,170,55,.08);font-size:1rem;
}}
.queue-empty-copy {{ min-width:0; }}
.queue-empty-title {{ color:#e7edea;font-size:.82rem;font-weight:850; }}
.queue-empty-sub {{ margin-top:3px;color:#8d9a94;font-size:.7rem;line-height:1.35; }}
.queue-empty-action {{
  border:1px solid rgba(142,160,255,.25);border-radius:10px;
  background:rgba(83,96,164,.11);color:#cdd4ff;padding:8px 10px;
  font-size:.68rem;font-weight:850;white-space:nowrap;
}}
@media (max-width:480px) {{
  .queue-empty {{ grid-template-columns:auto minmax(0,1fr); }}
  .queue-empty-action {{ grid-column:1 / -1;width:100%; }}
}}
.queue-item {{
  display:grid;grid-template-columns:58px minmax(0,1fr);gap:10px;align-items:center;
  padding:8px;border:1px solid rgba(255,255,255,.07);border-radius:12px;background:#0a1512;
}}
.queue-item.manageable {{ grid-template-columns:58px minmax(0,1fr) auto; }}
.queue-actions {{ display:flex;align-items:center;gap:4px; }}
.queue-action {{
  width:30px;height:30px;border-radius:9px;
  border:1px solid rgba(255,255,255,.09);background:#10201a;color:#dce5e1;
  font-size:.72rem;font-weight:900;
}}
.queue-action.danger {{ color:#ff727d;border-color:rgba(255,93,107,.24); }}
.queue-action:disabled {{ opacity:.28; }}
.queue-head-actions {{ display:flex;align-items:center;gap:8px; }}
.queue-clear {{
  border:0;background:transparent;color:#b7c2bd;
  padding:4px 0;font-size:.72rem;font-weight:750;
}}
.queue-clear[hidden] {{ display:none !important; }}
.queue-art {{ width:58px;aspect-ratio:16/10;border-radius:9px;overflow:hidden;background:#13231d; }}
.queue-art img {{ width:100%;height:100%;object-fit:cover; }}
.queue-title {{ font-weight:850;white-space:nowrap;overflow:hidden;text-overflow:ellipsis; }}
.queue-sub {{ color:#97a39e;font-size:.72rem;margin-top:3px; }}
.diagnostics summary {{ cursor:pointer;color:#cbd5d0;font-weight:850; }}
.grid {{ display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:8px;margin-top:12px; }}
.stat {{ min-height:62px;padding:10px;border-radius:11px;background:#0d1916; }}
.stat b {{ display:block;margin-bottom:5px;color:#84928c;font-size:.68rem;text-transform:uppercase;letter-spacing:.07em; }}
.quality-control {{
  display:flex;align-items:center;justify-content:space-between;gap:12px;
  margin-top:12px;padding:10px;border-radius:11px;background:#0d1916;
}}
.quality-control label {{ color:#cbd5d0;font-size:.76rem;font-weight:800; }}
.quality-select {{
  border:1px solid rgba(255,255,255,.11);border-radius:9px;
  background:#10201b;color:#f4f7f5;padding:7px 9px;font-weight:750;
}}
.quality-note {{ margin-top:8px;color:#84928c;font-size:.68rem;line-height:1.35; }}
.keyboard-help {{ margin-top:10px;color:#84928c;font-size:.68rem;line-height:1.45; }}
.keyboard-help kbd {{
  display:inline-block;min-width:22px;padding:2px 5px;margin:0 2px;
  border:1px solid rgba(255,255,255,.12);border-radius:5px;background:#101a17;
  color:#dce5e1;text-align:center;font:inherit;font-size:.64rem;
}}
.host-sheet {{
  position:fixed;left:50%;bottom:0;z-index:40;transform:translateX(-50%);
  width:min(1120px,100%);max-height:min(72vh,560px);
  padding:9px 18px calc(18px + env(safe-area-inset-bottom));
  overflow-y:auto;overscroll-behavior:contain;
  border:1px solid rgba(197,255,175,.17);border-bottom:0;border-radius:22px 22px 0 0;
  background:rgba(9,20,16,.97);backdrop-filter:blur(18px);box-shadow:0 -20px 55px rgba(0,0,0,.5);
  display:none;
}}
.host-sheet.show {{ display:block; }}
.sheet-handle {{ width:42px;height:4px;border-radius:999px;background:#596660;margin:0 auto 8px; }}
.sheet-title {{ display:flex;align-items:center;justify-content:space-between;gap:10px;margin-bottom:10px; }}
.sheet-title strong {{ font-size:.94rem; }}
.close-sheet {{ border:0;background:transparent;color:#d8dfdc;font-size:1.25rem; }}
.host-actions {{ display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:8px; }}
.host-action {{
  min-width:0;min-height:94px;border:1px solid rgba(255,255,255,.1);border-radius:14px;
  background:#101b18;color:#f5f7f6;padding:10px 7px;font-weight:850;font-size:.72rem;
  overflow-wrap:anywhere;line-height:1.15;
}}
.host-action small {{
  display:block;color:#95a29c;font-size:.63rem;font-weight:650;margin-top:5px;
  line-height:1.25;overflow-wrap:anywhere;
}}
.host-action.danger {{ color:#ff737c;border-color:rgba(255,82,96,.32);background:rgba(91,23,29,.28); }}
#play {{ position:absolute;left:-9999px; }}
.host-launcher {{
  position:fixed;right:max(14px,env(safe-area-inset-right));
  bottom:calc(14px + env(safe-area-inset-bottom));z-index:39;
  display:flex;align-items:center;gap:7px;
  border:1px solid rgba(160,255,92,.42);border-radius:999px;
  padding:10px 13px;background:rgba(9,20,16,.94);color:var(--lime);
  box-shadow:0 10px 30px rgba(0,0,0,.42);backdrop-filter:blur(14px);
  font-size:.75rem;font-weight:900;
}}
.host-launcher[hidden] {{ display:none !important; }}
.discord-context {{
  display:flex;align-items:center;gap:10px;margin-top:12px;padding:10px;
  border:1px solid rgba(115,137,255,.22);border-radius:12px;
  background:rgba(32,40,74,.18);
}}
.discord-context-copy {{ min-width:0;flex:1; }}
.discord-context-title {{ font-size:.78rem;font-weight:900;color:#dfe4ff; }}
.discord-context-sub {{
  margin-top:2px;color:#aeb8c8;font-size:.69rem;
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis;
}}
.discord-live {{
  display:flex;align-items:center;gap:7px;
  max-width:100%;margin-top:10px;padding:7px 10px;
  border:1px solid rgba(115,137,255,.24);border-radius:999px;
  background:rgba(46,56,104,.16);color:#dfe4ff;
  font-size:.7rem;font-weight:800;
}}
.discord-live[hidden] {{ display:none !important; }}
.discord-live svg {{ width:17px;height:14px;flex:0 0 auto;color:#8ea0ff; }}
.discord-live span {{
  min-width:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;
}}
.invite-modal {{
  position:fixed;inset:0;z-index:60;
  display:grid;place-items:center;padding:18px;
  background:rgba(0,0,0,.72);backdrop-filter:blur(10px);
}}
.invite-modal[hidden] {{ display:none !important; }}
.invite-card {{
  width:min(520px,100%);max-height:min(78vh,640px);overflow:auto;
  border:1px solid rgba(183,255,132,.22);border-radius:20px;
  background:#0a1713;box-shadow:0 24px 70px rgba(0,0,0,.58);
  padding:16px;
}}
.invite-head {{ display:flex;align-items:flex-start;justify-content:space-between;gap:12px; }}
.invite-head h3 {{ margin:0;font-size:1.05rem; }}
.invite-head p {{ margin:5px 0 0;color:#9eaaa5;font-size:.76rem;line-height:1.35; }}
.invite-close {{ border:0;background:transparent;color:#dce3e0;font-size:1.35rem; }}
.invite-search-row {{ display:flex;gap:8px;margin-top:14px; }}
.invite-search {{
  min-width:0;flex:1;border:1px solid rgba(255,255,255,.12);border-radius:12px;
  background:#0e1d18;color:#fff;padding:10px 11px;outline:none;
}}
.invite-search:focus {{ border-color:rgba(163,255,94,.5);box-shadow:0 0 0 3px rgba(126,255,65,.08); }}
.invite-search-button,.invite-fallback {{
  border:1px solid rgba(163,255,94,.3);border-radius:12px;
  background:rgba(76,146,45,.16);color:var(--lime);
  padding:10px 12px;font-weight:850;
}}
.invite-results {{ display:grid;gap:8px;margin-top:12px; }}
.invite-result {{
  display:flex;align-items:center;gap:10px;width:100%;
  border:1px solid rgba(255,255,255,.08);border-radius:13px;
  background:#0d1b17;color:#f4f7f5;padding:9px;text-align:left;
}}
.invite-result:hover,.invite-result:focus {{ border-color:rgba(163,255,94,.38); }}
.invite-result-copy {{ min-width:0;flex:1; }}
.invite-result-name {{ font-weight:850;white-space:nowrap;overflow:hidden;text-overflow:ellipsis; }}
.invite-result-user {{ margin-top:2px;color:#94a09b;font-size:.7rem;white-space:nowrap;overflow:hidden;text-overflow:ellipsis; }}
.invite-status {{
  margin-top:12px;padding:10px;border-radius:11px;background:#0d1916;color:#c8d1cd;
  font-size:.76rem;line-height:1.4;overflow-wrap:anywhere;
}}
.invite-link-row {{ display:flex;gap:7px;margin-top:8px; }}
.invite-link {{
  min-width:0;flex:1;border:1px solid rgba(255,255,255,.08);border-radius:9px;
  background:#07110e;color:#cfd8d4;padding:8px;font-size:.68rem;
}}
.sync-row {{ display:flex;align-items:center;gap:8px;margin-top:12px; }}
#sync {{
  border:1px solid rgba(143,255,75,.32);border-radius:999px;background:rgba(86,170,52,.12);
  color:var(--lime);padding:8px 11px;font-weight:850;font-size:.75rem;
}}
#sync:disabled {{ opacity:.72; }}
.sr-only {{ position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;clip:rect(0,0,0,0);white-space:nowrap;border:0; }}
html[data-quality="high"] .theater {{
  box-shadow:0 26px 78px rgba(0,0,0,.44),0 0 0 1px rgba(163,255,94,.025);
}}
html[data-quality="high"] .video-stage::before {{
  background:
    linear-gradient(180deg,rgba(0,0,0,.02) 0%,rgba(0,0,0,.04) 48%,rgba(0,0,0,.42) 100%),
    radial-gradient(circle at 50% 48%,transparent 42%,rgba(0,0,0,.28) 100%);
}}
html[data-quality="standard"] .brand::before,
html[data-quality="standard"] body::before {{ opacity:.045; }}
html[data-quality="lite"] body::before,
html[data-quality="lite"] .brand::before {{ display:none; }}
html[data-quality="lite"] .site-header,
html[data-quality="lite"] .room-pill,
html[data-quality="lite"] .cast,
html[data-quality="lite"] .center-play,
html[data-quality="lite"] .host-sheet,
html[data-quality="lite"] .host-launcher {{
  backdrop-filter:none;
}}
html[data-quality="lite"] .theater,
html[data-quality="lite"] .poster {{ box-shadow:none; }}
html[data-quality="lite"] * {{ text-shadow:none !important; }}

@media (min-width:1080px) {{
  .theater-grid {{
    grid-template-columns:minmax(0,1fr) minmax(320px,360px);
    gap:18px 24px;
  }}
  .theater-primary {{ grid-column:1; }}
  .theater-sidecar {{
    grid-column:2;
    position:sticky;
    top:18px;
    display:grid;
    gap:12px;
    max-height:calc(100vh - 36px);
    overflow:auto;
    scrollbar-width:thin;
    padding-right:2px;
  }}
  .theater-sidecar .queue-panel,
  .theater-sidecar .diagnostics {{ margin-top:0; }}
  .info {{ padding-top:24px; }}
  .movie-title {{ font-size:clamp(2.3rem,4vw,4.1rem); }}
  .synopsis {{ max-width:78ch; }}
  .quick-tabs {{ max-width:760px; }}
  .host-sheet {{
    left:auto;right:24px;bottom:24px;transform:none;
    width:390px;max-height:min(76vh,620px);
    border:1px solid rgba(197,255,175,.17);border-radius:22px;
    padding:11px 14px 14px;
  }}
  .host-actions {{ grid-template-columns:repeat(2,minmax(0,1fr)); }}
  .host-action {{ min-height:108px; }}
}}
@media (min-width:1500px) {{
  .theater-grid {{ grid-template-columns:minmax(0,1fr) 390px; }}
  .video-stage {{ min-height:0; }}
}}
@media (prefers-reduced-motion:reduce) {{
  html {{ scroll-behavior:auto; }}
  *,*::before,*::after {{
    animation-duration:.01ms !important;
    animation-iteration-count:1 !important;
    transition-duration:.01ms !important;
    scroll-behavior:auto !important;
  }}
}}
@media (min-width:641px) and (max-width:1079px) {{
  .shell {{ padding-left:20px;padding-right:20px; }}
  .site-header {{ margin-left:-20px;margin-right:-20px;padding-left:20px;padding-right:20px; }}
  .brand {{ width:min(720px,92vw); }}
  .info {{ grid-template-columns:108px minmax(0,1fr);gap:18px; }}
  .poster {{ width:108px; }}
  .host-sheet {{ width:min(760px,calc(100% - 24px));bottom:12px;border-radius:22px;border-bottom:1px solid rgba(197,255,175,.17); }}
}}
@media (max-width:640px) {{
  .shell {{ padding-left:14px;padding-right:14px;padding-bottom:160px; }}
  .site-header {{ margin-left:-14px;margin-right:-14px;padding-left:10px;padding-right:10px; }}
  .brand {{ width:100%; }}
  .brand-art {{ grid-template-columns:clamp(78px,24vw,112px) minmax(0,1fr);gap:0; }}
  .brand-mark-art {{ transform:translateX(2px) translateZ(0); }}
  .brand-wordmark-art {{ transform:translateX(-2px) translateZ(0); }}
  .nav {{ margin-top:10px; }}
  .nav-item {{ padding:9px 11px;font-size:.75rem; }}
  .video-stage {{ min-height:0; }}
  .center-play {{
    width:66px;height:66px;border-width:1.5px;
    background:rgba(2,8,6,.56);box-shadow:0 8px 28px rgba(0,0,0,.34);
  }}
  .center-play svg {{ width:29px;height:29px; }}
  .player-chrome {{ padding:38px 9px 10px; }}
  .control-row {{ gap:8px; }}
  .player-button {{ width:34px;height:34px; }}
  .volume {{ display:none; }}
  .info {{ grid-template-columns:78px minmax(0,1fr);gap:12px; }}
  .poster {{ width:78px; }}
  .title-row {{ display:block; }}
  .movie-title {{ font-size:clamp(1.85rem,11vw,2.65rem);overflow-wrap:anywhere; }}
  .health {{ margin-top:10px;width:fit-content;max-width:100%; }}
  .synopsis {{ font-size:.84rem;overflow-wrap:anywhere; }}
  .viewer-strip {{ align-items:flex-start; }}
  .quick-tabs {{ grid-template-columns:repeat(4,minmax(0,1fr));overflow:visible;gap:3px;padding:5px; }}
  .tab {{ padding:9px 3px;font-size:clamp(.56rem,2.6vw,.68rem); }}
  .section-head {{ align-items:flex-start; }}
  .queue-head-actions {{ flex-wrap:wrap;justify-content:flex-end; }}
  .queue-item.manageable {{ grid-template-columns:50px minmax(0,1fr); }}
  .queue-item.manageable .queue-actions {{ grid-column:1 / -1;justify-content:flex-end; }}
  .queue-art {{ width:50px; }}
  .grid {{ grid-template-columns:1fr; }}
  .host-sheet {{ padding-left:12px;padding-right:12px; }}
  .host-actions {{ grid-template-columns:repeat(2,minmax(0,1fr));overflow:visible; }}
  .host-action {{ min-width:0;min-height:104px;font-size:.74rem; }}
  .feed-form {{ grid-template-columns:1fr; }}
  .feed-form .wide,.feed-form-actions {{ grid-column:1; }}
}}

@media (max-width:380px) {{
  .room-pill {{ gap:5px;padding:7px 8px;font-size:.68rem; }}
  .cast {{ width:42px;height:42px; }}
  .quick-tabs {{ grid-template-columns:repeat(2,minmax(0,1fr)); }}
  .host-actions {{ grid-template-columns:1fr 1fr; }}
}}
</style>
</head>
<body>
<div class="shell">
<header class="site-header">
  <div class="brand-row">
    <div class="brand">
      <div class="brand-art" aria-label="Dank Cinema — A feature of The 420 Lobby">
        <img
          class="brand-mark-art"
          src="/movie/assets/dank-cinema-brand-mark.webp?v={_BRAND_ASSET_VERSION}"
          alt=""
          aria-hidden="true"
          width="320"
          height="245"
          decoding="async"
          fetchpriority="high"
        >
        <img
          class="brand-wordmark-art"
          src="/movie/assets/dank-cinema-brand-wordmark.webp?v={_BRAND_ASSET_VERSION}"
          alt="Dank Cinema — A feature of The 420 Lobby"
          width="1040"
          height="289"
          decoding="async"
          fetchpriority="high"
        >
      </div>
    </div>
  </div>
  <nav class="nav" aria-label="Dank Cinema">
    <button class="nav-item active" type="button" data-nav="theater">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="5" width="18" height="16" rx="2"/><path d="M7 3v4M17 3v4M3 10h18"/></svg>Theater
    </button>
    <button class="nav-item" type="button" data-nav="queue">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M5 6h14M5 12h14M5 18h9"/></svg>Queue
    </button>
    <button class="nav-item" type="button" data-nav="details">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="9"/><path d="M12 10v6M12 7h.01"/></svg>Details
    </button>
    <button class="nav-item" type="button" data-nav="feeds" id="feedNav">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M5 19a2 2 0 1 0 0 .01"/><path d="M4 11a9 9 0 0 1 9 9"/><path d="M4 5a15 15 0 0 1 15 15"/></svg>Feeds
    </button>
    <button class="nav-item" type="button" data-nav="discord">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M8 8c2-1 6-1 8 0M7 16c3 2 7 2 10 0"/><path d="M5 5c5-2 9-2 14 0l2 12c-3 2-6 3-9 3s-6-1-9-3Z"/></svg>Discord
    </button>
  </nav>
</header>

<main class="theater-grid">
  <div class="theater-primary">
  <section class="theater" aria-label="Dank Cinema player">
    <div class="video-stage" id="videoStage">
      <video id="video" playsinline preload="metadata" controlslist="nodownload" aria-label="Dank Cinema video"></video>
      <div class="tap-skip-feedback left" id="tapSkipLeft" aria-live="polite">↶ 10s</div>
      <div class="tap-skip-feedback right" id="tapSkipRight" aria-live="polite">10s ↷</div>
      <div class="stage-top">
        <div class="room-pill"><span class="live-dot"></span><span id="roomMode">Cinema Session</span><span>│</span><span id="role">Connecting…</span></div>
        <button class="cast" id="cast" type="button" aria-label="Cast" title="Cast" hidden>
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M2 18a4 4 0 0 1 4 4"/><path d="M2 13a9 9 0 0 1 9 9"/><path d="M2 8a14 14 0 0 1 14 14"/><path d="M6 4h14a2 2 0 0 1 2 2v10"/></svg>
        </button>
      </div>
      <button class="center-play" id="centerPlay" type="button" aria-label="Play or pause">
        <svg id="centerPlayIcon" viewBox="0 0 24 24" fill="currentColor"><path d="M8 5v14l11-7Z"/></svg>
      </button>
      <div class="player-chrome">
        <div class="timeline-row">
          <input class="timeline" id="timeline" type="range" min="0" max="1000" value="0" aria-label="Playback position">
          <div class="time-row"><span id="currentTime">0:00</span><span id="duration">0:00</span></div>
        </div>
        <div class="control-row">
          <button class="player-button" id="rewind10" type="button" aria-label="Back 10 seconds">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M9 7H4V2"/><path d="M4 7a9 9 0 1 1-1 9"/><text x="8.2" y="16.5" fill="currentColor" stroke="none" font-size="8">10</text></svg>
          </button>
          <button class="player-button primary" id="playerToggle" type="button" aria-label="Play or pause">
            <svg id="playerToggleIcon" viewBox="0 0 24 24" fill="currentColor"><path d="M8 5v14l11-7Z"/></svg>
          </button>
          <button class="player-button" id="forward10" type="button" aria-label="Forward 10 seconds">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M15 7h5V2"/><path d="M20 7a9 9 0 1 0 1 9"/><text x="7.7" y="16.5" fill="currentColor" stroke="none" font-size="8">10</text></svg>
          </button>
          <span class="control-spacer"></span>
          <div class="volume-wrap">
            <button class="player-button" id="mute" type="button" aria-label="Mute or unmute">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M11 5 6 9H2v6h4l5 4Z"/><path d="M15 9a4 4 0 0 1 0 6M18 6a8 8 0 0 1 0 12"/></svg>
            </button>
            <input class="volume" id="volume" type="range" min="0" max="1" value="1" step=".05" aria-label="Volume">
          </div>
          <button class="player-button" id="captions" type="button" aria-label="Subtitles" title="Subtitles" hidden>
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="2.5" y="5" width="19" height="14" rx="2"/><path d="M6.5 10h4M6.5 14h4M13.5 10h4M13.5 14h4"/></svg>
          </button>
          <button class="player-button" id="pip" type="button" aria-label="Picture in picture" title="Picture in picture" hidden>
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="2.5" y="4" width="19" height="16" rx="2"/><rect x="12.5" y="11" width="7" height="5.5" rx="1"/></svg>
          </button>
          <button class="player-button" id="fullscreen" type="button" aria-label="Fullscreen">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M8 3H3v5M16 3h5v5M8 21H3v-5M16 21h5v-5"/></svg>
          </button>
        </div>
      </div>
    </div>
  </section>

  <section class="info no-poster" id="movieInfo">
    <div class="poster" id="posterWrap" hidden><img id="poster" alt="" loading="lazy" decoding="async"></div>
    <div class="meta-main">
      <div class="title-row">
        <div>
          <h2 class="movie-title" id="title">Cinema</h2>
          <div class="movie-meta"><span id="state">—</span> &nbsp;•&nbsp; <span id="runtime">—</span><span id="yearWrap" hidden> &nbsp;•&nbsp; <span id="year"></span></span></div>
        </div>
        <div class="health"><span class="health-dot"></span><span id="healthText">Stream Health: Connecting</span></div>
      </div>
      <p class="synopsis" id="overview" hidden></p>
      <div class="viewer-strip">
        <div class="viewer-cluster" id="viewerAvatars" aria-label="Connected Discord viewers"></div>
        <div class="watchers">👥 <strong id="viewers">0</strong> watching</div>
        <div class="watchers" id="hostPresence">Host status: checking…</div>
      </div>
      <button class="discord-live" id="discordLive" type="button" hidden>
        <svg viewBox="0 0 24 18" aria-hidden="true"><path fill="currentColor" d="M19.8 2.1A16 16 0 0 0 15.8.9l-.5 1a14 14 0 0 0-6.6 0l-.5-1a16 16 0 0 0-4 1.2C1.7 5.8.9 9.4 1.2 13c2.1 1.6 4.1 2.5 6 3.1l1.5-2c-.8-.3-1.6-.7-2.3-1.2l.6-.5c4.4 2 9.2 2 13.6 0l.7.5c-.8.5-1.5.9-2.4 1.2l1.5 2c1.9-.6 3.9-1.5 6-3.1.4-4.1-.7-7.7-3.1-10.9Z"/></svg>
        <span id="discordLiveText">Discord linked</span>
      </button>
      <div class="sync-row"><button id="sync" type="button">Tap to Sync</button><span id="syncHint"></span></div>
    </div>
  </section>

  <div class="quick-tabs" aria-label="Theater actions">
    <button class="tab active" type="button" data-panel="queue">▤ Queue</button>
    <button class="tab" id="sessionTab" type="button" data-panel="viewers">👥 Session</button>
    <button class="tab" type="button" data-panel="chat">💬 Discord</button>
    <button class="tab" id="contextAction" type="button" data-panel="settings">⚙ Details</button>
  </div>

  <div id="notice" role="status" aria-live="polite"></div>
  </div>

  <aside class="theater-sidecar" aria-label="Cinema session tools">
  <section class="queue-panel feed-panel" id="feedPanel" hidden>
    <div class="feed-toolbar">
      <div>
        <h2>Feed Center</h2>
        <div class="feed-toolbar-copy">Server feeds and media sources, grouped by what they discover. Refreshes use Cinema's existing safe source resolver.</div>
      </div>
      <button class="feed-action primary" id="feedAddToggle" type="button">Add Source</button>
    </div>
    <div class="feed-groups" id="feedGroups"></div>
    <form class="feed-form" id="feedForm" hidden>
      <input type="hidden" id="feedSourceId">
      <div class="feed-field">
        <label for="feedLabel">Name</label>
        <input class="feed-input" id="feedLabel" maxlength="80" required placeholder="EZTV">
      </div>
      <div class="feed-field">
        <label for="feedCategory">Category</label>
        <select class="feed-select" id="feedCategory">
          <option value="movies">Movies</option>
          <option value="tv">TV Shows</option>
          <option value="anime">Anime</option>
          <option value="documentaries">Documentaries</option>
          <option value="custom">Custom</option>
        </select>
      </div>
      <div class="feed-field wide">
        <label for="feedUrl">HTTPS source URL</label>
        <input class="feed-input" id="feedUrl" type="url" required placeholder="https://example.com/feed.xml">
      </div>
      <div class="feed-field">
        <label for="feedType">Source type</label>
        <select class="feed-select" id="feedType">
          <option value="feed">RSS / Atom Feed</option>
          <option value="json">Search Provider</option>
          <option value="external">Reference Link</option>
        </select>
      </div>
      <div class="feed-form-actions">
        <button class="feed-action" id="feedCancel" type="button">Cancel</button>
        <button class="feed-action primary" type="submit">Save Source</button>
      </div>
    </form>
  </section>
  <section class="queue-panel" id="sessionPanel" hidden>
    <div class="section-head"><h2>Session</h2><span id="sessionMode">Connecting…</span></div>
    <div class="grid">
      <div class="stat"><b>Viewers</b><span id="sessionViewers">0</span></div>
      <div class="stat"><b>Your role</b><span id="sessionRole">Connecting…</span></div>
      <div class="stat"><b>Sync</b><span id="sessionSync">Checking…</span></div>
    </div>
    <div class="discord-context" id="discordContext" hidden>
      <span class="viewer-avatar" id="discordIdentityAvatar" aria-hidden="true">?</span>
      <div class="discord-context-copy">
        <div class="discord-context-title" id="discordIdentityTitle">Discord linked</div>
        <div class="discord-context-sub" id="discordIdentitySub"></div>
      </div>
    </div>
    <div class="session-viewer-list" id="sessionViewerList"></div>
  </section>

  <section class="queue-panel" id="queuePanel">
    <div class="section-head">
      <h2>Up Next</h2>
      <div class="queue-head-actions">
        <span id="queueCount">0 queued</span>
        <button class="queue-clear" id="clearQueue" type="button" hidden>Clear Queue</button>
      </div>
    </div>
    <div id="queueList">
      <div class="queue-empty">
        <div class="queue-empty-mark" aria-hidden="true">▤</div>
        <div class="queue-empty-copy">
          <div class="queue-empty-title">Your Queue Is Empty</div>
          <div class="queue-empty-sub">Add a title from Discord Cinema and it will appear here for everyone in the session.</div>
        </div>
      </div>
    </div>
  </section>

  <details class="diagnostics">
    <summary>Advanced Stream Details</summary>
    <div class="grid">
      <div class="stat"><b>Torrent</b><span id="progress">0%</span></div>
      <div class="stat"><b>Seeds / Leechers</b><span id="peers">0 / 0</span></div>
      <div class="stat"><b>Buffer target</b><span id="buffer">—</span></div>
    </div>
    <div class="quality-control">
      <label for="qualityMode">Visual quality</label>
      <select class="quality-select" id="qualityMode" aria-label="Visual quality">
        <option value="auto">Auto</option>
        <option value="high">High</option>
        <option value="standard">Standard</option>
        <option value="lite">Lite</option>
      </select>
    </div>
    <div class="quality-note" id="qualityNote">Auto balances artwork depth with device and network capability. Playback features stay identical in every mode.</div>
    <div class="keyboard-help" id="keyboardHelp">
      Desktop shortcuts: <kbd>Space</kbd> play/pause, <kbd>←</kbd>/<kbd>→</kbd> seek 10s when you control playback, <kbd>F</kbd> fullscreen, <kbd>M</kbd> mute.
    </div>
  </details>
  </aside>

  <span class="sr-only" id="heading">Dank Cinema</span>
</main>
</div>

<section class="host-sheet" id="hostSheet" aria-label="Host controls">
  <div class="sheet-handle"></div>
  <div class="sheet-title"><strong>♛ Host Controls</strong><button class="close-sheet" id="closeHostSheet" type="button" aria-label="Close host controls">×</button></div>
  <div class="host-actions">
    <button class="host-action" id="inviteWatchParty" type="button" hidden>➕<br>Invite to Watch Party<small>Pick a Discord member and convert this Private Session without restarting</small></button>
    <button class="host-action" id="passHost" type="button">👤→<br>Pass Host<small>Choose an active Discord viewer</small></button>
    <button class="host-action" id="manageQueue" type="button">☷<br>Manage Queue<small>Remove or reorder queued titles</small></button>
    <button class="host-action" id="pause" type="button">Ⅱ<br><span id="pauseLabel">Pause Playback</span><small id="pauseHelp">Pause this Cinema session</small></button>
    <button class="host-action danger" id="end" type="button">■<br><span id="endLabel">End Session</span><small id="endHelp">Close this Cinema session</small></button>
  </div>
  <button id="play" type="button">Resume</button>
</section>
<button class="host-launcher" id="hostLauncher" type="button" hidden>♛ Host Controls</button>
<div class="invite-modal" id="inviteModal" hidden role="dialog" aria-modal="true" aria-labelledby="inviteTitle">
  <div class="invite-card">
    <div class="invite-head">
      <div>
        <h3 id="inviteTitle">Invite to Watch Party</h3>
        <p>Pick a real Discord member. The current movie, queue, position, host, and stream stay intact while this room becomes a Watch Party.</p>
      </div>
      <button class="invite-close" id="inviteClose" type="button" aria-label="Close invite picker">×</button>
    </div>
    <div class="invite-search-row">
      <input class="invite-search" id="inviteSearch" type="search" autocomplete="off" placeholder="Search Discord members…">
      <button class="invite-search-button" id="inviteSearchButton" type="button">Search</button>
    </div>
    <div class="invite-results" id="inviteResults"></div>
    <div class="invite-status" id="inviteStatus">Search the Discord server member cache or open the native Discord picker.</div>
    <button class="invite-fallback" id="inviteDiscordFallback" type="button">Open Discord Picker</button>
  </div>
</div>
<script>
const BOOT={boot};
const QUALITY_STORAGE_KEY="dank-cinema-quality-v1";

function autoQualityMode() {{
  const reduced=window.matchMedia?.("(prefers-reduced-motion: reduce)")?.matches;
  const connection=navigator.connection||navigator.mozConnection||navigator.webkitConnection;
  const saveData=!!connection?.saveData;
  const network=String(connection?.effectiveType||"").toLowerCase();
  const memory=Number(navigator.deviceMemory||0);
  const cores=Number(navigator.hardwareConcurrency||0);
  if(
    reduced ||
    saveData ||
    network==="slow-2g" ||
    network==="2g" ||
    (memory>0 && memory<=2) ||
    (cores>0 && cores<=2)
  ) return "lite";
  if(
    network==="3g" ||
    (memory>0 && memory<=4) ||
    (cores>0 && cores<=4) ||
    window.innerWidth<430
  ) return "standard";
  return "high";
}}
function applyQualityMode(preference) {{
  const requested=["auto","high","standard","lite"].includes(preference)?preference:"auto";
  const effective=requested==="auto"?autoQualityMode():requested;
  document.documentElement.dataset.quality=effective;
  document.documentElement.dataset.qualityPreference=requested;
  const select=document.getElementById("qualityMode");
  if(select && select.value!==requested) select.value=requested;
  const note=document.getElementById("qualityNote");
  if(note) note.textContent=requested==="auto"
    ?"Auto selected "+effective+". Playback features stay identical in every mode."
    :requested[0].toUpperCase()+requested.slice(1)+" visual mode. Playback features stay identical.";
}}
let storedQuality="auto";
try {{ storedQuality=localStorage.getItem(QUALITY_STORAGE_KEY)||"auto"; }} catch(_) {{}}
applyQualityMode(storedQuality);
let artworkResizeTimer=null;
window.addEventListener("resize",()=>{{
  if(artworkResizeTimer!==null) clearTimeout(artworkResizeTimer);
  artworkResizeTimer=setTimeout(()=>{{
    artworkResizeTimer=null;
    if(lastState?.movie) applyMovieArtwork(lastState.movie,lastState);
  }},180);
}},{{passive:true}});

window.__dankCastApiAvailable=false;
window.__onGCastApiAvailable=function(isAvailable){{
  window.__dankCastApiAvailable=!!isAvailable;
  window.dispatchEvent(new Event("dank-cast-api"));
}};
function loadGoogleCastSdk() {{
  if(document.querySelector('script[data-dank-cast-sdk="1"]')) return;
  const script=document.createElement("script");
  script.async=true;
  script.dataset.dankCastSdk="1";
  script.src="https://www.gstatic.com/cv/js/sender/v1/cast_sender.js?loadCastFramework=1";
  script.onerror=()=>window.dispatchEvent(new Event("dank-cast-api"));
  document.head.appendChild(script);
}}
if(typeof window.requestIdleCallback==="function")
  window.requestIdleCallback(()=>loadGoogleCastSdk(),{{timeout:2200}});
else
  setTimeout(()=>loadGoogleCastSdk(),900);

const video=document.getElementById("video");
const notice=document.getElementById("notice");
const syncButton=document.getElementById("sync");
let lastToken="";
let lastStreamConsumer="";
let remoteApply=false;
let lastState=null;
let terminated=false;
let syncRequested=false;
let syncGestureGranted=false;
let joinTarget=null;
let lastJoinRetargetAt=0;
let lastHardSyncSeekAt=0;
let streamRetryTimer=null;
let streamRetryAttempt=0;
let stateFetchFailures=0;
let attachedStreamUrl="";
let hostSheetDismissed=true;
let previousHostState=null;
let controlsHideTimer=null;
let tapSkipFeedbackTimer=null;
let lastStageTapAt=0;
let lastStageTapSide="";
const SOFT_DRIFT_START=0.35;
const SOFT_DRIFT_STOP=0.12;
const HARD_DRIFT_SECONDS=5.0;
const HARD_SEEK_COOLDOWN_MS=8000;
const JOIN_RETARGET_SECONDS=12.0;
const JOIN_RETARGET_COOLDOWN_MS=10000;

function makeClientSessionId() {{
  try {{
    if(window.crypto && typeof window.crypto.randomUUID==="function")
      return window.crypto.randomUUID();
  }} catch(_) {{}}
  return String(Date.now())+"-"+Math.random().toString(36).slice(2);
}}
function loadClientSessionId() {{
  const key="dank-movie-client:"+BOOT.roomId+":"+BOOT.uid;
  try {{
    const existing=window.sessionStorage.getItem(key);
    if(existing && existing.length>=8 && existing.length<=96) return existing;
    const created=makeClientSessionId();
    window.sessionStorage.setItem(key,created);
    return created;
  }} catch(_) {{
    return makeClientSessionId();
  }}
}}
const CLIENT_SESSION_ID=loadClientSessionId();
const videoStage=document.getElementById("videoStage");
let playerLayoutRaf=0;

function stabilizePlayerLayout() {{
  if(playerLayoutRaf) cancelAnimationFrame(playerLayoutRaf);
  playerLayoutRaf=requestAnimationFrame(()=>{{
    playerLayoutRaf=0;
    const width=Math.max(1,Math.round(videoStage.getBoundingClientRect().width||0));
    if(!document.fullscreenElement && width>0) {{
      videoStage.style.height=(width*9/16)+"px";
      videoStage.style.minHeight="0";
    }} else if(document.fullscreenElement) {{
      videoStage.style.removeProperty("height");
    }}
    // Force Samsung/Chromium to rebuild the composited video layer after
    // desktop-mode, viewport, or orientation changes without reloading media.
    video.style.position="absolute";
    video.style.inset="0";
    video.style.width="100%";
    video.style.height="100%";
  }});
}}
if(typeof ResizeObserver==="function") {{
  const cinemaResizeObserver=new ResizeObserver(()=>stabilizePlayerLayout());
  cinemaResizeObserver.observe(videoStage);
}}
window.addEventListener("resize",stabilizePlayerLayout,{{passive:true}});
window.addEventListener("orientationchange",()=>setTimeout(stabilizePlayerLayout,120),{{passive:true}});
window.visualViewport?.addEventListener("resize",stabilizePlayerLayout,{{passive:true}});
window.addEventListener("pageshow",()=>stabilizePlayerLayout());
document.addEventListener("visibilitychange",()=>{{
  if(!document.hidden) setTimeout(stabilizePlayerLayout,60);
}});
stabilizePlayerLayout();

function clearControlsHideTimer() {{
  if(controlsHideTimer!==null) {{
    clearTimeout(controlsHideTimer);
    controlsHideTimer=null;
  }}
}}
function hidePlayerControls() {{
  clearControlsHideTimer();
  if(!lastState?.stream_url) return;
  videoStage.classList.add("controls-hidden");
}}
function schedulePlayerControlsHide(delayMs=null) {{
  clearControlsHideTimer();
  if(!lastState?.stream_url) return;
  const delay=Number(delayMs??(video.paused?4500:2600));
  controlsHideTimer=setTimeout(()=>hidePlayerControls(),Math.max(800,delay));
}}
function showPlayerControls(autoHide=true) {{
  videoStage.classList.remove("controls-hidden");
  clearControlsHideTimer();
  if(autoHide) schedulePlayerControlsHide();
}}
function stageTargetIsControl(target) {{
  return !!(
    target &&
    typeof target.closest==="function" &&
    target.closest("button,input,.player-chrome,.stage-top")
  );
}}
function showTapSkipFeedback(delta) {{
  const target=document.getElementById(delta<0?"tapSkipLeft":"tapSkipRight");
  if(!target) return;
  document.getElementById("tapSkipLeft").classList.remove("show");
  document.getElementById("tapSkipRight").classList.remove("show");
  target.textContent=delta<0?"↶ 10s":"10s ↷";
  target.classList.add("show");
  if(tapSkipFeedbackTimer!==null) clearTimeout(tapSkipFeedbackTimer);
  tapSkipFeedbackTimer=setTimeout(()=>target.classList.remove("show"),650);
}}
function privateTapSkip(delta) {{
  if(!lastState?.private || !lastState?.is_host || !lastState?.stream_url)
    return false;
  const current=Number(video.currentTime||0);
  const duration=Number.isFinite(video.duration)?Number(video.duration):Infinity;
  const target=Math.max(0,Math.min(duration,current+Number(delta||0)));
  if(!safeSeek(target)) return false;
  showTapSkipFeedback(delta);
  return true;
}}
videoStage.addEventListener("pointermove",event=>{{
  if(event.pointerType==="mouse") showPlayerControls(true);
}});
videoStage.addEventListener("pointerup",event=>{{
  if(stageTargetIsControl(event.target)) {{
    showPlayerControls(true);
    return;
  }}

  const rect=videoStage.getBoundingClientRect();
  const ratio=rect.width>0?(Number(event.clientX||0)-rect.left)/rect.width:.5;
  const side=ratio<.38?"left":ratio>.62?"right":"center";
  const now=Date.now();
  const doubleTap=(
    (side==="left"||side==="right") &&
    lastStageTapSide===side &&
    now-lastStageTapAt<=340
  );

  if(doubleTap && lastState?.private && lastState?.is_host) {{
    lastStageTapAt=0;
    lastStageTapSide="";
    showPlayerControls(false);
    privateTapSkip(side==="left"?-10:10);
    schedulePlayerControlsHide(1500);
    return;
  }}

  lastStageTapAt=now;
  lastStageTapSide=side;
  if(videoStage.classList.contains("controls-hidden"))
    showPlayerControls(true);
  else
    hidePlayerControls();
}});
videoStage.addEventListener("pointerdown",event=>{{
  if(stageTargetIsControl(event.target)) {{
    showPlayerControls(false);
    clearControlsHideTimer();
  }}
}});
videoStage.addEventListener("focusin",()=>showPlayerControls(false));
videoStage.addEventListener("focusout",()=>schedulePlayerControlsHide());

function api(path) {{
  const clean=String(path||"");
  return clean+(clean.includes("?")?"&":"?")+BOOT.query;
}}
async function jsonFetch(path, options={{}}) {{
  const response=await fetch(api(path), {{
    cache:"no-store",
    headers:{{"Content-Type":"application/json"}},
    ...options
  }});
  if(!response.ok) throw new Error(await response.text());
  return response.json();
}}
function bufferedEnd() {{
  const current=Number(video.currentTime||0);
  const ranges=video.buffered;
  if(!ranges || !ranges.length) return current;

  // Torrent-backed media often has disjoint buffered ranges (for example
  // metadata/tail probes plus the active playback corridor). Only the range
  // containing the current playhead is safe to report as playable-ahead.
  const epsilon=0.25;
  for(let i=0;i<ranges.length;i++) {{
    const start=Number(ranges.start(i));
    const end=Number(ranges.end(i));
    if(current+epsilon>=start && current<=end+epsilon)
      return Math.max(current,end);
  }}
  return current;
}}
function fmtRate(n) {{
  if(!n) return "0 B/s";
  const u=["B/s","KiB/s","MiB/s","GiB/s"]; let x=n,i=0;
  while(x>=1024&&i<u.length-1){{x/=1024;i++;}}
  return x.toFixed(i?1:0)+" "+u[i];
}}
function fmtClock(seconds) {{
  const total=Math.max(0,Math.floor(Number(seconds)||0));
  const h=Math.floor(total/3600);
  const m=Math.floor((total%3600)/60);
  const s=String(total%60).padStart(2,"0");
  return h?String(h)+":"+String(m).padStart(2,"0")+":"+s:String(m)+":"+s;
}}
function refreshNativePlayerCapabilities() {{
  const pip=document.getElementById("pip");
  pip.hidden=!Boolean(document.pictureInPictureEnabled && typeof video.requestPictureInPicture==="function");
  pip.classList.toggle("active",document.pictureInPictureElement===video);

  const captions=document.getElementById("captions");
  const tracks=video.textTracks;
  captions.hidden=!(tracks && tracks.length);
  let showing=false;
  if(tracks) {{
    for(let i=0;i<tracks.length;i++) if(tracks[i].mode==="showing") showing=true;
  }}
  captions.classList.toggle("active",showing);
}}
function updatePlayerChrome() {{
  const duration=Number.isFinite(video.duration)?video.duration:0;
  const current=Number(video.currentTime||0);
  document.getElementById("currentTime").textContent=fmtClock(current);
  document.getElementById("duration").textContent=duration?fmtClock(duration):"0:00";
  document.getElementById("runtime").textContent=duration?fmtClock(duration):"—";
  const timeline=document.getElementById("timeline");
  const ratio=duration>0?Math.max(0,Math.min(1,current/duration)):0;
  timeline.value=String(Math.round(ratio*1000));
  timeline.style.setProperty("--progress",(ratio*100).toFixed(2)+"%");
  const paused=video.paused;
  const icon=paused?'<path d="M8 5v14l11-7Z"/>':'<path d="M7 5h4v14H7ZM14 5h4v14h-4Z"/>';
  document.getElementById("centerPlayIcon").innerHTML=icon;
  document.getElementById("playerToggleIcon").innerHTML=icon;
  const action=paused?"Play":"Pause";
  document.getElementById("centerPlay").setAttribute("aria-label",action);
  document.getElementById("playerToggle").setAttribute("aria-label",action);
}}
function renderQueue(items) {{
  const list=document.getElementById("queueList");
  list.textContent="";
  const rows=Array.isArray(items)?items:[];
  document.getElementById("queueCount").textContent=rows.length+" queued";
  const clear=document.getElementById("clearQueue");
  clear.hidden=!(lastState?.is_host && rows.length);
  if(!rows.length) {{
    const empty=document.createElement("div");
    empty.className="queue-empty";

    const mark=document.createElement("div");
    mark.className="queue-empty-mark";
    mark.setAttribute("aria-hidden","true");
    mark.textContent="▤";

    const copy=document.createElement("div");
    copy.className="queue-empty-copy";
    const title=document.createElement("div");
    title.className="queue-empty-title";
    title.textContent="Your Queue Is Empty";
    const sub=document.createElement("div");
    sub.className="queue-empty-sub";
    sub.textContent="Add a title from Discord Cinema and it will appear here for everyone in the session.";
    copy.append(title,sub);
    empty.append(mark,copy);

    if(lastState?.discord_url) {{
      const action=document.createElement("button");
      action.type="button";
      action.className="queue-empty-action";
      action.textContent="Open Discord";
      action.onclick=openDiscordRoom;
      empty.appendChild(action);
    }}
    list.appendChild(empty);
    return;
  }}
  for(const item of rows) {{
    const row=document.createElement("div");
    row.className="queue-item";
    const art=document.createElement("div");
    art.className="queue-art";
    if(String(item.poster_url||"").startsWith("https://image.tmdb.org/")) {{
      const img=document.createElement("img");
      img.src=item.poster_url;
      img.alt="";
      art.appendChild(img);
    }}
    const copy=document.createElement("div");
    const title=document.createElement("div");
    title.className="queue-title";
    title.textContent=String(item.title||"Untitled");
    const sub=document.createElement("div");
    sub.className="queue-sub";
    sub.textContent=(item.year?String(item.year)+" • ":"")+(item.is_current?"Now playing":"Up next");
    copy.append(title,sub);
    row.append(art,copy);

    if(lastState?.is_host) {{
      row.classList.add("manageable");
      const actions=document.createElement("div");
      actions.className="queue-actions";
      const index=rows.indexOf(item);
      for(const [label,actionName,disabled,danger] of [
        ["↑","move_up",index===0,false],
        ["↓","move_down",index===rows.length-1,false],
        ["×","remove",false,true]
      ]) {{
        const button=document.createElement("button");
        button.type="button";
        button.className="queue-action"+(danger?" danger":"");
        button.textContent=label;
        button.disabled=disabled;
        button.setAttribute("aria-label",actionName.replace("_"," ")+" "+String(item.title||"title"));
        button.onclick=()=>queueAction(actionName,String(item.candidate_id||""));
        actions.appendChild(button);
      }}
      row.appendChild(actions);
    }}
    list.appendChild(row);
  }}
}}
function viewerInitials(name) {{
  const parts=String(name||"").trim().split(/\s+/).filter(Boolean);
  if(!parts.length) return "?";
  return (parts[0][0]+(parts.length>1?parts[parts.length-1][0]:"")).slice(0,2).toUpperCase();
}}
function buildViewerAvatar(viewer, compact=false) {{
  const avatar=document.createElement("span");
  avatar.className="viewer-avatar"+(viewer?.is_host?" host":"");
  avatar.title=String(viewer?.display_name||viewer?.user_id||"Discord viewer");
  const url=String(viewer?.avatar_url||"");
  if(url.startsWith("https://cdn.discordapp.com/")||url.startsWith("https://media.discordapp.net/")) {{
    const img=document.createElement("img");
    img.src=url;
    img.alt="";
    img.loading="lazy";
    avatar.appendChild(img);
  }} else {{
    avatar.textContent=viewerInitials(viewer?.display_name||viewer?.user_id);
  }}
  if(compact) avatar.setAttribute("aria-hidden","true");
  return avatar;
}}
function renderDiscordViewers(s) {{
  const rows=Array.isArray(s.viewers)?s.viewers:[];
  const cluster=document.getElementById("viewerAvatars");
  const list=document.getElementById("sessionViewerList");
  cluster.textContent="";
  list.textContent="";

  for(const viewer of rows.slice(0,5))
    cluster.appendChild(buildViewerAvatar(viewer,true));
  if(rows.length>5) {{
    const more=document.createElement("span");
    more.className="viewer-avatar";
    more.textContent="+"+String(rows.length-5);
    cluster.appendChild(more);
  }}

  for(const viewer of rows) {{
    const row=document.createElement("div");
    row.className="session-viewer";
    row.appendChild(buildViewerAvatar(viewer));

    const copy=document.createElement("div");
    copy.className="session-viewer-copy";
    const name=document.createElement("div");
    name.className="session-viewer-name";
    name.textContent=String(viewer.display_name||viewer.user_id||"Discord viewer");
    const role=document.createElement("div");
    role.className="session-viewer-role";
    role.textContent=viewer.is_host
      ?(s.private?"Private host":"Watch Party host")
      :(s.private?"Invited viewer":"Viewer");
    copy.append(name,role);
    row.appendChild(copy);

    if(s.is_host && !viewer.is_host) {{
      const action=document.createElement("button");
      action.type="button";
      action.className="session-viewer-action";
      action.textContent="Pass Host";
      action.onclick=()=>transferHost(Number(viewer.user_id||0),String(viewer.display_name||"viewer"));
      row.appendChild(action);
    }}
    list.appendChild(row);
  }}
}}
function renderDiscordContext(s) {{
  const ctx=s.discord||{{}};
  const panel=document.getElementById("discordContext");
  const title=document.getElementById("discordIdentityTitle");
  const sub=document.getElementById("discordIdentitySub");
  const avatar=document.getElementById("discordIdentityAvatar");
  const live=document.getElementById("discordLive");
  const liveText=document.getElementById("discordLiveText");

  panel.hidden=!ctx.connected;
  live.hidden=!ctx.connected;
  avatar.textContent="";
  if(!ctx.connected) {{
    liveText.textContent="Discord unavailable";
    return;
  }}

  const url=String(ctx.avatar_url||"");
  if(url.startsWith("https://cdn.discordapp.com/")||url.startsWith("https://media.discordapp.net/")) {{
    const img=document.createElement("img");
    img.src=url;
    img.alt="";
    img.loading="lazy";
    avatar.appendChild(img);
  }} else {{
    avatar.textContent=viewerInitials(ctx.user_name||ctx.user_id);
  }}

  title.textContent="Discord linked as "+String(ctx.user_name||ctx.user_id||"viewer");
  const guild=String(ctx.guild_name||"Discord server");
  const channel=String(ctx.channel_name||"");
  sub.textContent=channel?guild+" • #"+channel:guild;

  liveText.textContent=channel
    ?"Discord • "+guild+" • #"+channel
    :"Discord • "+guild;
}}
function applyModeSurface(s) {{
  const privateMode=String(s.mode||"")==="private" || !!s.private;
  document.body.dataset.cinemaMode=privateMode?"private":"watch-party";

  document.getElementById("roomMode").textContent=privateMode?"Private Room":"Watch Party";
  document.getElementById("endLabel").textContent=privateMode?"End Private Session":"End Movie Night";
  document.getElementById("endHelp").textContent=privateMode
    ?"Close this private session"
    :"Close the Watch Party for everyone";
  document.getElementById("sessionMode").textContent=privateMode?"Private Session":"Watch Party";
  document.getElementById("sessionTab").textContent=privateMode?"👥 Private":"👥 Viewers";

  const pauseLabel=document.getElementById("pauseLabel");
  const pauseHelp=document.getElementById("pauseHelp");
  if(privateMode) {{
    pauseLabel.textContent=Number(s.viewer_count||0)>1?"Pause Private Room":"Pause";
    pauseHelp.textContent=Number(s.viewer_count||0)>1
      ?"Pause playback for invited viewers"
      :"Pause your private playback";
  }} else {{
    pauseLabel.textContent="Pause for Everyone";
    pauseHelp.textContent="Pause synchronized playback for all viewers";
  }}

  const hostSheet=document.getElementById("hostSheet");
  const launcher=document.getElementById("hostLauncher");
  const contextAction=document.getElementById("contextAction");
  const inviteWatchParty=document.getElementById("inviteWatchParty");
  inviteWatchParty.hidden=!(s.is_host && privateMode);
  if(!privateMode) document.getElementById("inviteModal").hidden=true;

  if(s.is_host && previousHostState===false)
    hostSheetDismissed=false;

  if(s.is_host) {{
    hostSheet.classList.toggle("show",!hostSheetDismissed);
    launcher.hidden=!hostSheetDismissed;
    contextAction.dataset.panel="host";
    contextAction.textContent="♛ Host";
    contextAction.title="Open Host Controls";
  }} else {{
    hostSheet.classList.remove("show");
    launcher.hidden=true;
    contextAction.dataset.panel="settings";
    contextAction.textContent="⚙ Details";
    contextAction.title="Advanced Stream Details";
  }}

  document.getElementById("syncHint").textContent=s.is_host
    ?(privateMode
      ?"You control this private session."
      :"You control synchronized Watch Party playback.")
    :"";

  previousHostState=!!s.is_host;
}}
function streamHealthLabel(s) {{
  if(s.media_missing) return "Source unavailable";
  if(!s.stream_url) return "Waiting for source";
  if(s.state==="buffering") return "Preparing stream";
  const t=s.torrent||{{}};
  const seeds=Number(t.seeds||0);
  const rate=Number(t.download_rate||0);
  if(seeds>=5 || rate>=512*1024) return "Excellent";
  if(seeds>0 || rate>0) return "Good";
  return "Connected";
}}
function tmdbVariant(url,size) {{
  const clean=String(url||"");
  if(!clean.startsWith("https://image.tmdb.org/")) return "";
  return clean.replace(/\/t\/p\/(?:original|w\d+)\//,"/t/p/"+size+"/");
}}
function preferredBackdrop(url) {{
  const width=Math.max(window.innerWidth||0,videoStage?.clientWidth||0);
  const quality=document.documentElement.dataset.quality||"standard";
  if(quality==="lite" || width<720) return tmdbVariant(url,"w780")||url;
  if(quality==="standard" || width<1200) return tmdbVariant(url,"w1280")||url;
  return tmdbVariant(url,"original")||url;
}}
function applyMovieArtwork(movie,s) {{
  const poster=document.getElementById("poster");
  const posterWrap=document.getElementById("posterWrap");
  const stage=document.getElementById("videoStage");
  const sourceBackdrop=String(movie.backdrop_url||movie.poster_url||"");
  const backdrop=preferredBackdrop(sourceBackdrop);
  if(backdrop.startsWith("https://image.tmdb.org/")) {{
    stage.style.setProperty("--backdrop-image",'url("'+backdrop.replace(/"/g,"%22")+'")');
    if(video.poster!==backdrop) video.poster=backdrop;
  }} else {{
    stage.style.removeProperty("--backdrop-image");
    video.removeAttribute("poster");
  }}

  const posterUrl=String(movie.poster_url||"");
  if(posterUrl.startsWith("https://image.tmdb.org/")) {{
    const w185=tmdbVariant(posterUrl,"w185")||posterUrl;
    const w342=tmdbVariant(posterUrl,"w342")||posterUrl;
    const w500=tmdbVariant(posterUrl,"w500")||posterUrl;
    poster.src=w342;
    poster.srcset=w185+" 185w, "+w342+" 342w, "+w500+" 500w";
    poster.sizes="(max-width:640px) 78px, (max-width:1079px) 108px, 120px";
    poster.alt=(movie.title||s.title||"Movie")+" poster";
    posterWrap.hidden=false;
    document.getElementById("movieInfo").classList.remove("no-poster");
  }} else {{
    poster.removeAttribute("src");
    poster.removeAttribute("srcset");
    poster.removeAttribute("sizes");
    poster.alt="";
    posterWrap.hidden=true;
    document.getElementById("movieInfo").classList.add("no-poster");
  }}
}}
function renderSiteState(s) {{
  const movie=s.movie||{{}};
  applyModeSurface(s);
  document.getElementById("healthText").textContent="Stream Health: "+streamHealthLabel(s);
  document.getElementById("hostPresence").textContent=s.host_active?"Host online":"Host away";
  document.getElementById("sessionViewers").textContent=String(s.viewer_count||0);
  document.getElementById("sessionRole").textContent=s.is_host?"Host":"Viewer";
  document.getElementById("sessionSync").textContent=
    s.is_host?"Host clock":(s.sync_status==="joining"?"Joining":(s.sync_ready?"Synced":"Waiting"));
  const year=document.getElementById("year");
  const yearWrap=document.getElementById("yearWrap");
  year.textContent=movie.year?String(movie.year):"";
  yearWrap.hidden=!movie.year;
  const overview=document.getElementById("overview");
  overview.textContent=movie.overview||"";
  overview.hidden=!movie.overview;
  applyMovieArtwork(movie,s);
  renderQueue(s.queue||[]);
  renderDiscordContext(s);
  renderDiscordViewers(s);
  const hostOnly=!s.is_host;
  document.getElementById("rewind10").disabled=hostOnly;
  document.getElementById("forward10").disabled=hostOnly;
  document.getElementById("timeline").disabled=hostOnly;
  updatePlayerChrome();
  refreshCastAvailability();
  syncCastToRoom(s);
}}
function resetPlaybackRate() {{
  try {{
    if(Math.abs(Number(video.playbackRate||1)-1)>0.001) video.playbackRate=1;
  }} catch(_) {{}}
}}


function cancelStreamRetry() {{
  if(streamRetryTimer!==null) {{
    clearTimeout(streamRetryTimer);
    streamRetryTimer=null;
  }}
}}

function attachStream(url, force=false) {{
  const clean=String(url||"");
  if(!clean) return;
  if(!force && attachedStreamUrl===clean && video.getAttribute("src")) return;
  attachedStreamUrl=clean;
  resetPlaybackRate();
  video.src=clean;
  video.load();
}}

function scheduleStreamRetry() {{
  if(
    terminated ||
    streamRetryTimer!==null ||
    !lastState?.stream_url
  ) return;

  const step=Math.min(streamRetryAttempt,4);
  const delay=Math.min(15000,2500*Math.pow(1.6,step));
  streamRetryAttempt+=1;
  notice.textContent=
    "The source is still preparing. Keeping your Cinema session and retrying in "+
    Math.ceil(delay/1000)+"s…";

  streamRetryTimer=setTimeout(async()=>{{
    streamRetryTimer=null;
    if(terminated || !lastState?.stream_url) return;

    try {{
      const fresh=await jsonFetch("/movie/"+BOOT.roomId+"/state");
      await applyState(fresh);
    }} catch(_) {{}}

    if(terminated || !lastState?.stream_url) return;
    attachStream(lastState.stream_url,true);
  }},delay);
}}

function safeSeek(target) {{
  if(!Number.isFinite(target) || target<0) return false;
  try {{
    video.currentTime=target;
    return true;
  }} catch(_) {{
    return false;
  }}
}}

function correctSyncedDrift(target) {{
  if(!Number.isFinite(target)) return;
  const signed=(video.currentTime||0)-target;
  const drift=Math.abs(signed);
  if(drift>=HARD_DRIFT_SECONDS) {{
    const now=Date.now();
    if(now-lastHardSyncSeekAt>=HARD_SEEK_COOLDOWN_MS) {{
      resetPlaybackRate();
      if(safeSeek(target)) lastHardSyncSeekAt=now;
    }} else {{
      video.playbackRate=signed<0?1.04:0.96;
    }}
    return;
  }}
  if(drift>=SOFT_DRIFT_START) {{
    video.playbackRate=signed<0?1.04:0.96;
    return;
  }}
  if(drift<=SOFT_DRIFT_STOP) resetPlaybackRate();
}}

async function applyState(s) {{
  lastState=s;
  document.getElementById("title").textContent=s.title||(s.private?"Private Session":"Watch Party");
  document.getElementById("heading").textContent=
    s.private?"🔒 Dank Cinema Private Session":"🎬 Dank Cinema Watch Party";
  document.getElementById("state").textContent=s.state||"—";
  document.getElementById("viewers").textContent=String(s.viewer_count||0);
  document.getElementById("role").textContent=
    s.is_host?"Hosted by You":(s.sync_status==="joining"?"Joining…":"Synced Viewer");
  renderSiteState(s);
  const t=s.torrent||{{}};
  document.getElementById("progress").textContent=((t.progress||0)*100).toFixed(1)+"% • "+fmtRate(t.download_rate||0);
  const swarmSource=String(t.swarm_source||"");
  document.getElementById("peers").textContent=
    String(t.seeds||0)+" / "+String(t.leechers||0)+(swarmSource?" • "+swarmSource:"");
  const b=t.buffer||{{}};
  document.getElementById("buffer").textContent=b.target_seconds?Number(b.target_seconds).toFixed(0)+"s":"adaptive";

  document.getElementById("play").disabled=!s.is_host;
  document.getElementById("pause").disabled=!s.is_host;
  document.getElementById("end").disabled=!s.is_host;
  document.getElementById("passHost").disabled=!(
    s.is_host &&
    Array.isArray(s.viewers) &&
    s.viewers.some(viewer=>!viewer.is_host)
  );
  syncButton.hidden=!!s.is_host;
  syncButton.disabled=!!s.is_host;
  if(!s.is_host && s.sync_status==="joining") syncButton.textContent=syncRequested?"Syncing…":"Tap to Sync";
  else if(!s.is_host) syncButton.textContent="Synced";

  if(s.ended) {{
    terminated=true;
    cancelStreamRetry();
    resetPlaybackRate();
    video.pause();
    video.removeAttribute("src");
    video.load();
    document.getElementById("play").disabled=true;
    document.getElementById("pause").disabled=true;
    document.getElementById("end").disabled=true;
    syncButton.disabled=true;
    notice.textContent=s.private?"Private Session has ended.":"Watch Party has ended.";
    document.getElementById("hostSheet").classList.remove("show");
    return;
  }}

  const streamConsumer=String(s.stream_consumer||"");
  if(
    s.stream_token &&
    s.stream_url &&
    (
      s.stream_token!==lastToken ||
      streamConsumer!==lastStreamConsumer
    )
  ) {{
    lastToken=s.stream_token;
    lastStreamConsumer=streamConsumer;
    if(!s.is_host) {{
      syncRequested=false;
      syncGestureGranted=false;
      joinTarget=null;
      lastJoinRetargetAt=0;
      lastHardSyncSeekAt=0;
    }}
    streamRetryAttempt=0;
    cancelStreamRetry();
    attachStream(s.stream_url);
  }}
  if(!s.stream_url) {{
    if(s.media_missing) {{
      notice.textContent=s.private
        ?"The attached media session expired or was reclaimed. Your Private Session is still active; return to Discord and choose the release again."
        :"The attached media session expired or was reclaimed. The Watch Party is still active; return to Discord and choose the release again.";
    }} else {{
      notice.textContent="Waiting for the host to choose media.";
    }}
    return;
  }}

  if(!s.is_host && s.sync_requested) syncRequested=true;

  const target=Number(s.position_seconds||0);
  remoteApply=true;
  try {{
    if(s.is_host) {{
      resetPlaybackRate();
      if((s.state==="paused" || s.state==="buffering") && !video.paused) video.pause();
      if(s.state==="playing" && video.paused) {{
        try {{ await video.play(); }} catch(_) {{}}
      }}
    }} else if(s.sync_status==="joining") {{
      resetPlaybackRate();
      if(!syncRequested) {{
        if(!video.paused) video.pause();
        notice.textContent="Tap to Sync once to join playback with sound.";
      }} else {{
        if(joinTarget===null) {{
          const stable=Number(s.sync_target_position);
          joinTarget=Number.isFinite(stable)?stable:target;
        }}

        const liveDrift=Math.abs((video.currentTime||0)-target);
        const now=Date.now();
        if(
          syncGestureGranted &&
          Number.isFinite(target) &&
          liveDrift>=JOIN_RETARGET_SECONDS &&
          now-lastJoinRetargetAt>=JOIN_RETARGET_COOLDOWN_MS
        ) {{
          safeSeek(target);
          joinTarget=target;
          lastJoinRetargetAt=now;
        }}

        if(s.state==="paused" || s.state==="buffering") {{
          if(!video.paused) video.pause();
        }} else if(s.state==="playing" && video.paused && syncGestureGranted) {{
          try {{ await video.play(); }}
          catch(_) {{
            notice.textContent="Playback is still blocked. Tap Sync again or use the video Play control once.";
          }}
        }}

        if(!notice.textContent || notice.textContent.startsWith("Joining ")) {{
          const joiningLabel=s.private?"Private Session":"Watch Party";
          notice.textContent=
            "Joining "+joiningLabel+"… buffering around "+Math.floor((joinTarget||0)/60)+":"+
            String(Math.floor((joinTarget||0)%60)).padStart(2,"0")+
            ". Playback will stay put while the buffer catches up.";
        }}
      }}
    }} else {{
      joinTarget=null;
      if(s.state==="paused" || s.state==="buffering") {{
        resetPlaybackRate();
        if(!video.paused) video.pause();
        const pausedDrift=Math.abs((video.currentTime||0)-target);
        if(pausedDrift>0.75) safeSeek(target);
        if(s.state==="buffering") notice.textContent="Buffering the group for smoother playback…";
      }} else if(s.state==="playing") {{
        if(video.paused) {{
          if(syncGestureGranted) {{
            try {{ await video.play(); notice.textContent=""; }}
            catch(_) {{
              notice.textContent="Tap Sync again or use the video Play control once to restore sound.";
            }}
          }} else {{
            notice.textContent="Tap Sync once to allow synchronized playback with sound.";
          }}
        }}
        if(!video.paused) correctSyncedDrift(target);
      }}
    }}
  }} finally {{
    setTimeout(()=>{{remoteApply=false;}},150);
  }}

  if(s.open_vote && s.sync_status!=="joining") {{
    notice.textContent="Vote open: "+s.open_vote.action+" • "+s.open_vote.yes+"/"+s.open_vote.required_yes+" yes. Open /movie in Discord to vote.";
  }}
}}
async function poll() {{
  if(terminated) return;
  try {{
    const state=await jsonFetch("/movie/"+BOOT.roomId+"/state");
    stateFetchFailures=0;
    if(notice.textContent.startsWith("Sync connection lost"))
      notice.textContent="";
    await applyState(state);
  }}
  catch(err) {{
    const message=String(err.message||err);
    if(message.includes("Dank Cinema session not found")) {{
      terminated=true;
      video.pause();
      notice.textContent=lastState?.private?"Private Session has ended.":"Cinema session has ended.";
      return;
    }}
    stateFetchFailures+=1;
    if(stateFetchFailures>=3)
      notice.textContent="Sync connection lost. Reconnecting…";
  }}
}}
async function heartbeat(forceSync=false) {{
  if(terminated) return null;
  try {{
    return await jsonFetch("/movie/"+BOOT.roomId+"/heartbeat", {{
      method:"POST",
      body:JSON.stringify({{
        position_seconds:video.currentTime||0,
        duration_seconds:Number.isFinite(video.duration)?video.duration:0,
        buffered_until_seconds:bufferedEnd(),
        paused:video.paused,
        client_session_id:CLIENT_SESSION_ID,
        sync_requested:!!(syncRequested||forceSync)
      }})
    }});
  }} catch(_) {{
    return null;
  }}
}}
async function queueAction(action, candidateId="") {{
  if(!lastState?.is_host) return;
  if(action==="clear" && !confirm("Clear every queued title?")) return;
  try {{
    const state=await jsonFetch("/movie/"+BOOT.roomId+"/queue", {{
      method:"POST",
      body:JSON.stringify({{action,candidate_id:candidateId}})
    }});
    await applyState(state);
  }} catch(err) {{
    notice.textContent="Queue update failed: "+String(err?.message||err);
  }}
}}
async function hostAction(action, extra={{}}) {{
  // Explicit user controls must never be dropped just because a state poll is
  // currently applying remote media state. Media event listeners themselves
  // already use remoteApply to suppress feedback loops.
  if(!lastState || !lastState.is_host) return;
  try {{
    await applyState(await jsonFetch("/movie/"+BOOT.roomId+"/action", {{
      method:"POST",
      body:JSON.stringify({{action,...extra}})
    }}));
  }} catch(err) {{ notice.textContent="Control error: "+String(err.message||err); }}
}}
syncButton.onclick=async()=>{{
  if(!lastState || lastState.is_host || !lastState.stream_url) return;

  syncRequested=true;
  syncGestureGranted=true;
  resetPlaybackRate();

  const requestedTarget=Number(
    lastState.sync_target_position??lastState.position_seconds??0
  );
  joinTarget=Number.isFinite(requestedTarget)?requestedTarget:0;
  lastJoinRetargetAt=Date.now();

  let playbackBlocked=false;
  remoteApply=true;
  try {{
    if(Math.abs((video.currentTime||0)-joinTarget)>0.35)
      safeSeek(joinTarget);

    if(lastState.state==="playing") {{
      try {{
        await video.play();
      }} catch(_) {{
        playbackBlocked=true;
        notice.textContent="Your browser blocked playback. Tap the video Play control once, then Tap to Sync again.";
      }}
    }} else {{
      // Prime audible playback inside the real user gesture so a later host Play
      // is not rejected by mobile autoplay policy.
      try {{
        await video.play();
        video.pause();
        if(Math.abs((video.currentTime||0)-joinTarget)>0.5)
          safeSeek(joinTarget);
      }} catch(_) {{}}
    }}
  }} finally {{
    setTimeout(()=>{{remoteApply=false;}},150);
  }}

  if(!playbackBlocked)
    notice.textContent="Sync requested… building your buffer.";
  const state=await heartbeat(true);
  if(state) await applyState(state);
}};
document.getElementById("play").onclick=()=>hostAction("resume");
document.getElementById("pause").onclick=()=>hostAction("pause");
async function togglePlayerPlayback() {{
  if(!lastState?.stream_url) return;
  showPlayerControls(true);

  if(lastState.is_host) {{
    const shouldResume=video.paused || lastState.state!=="playing";
    await hostAction(shouldResume?"resume":"pause");
    return;
  }}

  if(video.paused) {{
    syncRequested=true;
    syncGestureGranted=true;
    try {{
      await video.play();
      await heartbeat(true);
      notice.textContent=lastState.private
        ?"Private playback resumed and resynced."
        :"Playback resumed and resynced to the Watch Party.";
    }} catch(err) {{
      notice.textContent="Playback could not start: "+String(err?.message||err);
    }}
  }} else {{
    syncRequested=false;
    syncGestureGranted=false;
    video.pause();
    notice.textContent=lastState.private
      ?"Paused locally. Tap Play to rejoin the private session."
      :"Paused locally. Tap Play to rejoin synchronized playback.";
  }}
}}
document.getElementById("centerPlay").onclick=togglePlayerPlayback;
document.getElementById("playerToggle").onclick=togglePlayerPlayback;
document.getElementById("rewind10").onclick=()=>{{
  if(lastState?.is_host) safeSeek(Math.max(0,(video.currentTime||0)-10));
}};
document.getElementById("forward10").onclick=()=>{{
  if(lastState?.is_host) safeSeek(Math.min(Number.isFinite(video.duration)?video.duration:Infinity,(video.currentTime||0)+10));
}};
document.getElementById("timeline").addEventListener("input",event=>{{
  if(!lastState?.is_host || !Number.isFinite(video.duration) || video.duration<=0) return;
  safeSeek((Number(event.target.value||0)/1000)*video.duration);
}});
document.getElementById("volume").addEventListener("input",event=>{{
  video.volume=Math.max(0,Math.min(1,Number(event.target.value||1)));
  video.muted=video.volume===0;
}});
document.getElementById("mute").onclick=()=>{{ video.muted=!video.muted; }};
document.getElementById("pip").onclick=async()=>{{
  try {{
    if(document.pictureInPictureElement===video) await document.exitPictureInPicture();
    else if(document.pictureInPictureEnabled && typeof video.requestPictureInPicture==="function")
      await video.requestPictureInPicture();
  }} catch(err) {{
    notice.textContent="Picture-in-picture is unavailable: "+String(err?.message||err);
  }}
  refreshNativePlayerCapabilities();
}};
document.getElementById("captions").onclick=()=>{{
  const tracks=video.textTracks;
  if(!tracks || !tracks.length) return;
  let anyShowing=false;
  for(let i=0;i<tracks.length;i++) if(tracks[i].mode==="showing") anyShowing=true;
  for(let i=0;i<tracks.length;i++) tracks[i].mode=(i===0 && !anyShowing)?"showing":"disabled";
  refreshNativePlayerCapabilities();
}};
video.addEventListener("enterpictureinpicture",refreshNativePlayerCapabilities);
video.addEventListener("leavepictureinpicture",refreshNativePlayerCapabilities);
video.addEventListener("loadedmetadata",refreshNativePlayerCapabilities);
video.addEventListener("contextmenu",event=>event.preventDefault());
for(const eventName of ["loadedmetadata","loadeddata","canplay","playing","emptied"]) {{
  video.addEventListener(eventName,()=>stabilizePlayerLayout());
}}
async function enterTheaterFullscreen() {{
  const target=document.getElementById("videoStage");
  try {{
    if(document.fullscreenElement) {{
      await document.exitFullscreen();
      return;
    }}
    if(target?.requestFullscreen) {{
      await target.requestFullscreen({{navigationUI:"hide"}});
      if(screen.orientation && typeof screen.orientation.lock==="function") {{
        try {{ await screen.orientation.lock("landscape"); }}
        catch(_) {{
          notice.textContent="Fullscreen is active. This browser blocked automatic landscape rotation.";
        }}
      }}
      return;
    }}
    if(typeof video.webkitEnterFullscreen==="function") {{
      video.webkitEnterFullscreen();
      return;
    }}
    notice.textContent="Fullscreen is not supported by this browser.";
  }} catch(err) {{
    notice.textContent="Fullscreen could not start: "+String(err?.message||err);
  }}
}}
document.getElementById("fullscreen").onclick=enterTheaterFullscreen;
document.addEventListener("fullscreenchange",()=>{{
  setTimeout(stabilizePlayerLayout,40);
  if(!document.fullscreenElement && screen.orientation && typeof screen.orientation.unlock==="function") {{
    try {{ screen.orientation.unlock(); }} catch(_) {{}}
  }}
}});
function openHostControls() {{
  if(!lastState?.is_host) return;
  hostSheetDismissed=false;
  document.getElementById("hostSheet").classList.add("show");
  document.getElementById("hostLauncher").hidden=true;
}}
function closeHostControls() {{
  hostSheetDismissed=true;
  document.getElementById("hostSheet").classList.remove("show");
  document.getElementById("hostLauncher").hidden=!lastState?.is_host;
}}
document.getElementById("closeHostSheet").onclick=closeHostControls;
document.getElementById("hostLauncher").onclick=openHostControls;

const inviteModal=document.getElementById("inviteModal");
const inviteResults=document.getElementById("inviteResults");
const inviteStatus=document.getElementById("inviteStatus");
const inviteSearch=document.getElementById("inviteSearch");
let inviteTrigger=null;

function closeInviteModal() {{
  inviteModal.hidden=true;
  inviteResults.textContent="";
  inviteStatus.textContent="Search the Discord server member cache or open the native Discord picker.";
  if(inviteTrigger && typeof inviteTrigger.focus==="function") {{
    try {{ inviteTrigger.focus(); }} catch(_) {{}}
  }}
  inviteTrigger=null;
}}
function inviteAvatar(row) {{
  const avatar=document.createElement("span");
  avatar.className="viewer-avatar";
  const url=String(row?.avatar_url||"");
  if(url.startsWith("https://cdn.discordapp.com/")||url.startsWith("https://media.discordapp.net/")) {{
    const img=document.createElement("img");
    img.src=url;
    img.alt="";
    img.loading="lazy";
    avatar.appendChild(img);
  }} else {{
    avatar.textContent=viewerInitials(row?.display_name||row?.user_id);
  }}
  return avatar;
}}
async function searchInviteMembers() {{
  if(!lastState?.is_host || !lastState?.private) return;
  inviteResults.textContent="";
  inviteStatus.textContent="Searching Discord members…";
  try {{
    const query=encodeURIComponent(String(inviteSearch.value||"").trim());
    const data=await jsonFetch("/movie/"+BOOT.roomId+"/invite-options?q="+query);
    const rows=Array.isArray(data.members)?data.members:[];
    inviteStatus.textContent=rows.length
      ?"Choose who to invite. This immediately turns the live Private Session into a Watch Party."
      :"No cached Discord member matched. Use the native Discord picker below.";
    for(const row of rows) {{
      const button=document.createElement("button");
      button.type="button";
      button.className="invite-result";
      button.appendChild(inviteAvatar(row));
      const copy=document.createElement("div");
      copy.className="invite-result-copy";
      const name=document.createElement("div");
      name.className="invite-result-name";
      name.textContent=String(row.display_name||row.user_id||"Discord member");
      const user=document.createElement("div");
      user.className="invite-result-user";
      user.textContent=row.username?("@"+String(row.username)):"Discord member";
      copy.append(name,user);
      button.appendChild(copy);
      button.onclick=()=>promoteAndInvite(Number(row.user_id||0),String(row.display_name||"viewer"));
      inviteResults.appendChild(button);
    }}
  }} catch(err) {{
    inviteStatus.textContent="Discord member search failed: "+String(err?.message||err);
  }}
}}
async function promoteAndInvite(userId,displayName) {{
  if(!lastState?.is_host || !lastState?.private || !Number(userId)) return;
  if(!confirm("Invite "+displayName+" and turn this Private Session into a Watch Party?")) return;
  inviteStatus.textContent="Starting Watch Party without restarting the movie…";
  try {{
    const state=await jsonFetch("/movie/"+BOOT.roomId+"/promote", {{
      method:"POST",
      body:JSON.stringify({{user_id:Number(userId)}})
    }});
    const invite=state.invite||{{}};
    await applyState(state);
    inviteModal.hidden=true;
    if(invite.dm_sent) {{
      notice.textContent="Watch Party started. Discord invite sent to "+displayName+".";
    }} else if(invite.watch_url) {{
      let copied=false;
      try {{
        await navigator.clipboard.writeText(String(invite.watch_url));
        copied=true;
      }} catch(_) {{}}
      notice.textContent=copied
        ?"Watch Party started. Their signed invite link was copied because Discord DM was unavailable."
        :"Watch Party started. Discord DM was unavailable; use Discord to send the invite.";
    }} else {{
      notice.textContent="Watch Party started. Open Discord to invite "+displayName+".";
    }}
  }} catch(err) {{
    inviteStatus.textContent="Invite failed: "+String(err?.message||err);
  }}
}}
document.getElementById("inviteWatchParty").onclick=event=>{{
  if(!lastState?.is_host || !lastState?.private) return;
  inviteTrigger=event.currentTarget;
  closeHostControls();
  inviteModal.hidden=false;
  inviteSearch.value="";
  searchInviteMembers();
  setTimeout(()=>inviteSearch.focus(),30);
}};
document.getElementById("inviteClose").onclick=closeInviteModal;
document.getElementById("inviteSearchButton").onclick=searchInviteMembers;
inviteSearch.addEventListener("keydown",event=>{{
  if(event.key==="Enter") {{
    event.preventDefault();
    searchInviteMembers();
  }}
}});
document.getElementById("inviteDiscordFallback").onclick=()=>{{
  openDiscordRoom();
}};
inviteModal.addEventListener("click",event=>{{
  if(event.target===inviteModal) closeInviteModal();
}});
inviteModal.addEventListener("keydown",event=>{{
  if(event.key==="Escape") {{
    event.preventDefault();
    closeInviteModal();
  }}
}});

async function transferHost(newHostId, displayName="viewer") {{
  if(!lastState?.is_host || !Number(newHostId)) return;
  const target=String(displayName||"viewer");
  if(!confirm("Pass host control to "+target+"?")) return;
  try {{
    const state=await jsonFetch("/movie/"+BOOT.roomId+"/host", {{
      method:"POST",
      body:JSON.stringify({{new_host_id:Number(newHostId)}})
    }});
    await applyState(state);
    notice.textContent="Host control passed to "+target+".";
  }} catch(err) {{
    notice.textContent="Pass Host failed: "+String(err?.message||err);
  }}
}}
function openDiscordRoom() {{
  const url=String(lastState?.discord_url||"");
  if(!url) {{
    notice.textContent="Discord room link is unavailable for this session.";
    return;
  }}
  window.location.href=url;
}}
document.getElementById("passHost").onclick=()=>{{
  const panel=document.getElementById("sessionPanel");
  panel.hidden=false;
  panel.scrollIntoView({{behavior:"smooth",block:"nearest"}});
  notice.textContent="Choose an active viewer below to pass host control.";
}};
document.getElementById("discordLive").onclick=openDiscordRoom;
document.getElementById("manageQueue").onclick=()=>{{
  document.getElementById("queuePanel").scrollIntoView({{behavior:"smooth",block:"nearest"}});
  notice.textContent="Queue manager is active. Use ↑ ↓ or × on queued titles.";
}};
document.getElementById("clearQueue").onclick=()=>queueAction("clear");

const qualitySelect=document.getElementById("qualityMode");
qualitySelect.addEventListener("change",()=>{{
  const value=String(qualitySelect.value||"auto");
  try {{ localStorage.setItem(QUALITY_STORAGE_KEY,value); }} catch(_) {{}}
  applyQualityMode(value);
  if(lastState?.movie) applyMovieArtwork(lastState.movie,lastState);
}});
window.addEventListener("resize",()=>{{
  if(document.documentElement.dataset.qualityPreference==="auto") applyQualityMode("auto");
}},{{passive:true}});

document.addEventListener("keydown",event=>{{
  if(event.defaultPrevented || event.altKey || event.ctrlKey || event.metaKey) return;
  const target=event.target;
  if(target && /INPUT|TEXTAREA|SELECT|BUTTON/.test(String(target.tagName||""))) return;
  const key=String(event.key||"").toLowerCase();
  if(key===" "){{
    event.preventDefault();
    togglePlayerPlayback();
  }} else if(key==="f"){{
    event.preventDefault();
    enterTheaterFullscreen();
  }} else if(key==="m"){{
    event.preventDefault();
    video.muted=!video.muted;
  }} else if((key==="arrowleft"||key==="arrowright") && lastState?.is_host){{
    event.preventDefault();
    const delta=key==="arrowleft"?-10:10;
    safeSeek(Math.max(0,Math.min(Number.isFinite(video.duration)?video.duration:Infinity,(video.currentTime||0)+delta)));
  }}
}});

const FEED_CATEGORY_LABELS={{
  movies:"Movies",
  tv:"TV Shows",
  anime:"Anime",
  documentaries:"Documentaries",
  custom:"Custom"
}};
let feedCenterLoaded=false;
let feedCenterState=null;

function feedTime(epoch) {{
  const value=Number(epoch||0);
  if(!value) return "Not refreshed this process";
  try {{ return new Date(value*1000).toLocaleString(); }}
  catch(_) {{ return "Refreshed"; }}
}}
function feedTypeLabel(type) {{
  if(type==="feed") return "RSS / Atom";
  if(type==="json") return "Search Provider";
  if(type==="external") return "Reference Link";
  return "Source";
}}
function resetFeedForm() {{
  document.getElementById("feedSourceId").value="";
  document.getElementById("feedLabel").value="";
  document.getElementById("feedUrl").value="";
  document.getElementById("feedType").value="feed";
  document.getElementById("feedCategory").value="custom";
}}
function openFeedForm(source=null) {{
  const form=document.getElementById("feedForm");
  form.hidden=false;
  if(source) {{
    document.getElementById("feedSourceId").value=String(source.source_id||"");
    document.getElementById("feedLabel").value=String(source.label||"");
    document.getElementById("feedUrl").value=String(source.endpoint_url||"");
    document.getElementById("feedType").value=String(source.provider_type||"feed");
    document.getElementById("feedCategory").value=String(source.category||"custom");
  }} else {{
    resetFeedForm();
  }}
  document.getElementById("feedLabel").focus();
}}
function renderFeedCenter(state) {{
  feedCenterState=state||{{sources:[]}};
  const root=document.getElementById("feedGroups");
  root.textContent="";
  const sources=Array.isArray(feedCenterState.sources)?feedCenterState.sources:[];
  const host=!!feedCenterState.is_host;
  document.getElementById("feedAddToggle").hidden=!host;
  document.getElementById("feedForm").hidden=true;

  if(!sources.length) {{
    const empty=document.createElement("div");
    empty.className="feed-empty";
    empty.textContent=host
      ?"No media sources yet. Add a feed or provider when you have one. Empty decorative sections stay out of the rest of Cinema."
      :"No enabled media feeds are available for this server.";
    root.appendChild(empty);
    return;
  }}

  for(const category of ["movies","tv","anime","documentaries","custom"]) {{
    const rows=sources.filter(item=>String(item.category||"custom")===category);
    if(!rows.length) continue;
    const group=document.createElement("section");
    group.className="feed-group";
    const heading=document.createElement("div");
    heading.className="feed-group-title";
    const title=document.createElement("span");
    title.textContent=FEED_CATEGORY_LABELS[category]||"Custom";
    const count=document.createElement("span");
    count.textContent=String(rows.length);
    heading.append(title,count);
    group.appendChild(heading);

    for(const source of rows) {{
      const card=document.createElement("article");
      card.className="feed-card";
      const top=document.createElement("div");
      top.className="feed-card-top";
      const name=document.createElement("div");
      name.className="feed-card-name";
      name.textContent=String(source.label||source.source_id||"Media source");
      const stateBadge=document.createElement("span");
      stateBadge.className="feed-badge "+(source.enabled?"good":"off");
      stateBadge.textContent=source.enabled?"Enabled":"Disabled";
      top.append(name,stateBadge);
      card.appendChild(top);

      const badges=document.createElement("div");
      badges.className="feed-badges";
      for(const text of [
        feedTypeLabel(String(source.provider_type||"")),
        source.discovery_capable?"Discovery":"",
        source.search_capable?"Search":"",
        source.playback_capable?"Playback":""
      ].filter(Boolean)) {{
        const badge=document.createElement("span");
        badge.className="feed-badge";
        badge.textContent=text;
        badges.appendChild(badge);
      }}
      card.appendChild(badges);

      const meta=document.createElement("div");
      meta.className="feed-meta";
      let refresh="Last refresh: "+feedTime(source.last_refresh_at);
      if(source.last_refresh_ok===false && source.last_refresh_error)
        refresh+=" • "+String(source.last_refresh_error);
      else if(source.last_refresh_ok===true)
        refresh+=" • reachable";
      meta.textContent=refresh;
      card.appendChild(meta);

      const discovered=Array.isArray(source.newly_discovered)?source.newly_discovered:[];
      if(discovered.length) {{
        const chips=document.createElement("div");
        chips.className="feed-discovered";
        for(const item of discovered) {{
          const chip=document.createElement("span");
          chip.className="feed-title-chip";
          chip.textContent=String(item);
          chips.appendChild(chip);
        }}
        card.appendChild(chips);
      }}

      if(host) {{
        const actions=document.createElement("div");
        actions.className="feed-actions";
        if(source.provider_type!=="external") {{
          const refresh=document.createElement("button");
          refresh.type="button"; refresh.className="feed-action primary"; refresh.textContent="Refresh";
          refresh.onclick=()=>feedAction("refresh",source);
          actions.appendChild(refresh);
        }}
        const edit=document.createElement("button");
        edit.type="button"; edit.className="feed-action"; edit.textContent="Edit";
        edit.onclick=()=>openFeedForm(source);
        const toggle=document.createElement("button");
        toggle.type="button"; toggle.className="feed-action";
        toggle.textContent=source.enabled?"Disable":"Enable";
        toggle.onclick=()=>feedAction("toggle",source);
        const remove=document.createElement("button");
        remove.type="button"; remove.className="feed-action danger"; remove.textContent="Delete";
        remove.onclick=()=>feedAction("remove",source);
        actions.append(edit,toggle,remove);
        card.appendChild(actions);
      }}
      group.appendChild(card);
    }}
    root.appendChild(group);
  }}
}}
async function loadFeedCenter(force=false) {{
  if(feedCenterLoaded && !force && feedCenterState) {{
    renderFeedCenter(feedCenterState);
    return;
  }}
  const root=document.getElementById("feedGroups");
  root.textContent="";
  const loading=document.createElement("div");
  loading.className="feed-empty"; loading.textContent="Loading Cinema sources…";
  root.appendChild(loading);
  try {{
    const state=await jsonFetch("/movie/"+BOOT.roomId+"/sources");
    feedCenterLoaded=true;
    renderFeedCenter(state);
  }} catch(err) {{
    root.textContent="";
    const error=document.createElement("div");
    error.className="feed-empty";
    error.textContent="Feed Center could not load. "+String(err?.message||err);
    root.appendChild(error);
  }}
}}
async function feedAction(action,source) {{
  if(!feedCenterState?.is_host) return;
  if(action==="remove" && !confirm("Delete "+String(source.label||"this media source")+"?")) return;
  try {{
    const state=await jsonFetch("/movie/"+BOOT.roomId+"/sources",{{
      method:"POST",
      body:JSON.stringify({{action,source_id:String(source.source_id||"")}})
    }});
    feedCenterLoaded=true;
    renderFeedCenter(state);
  }} catch(err) {{
    notice.textContent="Feed Center update failed: "+String(err?.message||err);
  }}
}}
document.getElementById("feedAddToggle").onclick=()=>openFeedForm();
document.getElementById("feedCancel").onclick=()=>{{
  document.getElementById("feedForm").hidden=true;
  resetFeedForm();
}};
document.getElementById("feedForm").addEventListener("submit",async event=>{{
  event.preventDefault();
  if(!feedCenterState?.is_host) return;
  const payload={{
    action:"save",
    source_id:String(document.getElementById("feedSourceId").value||""),
    label:String(document.getElementById("feedLabel").value||""),
    endpoint_url:String(document.getElementById("feedUrl").value||""),
    provider_type:String(document.getElementById("feedType").value||"feed"),
    category:String(document.getElementById("feedCategory").value||"custom")
  }};
  try {{
    const state=await jsonFetch("/movie/"+BOOT.roomId+"/sources",{{
      method:"POST",body:JSON.stringify(payload)
    }});
    feedCenterLoaded=true;
    resetFeedForm();
    renderFeedCenter(state);
  }} catch(err) {{
    notice.textContent="Could not save media source: "+String(err?.message||err);
  }}
}});

for(const item of document.querySelectorAll("[data-nav]")) {{
  item.addEventListener("click",()=>{{
    document.querySelectorAll("[data-nav]").forEach(x=>x.classList.toggle("active",x===item));
    if(item.dataset.nav==="theater") document.querySelector(".theater").scrollIntoView({{behavior:"smooth",block:"start"}});
    else if(item.dataset.nav==="queue") document.getElementById("queuePanel").scrollIntoView({{behavior:"smooth",block:"start"}});
    else if(item.dataset.nav==="details") {{
      const details=document.querySelector(".diagnostics");
      details.open=true;
      details.scrollIntoView({{behavior:"smooth",block:"start"}});
    }} else if(item.dataset.nav==="feeds") {{
      const panel=document.getElementById("feedPanel");
      panel.hidden=false;
      loadFeedCenter();
      panel.scrollIntoView({{behavior:"smooth",block:"start"}});
    }} else if(item.dataset.nav==="discord") openDiscordRoom();
  }});
}}
for(const tab of document.querySelectorAll("[data-panel]")) {{
  tab.addEventListener("click",()=>{{
    document.querySelectorAll("[data-panel]").forEach(x=>x.classList.toggle("active",x===tab));
    if(tab.dataset.panel==="queue") document.getElementById("queuePanel").scrollIntoView({{behavior:"smooth",block:"nearest"}});
    else if(tab.dataset.panel==="viewers") {{
      const panel=document.getElementById("sessionPanel");
      panel.hidden=false;
      panel.scrollIntoView({{behavior:"smooth",block:"nearest"}});
    }} else if(tab.dataset.panel==="settings") {{
      const details=document.querySelector(".diagnostics");
      details.open=true;
      details.scrollIntoView({{behavior:"smooth",block:"nearest"}});
    }} else if(tab.dataset.panel==="host") {{
      openHostControls();
    }} else if(tab.dataset.panel==="chat") openDiscordRoom();
  }});
}}

const castButton=document.getElementById("cast");
let castContext=null;
let castState="NO_DEVICES_AVAILABLE";
let castActive=false;
let castWasMuted=false;
let castLastSyncAt=0;
let remotePlaybackAvailable=false;

function googleCastDeviceAvailable() {{
  const noDevices=window.cast?.framework
    ?cast.framework.CastState.NO_DEVICES_AVAILABLE
    :"NO_DEVICES_AVAILABLE";
  return !!(
    castContext &&
    typeof castContext.requestSession==="function" &&
    castState &&
    castState!==noDevices
  );
}}
function castAvailability() {{
  if(!lastState?.stream_url)
    return {{show:false,available:false,transport:"",reason:"No media is loaded yet."}};

  if(googleCastDeviceAvailable() && lastState?.cast_supported_media)
    return {{show:true,available:true,transport:"google",reason:"Chromecast available"}};

  if(remotePlaybackAvailable && video.remote && typeof video.remote.prompt==="function")
    return {{show:true,available:true,transport:"remote",reason:"Remote playback device available"}};

  if(googleCastDeviceAvailable() && !lastState?.cast_supported_media)
    return {{
      show:true,
      available:false,
      transport:"",
      reason:"A Cast device is available, but this release cannot be sent directly. Try an MP4/WebM release."
    }};

  return {{
    show:true,
    available:false,
    transport:"",
    reason:"No compatible casting device is currently available in this browser."
  }};
}}
function refreshCastAvailability() {{
  const status=castAvailability();
  castButton.hidden=!status.show;
  castButton.disabled=false;
  castButton.classList.toggle("unavailable",status.show&&!status.available);
  castButton.setAttribute("aria-label",status.available?"Cast":"Cast unavailable");
  castButton.title=status.reason;
  castButton.dataset.transport=status.transport||"";
}}
function initGoogleCast() {{
  try {{
    if(!window.__dankCastApiAvailable || !window.cast?.framework || !window.chrome?.cast?.media) {{
      castContext=null;
      castState="NO_DEVICES_AVAILABLE";
      refreshCastAvailability();
      return false;
    }}
    castContext=cast.framework.CastContext.getInstance();
    castContext.setOptions({{
      receiverApplicationId:chrome.cast.media.DEFAULT_MEDIA_RECEIVER_APP_ID,
      autoJoinPolicy:chrome.cast.AutoJoinPolicy.ORIGIN_SCOPED
    }});
    castState=castContext.getCastState();
    castContext.addEventListener(
      cast.framework.CastContextEventType.CAST_STATE_CHANGED,
      event=>{{
        castState=event.castState;
        refreshCastAvailability();
      }}
    );
    castContext.addEventListener(
      cast.framework.CastContextEventType.SESSION_STATE_CHANGED,
      event=>{{
        const state=String(event.sessionState||"");
        const connected=state.includes("STARTED")||state.includes("RESUMED");
        if(connected) {{
          castActive=true;
          castButton.classList.add("connected");
        }} else if(state.includes("ENDED") || state.includes("FAILED")) {{
          castActive=false;
          castButton.classList.remove("connected");
          video.muted=castWasMuted;
        }}
        refreshCastAvailability();
      }}
    );
    refreshCastAvailability();
    return true;
  }} catch(_) {{
    castContext=null;
    castState="NO_DEVICES_AVAILABLE";
    refreshCastAvailability();
    return false;
  }}
}}
function castLoadCurrentMedia() {{
  if(!castContext || !lastState?.cast_stream_url)
    return Promise.reject(new Error("No playable Cinema stream is ready."));
  if(!lastState?.cast_supported_media)
    return Promise.reject(new Error("This release cannot be played directly by Chromecast."));
  const session=castContext.getCurrentSession();
  if(!session) return Promise.reject(new Error("No Cast device is connected."));
  const mediaInfo=new chrome.cast.media.MediaInfo(
    lastState.cast_stream_url,
    lastState.media_content_type||"video/mp4"
  );
  const metadata=new chrome.cast.media.GenericMediaMetadata();
  metadata.title=String(lastState.movie?.title||lastState.title||"Dank Cinema");
  metadata.subtitle="The 420 Lobby";
  const artwork=String(lastState.movie?.backdrop_url||lastState.movie?.poster_url||"");
  if(artwork.startsWith("https://image.tmdb.org/"))
    metadata.images=[new chrome.cast.Image(artwork)];
  mediaInfo.metadata=metadata;
  const request=new chrome.cast.media.LoadRequest(mediaInfo);
  request.currentTime=Math.max(0,Number(video.currentTime||lastState.position_seconds||0));
  request.autoplay=!video.paused;
  return session.loadMedia(request);
}}
async function startGoogleCast() {{
  if(!initGoogleCast() || !googleCastDeviceAvailable())
    throw new Error("No Chromecast device is currently available in this browser.");
  if(!lastState?.cast_supported_media)
    throw new Error("This release cannot be sent directly to Chromecast. Choose an MP4/WebM release.");
  await castContext.requestSession();
  await castLoadCurrentMedia();
  castWasMuted=video.muted;
  video.muted=true;
  castActive=true;
  castButton.classList.add("connected");
  notice.textContent="Casting "+String(lastState?.movie?.title||lastState?.title||"Dank Cinema")+".";
}}
function syncCastToRoom(s) {{
  if(!castActive || !castContext) return;
  const session=castContext.getCurrentSession();
  const media=session?.getMediaSession?.();
  if(!media) return;
  const now=Date.now();
  if(now-castLastSyncAt<1800) return;
  castLastSyncAt=now;
  try {{
    const target=Number(s.position_seconds||0);
    const remotePosition=Number(media.currentTime||0);
    if(Number.isFinite(target) && Math.abs(remotePosition-target)>3) {{
      const seek=new chrome.cast.media.SeekRequest();
      seek.currentTime=target;
      media.seek(seek,()=>{{}},()=>{{}});
    }}
    const state=String(media.playerState||"");
    if((s.state==="paused"||s.state==="buffering") && state==="PLAYING")
      media.pause(null,()=>{{}},()=>{{}});
    else if(s.state==="playing" && state==="PAUSED")
      media.play(null,()=>{{}},()=>{{}});
  }} catch(_) {{}}
}}
if(video.remote && typeof video.remote.watchAvailability==="function") {{
  try {{
    video.remote.watchAvailability(available=>{{
      remotePlaybackAvailable=!!available;
      refreshCastAvailability();
    }}).catch(()=>{{
      remotePlaybackAvailable=false;
      refreshCastAvailability();
    }});
    video.remote.addEventListener("connect",()=>{{
      castButton.classList.add("connected");
      notice.textContent="Remote playback connected.";
    }});
    video.remote.addEventListener("disconnect",()=>{{
      castButton.classList.remove("connected");
      refreshCastAvailability();
    }});
  }} catch(_) {{
    remotePlaybackAvailable=false;
  }}
}}
castButton.hidden=true;
window.addEventListener("dank-cast-api",()=>initGoogleCast());
setTimeout(()=>initGoogleCast(),1200);
castButton.onclick=async()=>{{
  const status=castAvailability();
  if(!status.available) {{
    notice.textContent=status.reason;
    return;
  }}
  try {{
    if(status.transport==="google") {{
      await startGoogleCast();
      return;
    }}
    if(status.transport==="remote" && video.remote && typeof video.remote.prompt==="function") {{
      await video.remote.prompt();
      return;
    }}
  }} catch(err) {{
    notice.textContent=String(err?.message||"Casting could not start.");
  }}
}};
document.getElementById("end").onclick=()=>{{
  if(confirm((lastState&&lastState.private)?"End this Private Session and release its media?":"End this Movie Night for everyone and release the room media session?"))
    hostAction("end");
}};
video.addEventListener("play",()=>{{
  schedulePlayerControlsHide(2200);
  if(remoteApply) return;
  if(lastState?.is_host) {{
    hostAction("resume");
    return;
  }}
  if(lastState?.stream_url) {{
    syncGestureGranted=true;
    syncRequested=true;
    if(joinTarget===null) {{
      const target=Number(lastState.sync_target_position??lastState.position_seconds??0);
      joinTarget=Number.isFinite(target)?target:(video.currentTime||0);
    }}
    if(Math.abs((video.currentTime||0)-joinTarget)>0.35)
      safeSeek(joinTarget);
    lastJoinRetargetAt=Date.now();
    heartbeat(true);
  }}
}});
video.addEventListener("pause",()=>{{
  showPlayerControls(true);
  if(!remoteApply && lastState?.is_host) hostAction("pause");
}});
video.addEventListener("seeked",()=>{{ if(!remoteApply && lastState?.is_host) hostAction("seek",{{seconds:video.currentTime||0}}); }});
video.addEventListener("loadedmetadata",()=>{{
  streamRetryAttempt=0;
  cancelStreamRetry();
  updatePlayerChrome();
}});
video.addEventListener("durationchange",updatePlayerChrome);
video.addEventListener("timeupdate",updatePlayerChrome);
video.addEventListener("play",updatePlayerChrome);
video.addEventListener("pause",updatePlayerChrome);
video.addEventListener("canplay",()=>{{
  streamRetryAttempt=0;
  cancelStreamRetry();
  if(notice.textContent.startsWith("The source is still preparing"))
    notice.textContent="";
}});
video.addEventListener("waiting",()=>{{
  if(lastState?.stream_url && !terminated)
    notice.textContent="Preparing the stream… keeping playback stable.";
}});
video.addEventListener("stalled",()=>{{
  if(lastState?.stream_url && !terminated)
    notice.textContent="The stream paused briefly… waiting for enough data to continue smoothly.";
}});
video.addEventListener("error",()=>{{
  if(lastState?.stream_url) scheduleStreamRetry();
}});
heartbeat(false).then(state=>{{ if(state) applyState(state); else poll(); }});
setInterval(poll,2000);
setInterval(()=>heartbeat(false),3000);
</script>
</body>
</html>"""


async def movie_night_watch(request: web.Request) -> web.Response:
    room, uid = await _room_and_user(request)
    query = urlencode(
        {
            "uid": str(request.query.get("uid", "") or ""),
            "exp": str(request.query.get("exp", "") or ""),
            "sig": str(request.query.get("sig", "") or ""),
        }
    )
    return web.Response(
        text=_watch_html(room.room_id, uid, query),
        content_type="text/html",
        headers={
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "no-referrer",
            "Content-Security-Policy": (
                "default-src 'self'; "
                "script-src 'unsafe-inline' https://www.gstatic.com; "
                "style-src 'unsafe-inline' https://fonts.googleapis.com; "
                "font-src https://fonts.gstatic.com; "
                "media-src 'self'; "
                "connect-src 'self' https://www.gstatic.com https://*.googleapis.com; "
                "img-src 'self' https://image.tmdb.org https://cdn.discordapp.com https://media.discordapp.net; object-src 'none'; frame-ancestors 'none'; base-uri 'none'"
            ),
        },
    )


def register_movie_night_public_routes(app: web.Application) -> None:
    ensure_movie_night_cleanup_task()
    app.router.add_get(
        "/movie/assets/dank-cinema-brand.webp",
        dank_cinema_brand_asset,
    )
    app.router.add_get(
        "/movie/assets/dank-cinema-brand-{variant}.webp",
        dank_cinema_brand_asset,
    )
    app.router.add_get("/movie/{room_id}/watch", movie_night_watch)
    app.router.add_get("/movie/{room_id}/state", movie_night_state)
    app.router.add_post("/movie/{room_id}/heartbeat", movie_night_heartbeat)
    app.router.add_post("/movie/{room_id}/action", movie_night_action)
    app.router.add_post("/movie/{room_id}/host", movie_night_transfer_host)
    app.router.add_get("/movie/{room_id}/invite-options", movie_night_invite_options)
    app.router.add_post("/movie/{room_id}/promote", movie_night_promote_watch_party)
    app.router.add_post("/movie/{room_id}/queue", movie_night_queue_action)
    app.router.add_get("/movie/{room_id}/sources", movie_night_sources)
    app.router.add_post("/movie/{room_id}/sources", movie_night_source_action)


__all__ = [
    "dank_cinema_brand_asset",
    "movie_night_invite_options",
    "movie_night_promote_watch_party",
    "movie_night_queue_action",
    "movie_night_source_action",
    "movie_night_sources",
    "movie_night_transfer_host",
    "movie_night_watch_url",
    "register_movie_night_public_routes",
]
