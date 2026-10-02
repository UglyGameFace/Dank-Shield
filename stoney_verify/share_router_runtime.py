from __future__ import annotations

"""Production Share Router runtime.

The router gives a guild private, non-age-restricted proxy channels that remain
usable from mobile share sheets, then forwards the human's post into a configured
destination the same human is normally allowed to view and post in.

This module owns runtime behavior only. It has no import-time bot mutation and
adds no slash commands. Public configuration UI lives in
commands_ext.public_share_router.
"""

import asyncio
import json
import os
import re
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional
from urllib.parse import urljoin, urlsplit, urlunsplit

import aiohttp
import discord

from stoney_verify.share_router_resources import (
    DEFAULT_SHARE_CHANNELS,
    SHARE_ROUTER_CATEGORY_NAME,
    is_share_router_category_name,
    is_share_router_design_resource,
    share_source_key,
)

_DATA_LOCK = asyncio.Lock()
_RECENT_ROUTE_KEYS: dict[tuple[int, int, str], float] = {}
URL_RE = re.compile(r"https?://[^\s<>()]+", re.IGNORECASE)
_X_STATUS_PATH_RE = re.compile(r"^/([^/]+)/status/(\d+)(?:/.*)?$", re.IGNORECASE)
_VIDEO_EXTENSIONS = (".mp4", ".webm", ".mov")
_VIDEO_CONTENT_TYPES = {
    "video/mp4": ".mp4",
    "video/webm": ".webm",
    "video/quicktime": ".mov",
}
_DEFAULT_VIDEO_MAX_BYTES = 25 * 1024 * 1024
_DEFAULT_VIDEO_TIMEOUT_SECONDS = 12.0
_TRUSTED_VIDEO_HOSTS = {
    "video.twimg.com",
    "media.tenor.com",
    "i.giphy.com",
}
_X_EXTRACT_CONCURRENCY = max(
    1,
    min(int(os.getenv("DANK_SHARE_ROUTER_X_EXTRACT_CONCURRENCY", "2") or "2"), 4),
)
_X_EXTRACT_SEMAPHORE = asyncio.Semaphore(_X_EXTRACT_CONCURRENCY)
_X_VIDEO_CACHE: dict[str, tuple[float, Optional[str]]] = {}
_X_VIDEO_CACHE_TTL_SECONDS = 15 * 60.0
_X_VIDEO_NEGATIVE_CACHE_TTL_SECONDS = 90.0

# Keep the historical path so a deployed guild does not have to rebuild routes
# merely because ownership moved out of startup_guards.
ROUTES_FILE = Path(
    os.getenv(
        "DANK_SHARE_ROUTES_FILE",
        str(Path(os.getenv("DANK_DATA_DIR", "data")) / "share_routes.json"),
    )
)


@dataclass(frozen=True)
class RouteHealth:
    blockers: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.blockers


@dataclass(frozen=True)
class HubRepairResult:
    category_id: int
    created: tuple[str, ...] = ()
    repaired: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class RoutedVideo:
    file: discord.File
    source_url: str
    size_bytes: int


def _log(message: str) -> None:
    try:
        print(f"🔗 share_router {message}")
    except Exception:
        pass


def _safe_str(value: Any, default: str = "") -> str:
    try:
        text = str(value or "").strip()
        return text if text else default
    except Exception:
        return default


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or isinstance(value, bool):
            return int(default)
        text = str(value).strip().strip("<#@!&>")
        return int(text) if text else int(default)
    except Exception:
        return int(default)


def _x_status_parts(value: str) -> Optional[tuple[str, str]]:
    raw = _safe_str(value)
    if not raw:
        return None
    try:
        parsed = urlsplit(raw)
    except Exception:
        return None
    host = str(parsed.hostname or "").lower().strip(".")
    if host.startswith("www."):
        host = host[4:]
    if host == "mobile.twitter.com":
        host = "twitter.com"
    if host not in {"x.com", "twitter.com"}:
        return None
    match = _X_STATUS_PATH_RE.match(str(parsed.path or ""))
    if not match:
        return None
    handle, status_id = match.groups()
    return handle, status_id


def _canonical_share_url(value: str) -> str:
    raw = _safe_str(value).rstrip(".,)")
    if not raw:
        return ""
    status = _x_status_parts(raw)
    if status is not None:
        handle, status_id = status
        return f"https://x.com/{handle}/status/{status_id}"
    try:
        parsed = urlsplit(raw)
    except Exception:
        return raw
    if str(parsed.scheme or "").lower() not in {"http", "https"}:
        return raw
    host = str(parsed.hostname or "").lower()
    if not host:
        return raw
    netloc = host
    try:
        port = parsed.port
    except ValueError:
        return raw
    if port:
        netloc = f"{host}:{port}"
    return urlunsplit(
        (
            str(parsed.scheme or "").lower(),
            netloc,
            str(parsed.path or ""),
            str(parsed.query or ""),
            "",
        )
    )


def _url_identity(value: str) -> str:
    status = _x_status_parts(value)
    if status is not None:
        return f"x-status:{status[1]}"
    return _canonical_share_url(value).lower()


def _normalize_share_text_urls(text: str, seen_urls: set[str]) -> str:
    raw_text = _safe_str(text)
    if not raw_text:
        return ""

    chunks: list[str] = []
    cursor = 0
    for match in URL_RE.finditer(raw_text):
        chunks.append(raw_text[cursor : match.start()])
        original = match.group(0)
        canonical = _canonical_share_url(original)
        identity = _url_identity(canonical)
        if identity and identity not in seen_urls:
            seen_urls.add(identity)
            chunks.append(canonical)
        cursor = match.end()
    chunks.append(raw_text[cursor:])

    cleaned = "".join(chunks)
    cleaned = re.sub(r"[ \t]+\n", "\n", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def _suppress_url_previews(text: str) -> str:
    def replace(match: re.Match[str]) -> str:
        canonical = _canonical_share_url(match.group(0))
        return f"<{canonical}>" if canonical else match.group(0)

    return URL_RE.sub(replace, text or "")


def _trusted_video_url(value: str) -> bool:
    raw = _safe_str(value)
    if not raw:
        return False
    try:
        parsed = urlsplit(raw)
    except Exception:
        return False
    if str(parsed.scheme or "").lower() != "https":
        return False
    host = str(parsed.hostname or "").lower().strip(".")
    if not host:
        return False
    if host in _TRUSTED_VIDEO_HOSTS:
        return True
    if host.endswith(".discordapp.net") or host.endswith(".discordapp.com"):
        return True
    if host.endswith(".giphy.com"):
        return True
    return False


def _video_source_urls(message: discord.Message) -> list[str]:
    urls: list[str] = []
    seen: set[str] = set()

    def add(value: Any) -> None:
        url = _safe_str(value)
        if not url or not _trusted_video_url(url):
            return
        key = _canonical_share_url(url)
        if key and key not in seen:
            seen.add(key)
            urls.append(url)

    try:
        for attachment in list(getattr(message, "attachments", []) or []):
            content_type = _safe_str(getattr(attachment, "content_type", "")).lower()
            filename = _safe_str(getattr(attachment, "filename", "")).lower()
            if content_type.startswith("video/") or filename.endswith(_VIDEO_EXTENSIONS):
                add(getattr(attachment, "proxy_url", None))
                add(getattr(attachment, "url", None))
    except Exception:
        pass

    try:
        for embed in list(getattr(message, "embeds", []) or []):
            video = getattr(embed, "video", None)
            if video is None:
                continue
            # Prefer Discord's proxy when available; it avoids re-fetching the
            # provider directly and keeps the relay on an explicitly trusted host.
            add(getattr(video, "proxy_url", None))
            add(getattr(video, "url", None))
    except Exception:
        pass

    return urls


def _share_video_limit_bytes(guild: discord.Guild) -> int:
    try:
        configured = int(
            str(
                os.getenv(
                    "DANK_SHARE_ROUTER_MAX_VIDEO_BYTES",
                    str(_DEFAULT_VIDEO_MAX_BYTES),
                )
                or _DEFAULT_VIDEO_MAX_BYTES
            ).strip()
        )
    except Exception:
        configured = _DEFAULT_VIDEO_MAX_BYTES
    configured = max(1024 * 1024, min(configured, 100 * 1024 * 1024))
    try:
        guild_limit = int(getattr(guild, "filesize_limit", 0) or 0)
    except Exception:
        guild_limit = 0
    if guild_limit > 0:
        return max(1024 * 1024, min(configured, guild_limit))
    return configured


def _first_x_status_url(text: str) -> str:
    for raw in URL_RE.findall(text or ""):
        canonical = _canonical_share_url(raw)
        if _x_status_parts(canonical) is not None:
            return canonical
    return ""


def _iter_extracted_video_entries(info: Any) -> list[Mapping[str, Any]]:
    if not isinstance(info, Mapping):
        return []
    entries = info.get("entries")
    if isinstance(entries, (list, tuple)):
        flattened: list[Mapping[str, Any]] = []
        for entry in entries:
            flattened.extend(_iter_extracted_video_entries(entry))
        return flattened
    return [info]


def _select_progressive_video_url(
    info: Any,
    *,
    max_bytes: int,
) -> str:
    candidates: list[tuple[tuple[int, int, float, int], str]] = []

    for entry in _iter_extracted_video_entries(info):
        formats = entry.get("formats")
        pool: list[Mapping[str, Any]] = []
        if isinstance(formats, list):
            pool.extend(item for item in formats if isinstance(item, Mapping))
        pool.append(entry)

        for fmt in pool:
            url = _safe_str(fmt.get("url"))
            if not url or not _trusted_video_url(url):
                continue

            protocol = _safe_str(fmt.get("protocol")).lower()
            if protocol and protocol not in {"http", "https"}:
                continue

            ext = _safe_str(fmt.get("ext")).lower()
            if ext and ext not in {"mp4", "webm", "mov"}:
                continue

            vcodec = _safe_str(fmt.get("vcodec")).lower()
            if vcodec == "none":
                continue

            known_size = _safe_int(
                fmt.get("filesize") or fmt.get("filesize_approx"),
                0,
            )
            if known_size > max_bytes > 0:
                continue

            acodec = _safe_str(fmt.get("acodec")).lower()
            has_audio = 0 if acodec == "none" else 1
            height = _safe_int(fmt.get("height"), 0)
            try:
                tbr = float(fmt.get("tbr") or 0.0)
            except Exception:
                tbr = 0.0

            # Prefer combined A/V, then resolution/bitrate. Unknown-size media
            # is allowed because the actual relay is still hard-capped while
            # streaming and fails open to the source link.
            size_score = -known_size if known_size > 0 else 0
            candidates.append(((has_audio, height, tbr, size_score), url))

    if not candidates:
        return ""
    candidates.sort(key=lambda item: item[0], reverse=True)
    return candidates[0][1]


def _extract_x_video_url_sync(status_url: str, max_bytes: int) -> str:
    canonical = _canonical_share_url(status_url)
    if _x_status_parts(canonical) is None:
        return ""

    try:
        import yt_dlp
    except Exception:
        return ""

    options = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "noplaylist": True,
        "cachedir": False,
        "socket_timeout": 6,
        "retries": 1,
        "extractor_retries": 1,
        "fragment_retries": 0,
    }
    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(canonical, download=False)
    except Exception:
        return ""

    return _select_progressive_video_url(info, max_bytes=max_bytes)


async def _extract_x_video_url(status_url: str, *, max_bytes: int) -> str:
    canonical = _canonical_share_url(status_url)
    status = _x_status_parts(canonical)
    if status is None:
        return ""

    cache_key = f"x-status:{status[1]}"
    now = time.monotonic()
    cached = _X_VIDEO_CACHE.get(cache_key)
    if cached is not None:
        expires_at, value = cached
        if now < expires_at:
            return value or ""
        _X_VIDEO_CACHE.pop(cache_key, None)

    async with _X_EXTRACT_SEMAPHORE:
        # Recheck after waiting for the semaphore so concurrent shares of the
        # same status do not all hit X independently.
        now = time.monotonic()
        cached = _X_VIDEO_CACHE.get(cache_key)
        if cached is not None:
            expires_at, value = cached
            if now < expires_at:
                return value or ""
            _X_VIDEO_CACHE.pop(cache_key, None)

        try:
            extracted = await asyncio.wait_for(
                asyncio.to_thread(
                    _extract_x_video_url_sync,
                    canonical,
                    int(max_bytes),
                ),
                timeout=10.0,
            )
        except (asyncio.TimeoutError, Exception):
            extracted = ""

        ttl = (
            _X_VIDEO_CACHE_TTL_SECONDS
            if extracted
            else _X_VIDEO_NEGATIVE_CACHE_TTL_SECONDS
        )
        _X_VIDEO_CACHE[cache_key] = (time.monotonic() + ttl, extracted or None)
        return extracted


def _share_video_timeout_seconds() -> float:
    try:
        raw = float(
            str(
                os.getenv(
                    "DANK_SHARE_ROUTER_VIDEO_TIMEOUT_SECONDS",
                    str(_DEFAULT_VIDEO_TIMEOUT_SECONDS),
                )
                or _DEFAULT_VIDEO_TIMEOUT_SECONDS
            ).strip()
        )
    except Exception:
        raw = _DEFAULT_VIDEO_TIMEOUT_SECONDS
    return max(3.0, min(raw, 30.0))


def _video_filename(url: str, content_type: str) -> str:
    extension = _VIDEO_CONTENT_TYPES.get(str(content_type or "").split(";", 1)[0].strip().lower(), "")
    if not extension:
        try:
            path = str(urlsplit(url).path or "").lower()
            extension = next((item for item in _VIDEO_EXTENSIONS if path.endswith(item)), "")
        except Exception:
            extension = ""
    return f"share-router-video{extension or '.mp4'}"


async def _download_trusted_video(
    url: str,
    *,
    max_bytes: int,
) -> Optional[RoutedVideo]:
    if not _trusted_video_url(url):
        return None

    timeout = aiohttp.ClientTimeout(
        total=_share_video_timeout_seconds(),
        connect=4.0,
        sock_read=8.0,
    )
    spool = tempfile.SpooledTemporaryFile(max_size=2 * 1024 * 1024, mode="w+b")
    current = url
    try:
        async with aiohttp.ClientSession(
            timeout=timeout,
            headers={"User-Agent": "DankShield-ShareRouter/1.0"},
        ) as session:
            for _ in range(4):
                if not _trusted_video_url(current):
                    return None
                async with session.get(current, allow_redirects=False) as response:
                    if response.status in {301, 302, 303, 307, 308}:
                        location = _safe_str(response.headers.get("Location"))
                        if not location:
                            return None
                        current = urljoin(current, location)
                        continue
                    if response.status != 200:
                        return None
                    if not _trusted_video_url(str(response.url)):
                        return None

                    content_type = _safe_str(response.headers.get("Content-Type")).lower()
                    media_type = content_type.split(";", 1)[0].strip()
                    path = str(urlsplit(str(response.url)).path or "").lower()
                    if not (
                        media_type.startswith("video/")
                        or (
                            media_type in {"", "application/octet-stream"}
                            and path.endswith(_VIDEO_EXTENSIONS)
                        )
                    ):
                        return None

                    content_length = _safe_int(response.headers.get("Content-Length"), 0)
                    if content_length > max_bytes > 0:
                        return None

                    total = 0
                    async for chunk in response.content.iter_chunked(64 * 1024):
                        total += len(chunk)
                        if total > max_bytes:
                            return None
                        spool.write(chunk)

                    if total <= 0:
                        return None
                    spool.seek(0)
                    file = discord.File(
                        spool,
                        filename=_video_filename(str(response.url), media_type),
                    )
                    spool = None
                    return RoutedVideo(file=file, source_url=str(response.url), size_bytes=total)
            return None
    except (aiohttp.ClientError, asyncio.TimeoutError, OSError, ValueError):
        return None
    finally:
        if spool is not None:
            try:
                spool.close()
            except Exception:
                pass


async def _prepare_native_video(
    message: discord.Message,
    target: discord.TextChannel,
    routed_text: str,
) -> Optional[RoutedVideo]:
    try:
        me = target.guild.me
        if not isinstance(me, discord.Member):
            return None
        if not target.permissions_for(me).attach_files:
            return None
    except Exception:
        return None

    max_bytes = _share_video_limit_bytes(message.guild)

    # Fast path: use a real video URL Discord already supplied.
    for candidate in _video_source_urls(message):
        routed = await _download_trusted_video(candidate, max_bytes=max_bytes)
        if routed is not None:
            return routed

    # X commonly gives Discord only a static preview image. When that happens,
    # extract the real progressive video URL from the canonical X status itself.
    status_url = _first_x_status_url(routed_text)
    if status_url:
        candidate = await _extract_x_video_url(status_url, max_bytes=max_bytes)
        if candidate:
            routed = await _download_trusted_video(candidate, max_bytes=max_bytes)
            if routed is not None:
                return routed

    return None


def _utc_iso() -> str:
    try:
        return discord.utils.utcnow().isoformat()
    except Exception:
        return ""


def _load_all_unlocked() -> dict[str, Any]:
    try:
        if not ROUTES_FILE.exists():
            return {}
        data = json.loads(ROUTES_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_all_unlocked(data: Mapping[str, Any]) -> None:
    ROUTES_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = ROUTES_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(dict(data), indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(ROUTES_FILE)


async def guild_routes(guild_id: int) -> list[dict[str, Any]]:
    async with _DATA_LOCK:
        data = _load_all_unlocked()
        bucket = data.get(str(int(guild_id))) or {}
        routes = bucket.get("routes") if isinstance(bucket, dict) else []
        if not isinstance(routes, list):
            return []

        clean: list[dict[str, Any]] = []
        for raw in routes:
            if not isinstance(raw, dict):
                continue
            source_id = _safe_int(raw.get("source_channel_id"), 0)
            target_id = _safe_int(raw.get("target_channel_id"), 0)
            if source_id <= 0 or target_id <= 0:
                continue
            item = dict(raw)
            item["source_channel_id"] = str(source_id)
            item["target_channel_id"] = str(target_id)
            item["enabled"] = bool(item.get("enabled", True))
            item["delete_source"] = bool(item.get("delete_source", True))
            clean.append(item)
        return clean


async def save_route(
    guild_id: int,
    *,
    source_channel_id: int,
    target_channel_id: int,
    delete_source: bool = True,
    created_by_id: int = 0,
) -> None:
    async with _DATA_LOCK:
        data = _load_all_unlocked()
        key = str(int(guild_id))
        bucket = data.get(key)
        if not isinstance(bucket, dict):
            bucket = {}
        routes = bucket.get("routes")
        if not isinstance(routes, list):
            routes = []

        source_text = str(int(source_channel_id))
        next_routes = [
            dict(raw)
            for raw in routes
            if isinstance(raw, dict) and str(raw.get("source_channel_id")) != source_text
        ]
        now = _utc_iso()
        next_routes.append(
            {
                "source_channel_id": source_text,
                "target_channel_id": str(int(target_channel_id)),
                "enabled": True,
                "delete_source": bool(delete_source),
                "created_by_id": str(int(created_by_id or 0)),
                "created_at": now,
                "updated_at": now,
            }
        )
        bucket["routes"] = next_routes
        data[key] = bucket
        _save_all_unlocked(data)


async def remove_route(guild_id: int, source_channel_id: int) -> bool:
    async with _DATA_LOCK:
        data = _load_all_unlocked()
        key = str(int(guild_id))
        bucket = data.get(key)
        if not isinstance(bucket, dict):
            return False
        routes = bucket.get("routes")
        if not isinstance(routes, list):
            return False

        before = len(routes)
        source_text = str(int(source_channel_id))
        bucket["routes"] = [
            raw
            for raw in routes
            if not (isinstance(raw, dict) and str(raw.get("source_channel_id")) == source_text)
        ]
        data[key] = bucket
        _save_all_unlocked(data)
        return len(bucket["routes"]) != before


def route_for_source(routes: list[dict[str, Any]], source_channel_id: int) -> Optional[dict[str, Any]]:
    source_text = str(int(source_channel_id))
    for route in routes:
        if not route.get("enabled", True):
            continue
        if str(route.get("source_channel_id")) == source_text:
            return route
    return None


def source_privacy_blocker(source: discord.TextChannel) -> str:
    try:
        everyone_perms = source.permissions_for(source.guild.default_role)
        if everyone_perms.view_channel:
            return "@everyone can view the proxy source. Hide it and grant only the intended role(s) access."
    except Exception:
        return "Could not verify @everyone visibility for the proxy source."
    return ""


def source_age_blocker(source: discord.TextChannel) -> str:
    try:
        if bool(source.is_nsfw()):
            return "The proxy source itself is age-restricted. Keep the proxy non-age-restricted or it cannot solve the mobile share-sheet problem."
    except Exception:
        return "Could not verify whether the proxy source is age-restricted."
    return ""


def route_permission_blockers(
    source: discord.TextChannel,
    target: discord.TextChannel,
    *,
    delete_source: bool,
) -> list[str]:
    blockers: list[str] = []
    if int(getattr(source, "id", 0) or 0) == int(getattr(target, "id", 0) or 0):
        blockers.append("The proxy source and real destination must be different channels.")
    me = source.guild.me
    if not isinstance(me, discord.Member):
        return ["Dank Shield's server member could not be resolved."]

    source_perms = source.permissions_for(me)
    target_perms = target.permissions_for(me)

    if not source_perms.view_channel:
        blockers.append(f"Dank Shield cannot view source {source.mention}.")
    if not source_perms.read_message_history:
        blockers.append(f"Dank Shield cannot read source history in {source.mention}.")
    if bool(delete_source) and not source_perms.manage_messages:
        blockers.append(f"Dank Shield needs Manage Messages in {source.mention} to clean routed posts.")
    if not target_perms.view_channel:
        blockers.append(f"Dank Shield cannot view target {target.mention}.")
    if not target_perms.send_messages:
        blockers.append(f"Dank Shield cannot send messages in target {target.mention}.")
    return blockers


def route_health(guild: discord.Guild, route: Mapping[str, Any]) -> RouteHealth:
    blockers: list[str] = []
    warnings: list[str] = []
    if not bool(route.get("enabled", True)):
        blockers.append("Route is disabled. Use Add / Change Route to enable it again.")
    source_id = _safe_int(route.get("source_channel_id"), 0)
    target_id = _safe_int(route.get("target_channel_id"), 0)
    source = guild.get_channel(source_id)
    target = guild.get_channel(target_id)

    if not isinstance(source, discord.TextChannel):
        blockers.append(f"Source channel {source_id or 'unknown'} is missing.")
    if not isinstance(target, discord.TextChannel):
        blockers.append(f"Target channel {target_id or 'unknown'} is missing.")
    if blockers:
        return RouteHealth(tuple(blockers), tuple(warnings))

    age_blocker = source_age_blocker(source)
    if age_blocker:
        blockers.append(age_blocker)
    privacy = source_privacy_blocker(source)
    if privacy:
        blockers.append(privacy)
    blockers.extend(
        route_permission_blockers(
            source,
            target,
            delete_source=bool(route.get("delete_source", True)),
        )
    )

    try:
        me = guild.me
        if isinstance(me, discord.Member):
            target_perms = target.permissions_for(me)
            if not target_perms.embed_links:
                warnings.append(f"Dank Shield lacks Embed Links in {target.mention}; URLs can route but previews may be reduced.")
            if not target_perms.attach_files:
                warnings.append(
                    f"Dank Shield lacks Attach Files in {target.mention}; routed videos will fall back to provider links instead of native inline playback."
                )
    except Exception:
        warnings.append("Could not verify destination Embed Links / Attach Files permissions.")

    if not is_share_router_design_resource(source):
        warnings.append("This is a legacy/custom proxy source outside the canonical Share Routes hub.")

    return RouteHealth(tuple(dict.fromkeys(blockers)), tuple(dict.fromkeys(warnings)))


def _message_share_text(message: discord.Message) -> str:
    parts: list[str] = []
    seen_urls: set[str] = set()

    content = _normalize_share_text_urls(
        _safe_str(getattr(message, "content", "")),
        seen_urls,
    )
    if content:
        parts.append(content)

    # Discord's generated embed title/description are derived preview copy, not
    # human-authored share text. Re-forwarding them caused X shares to include
    # both the x.com URL and Discord's twitter.com alias plus duplicated titles.
    try:
        for embed in list(getattr(message, "embeds", []) or []):
            text = _normalize_share_text_urls(
                _safe_str(getattr(embed, "url", None)),
                seen_urls,
            )
            if text:
                parts.append(text)
    except Exception:
        pass

    try:
        for attachment in list(getattr(message, "attachments", []) or []):
            text = _normalize_share_text_urls(
                _safe_str(getattr(attachment, "url", "")),
                seen_urls,
            )
            if text:
                parts.append(text)
    except Exception:
        pass

    return "\n".join(part for part in parts if part).strip()


def _dedupe_key(text: str) -> str:
    urls = URL_RE.findall(text or "")
    if urls:
        return _url_identity(urls[0])
    return re.sub(r"\s+", " ", (text or "").strip().lower())[:180]


def _prune_recent(now: float) -> None:
    stale = [key for key, saved in _RECENT_ROUTE_KEYS.items() if now - float(saved or 0.0) > 3600.0]
    for key in stale:
        _RECENT_ROUTE_KEYS.pop(key, None)


async def _send_modlog(guild: discord.Guild, embed: discord.Embed) -> None:
    try:
        from stoney_verify import spam_guard

        sender = getattr(spam_guard, "_send_modlog_embed", None)
        if callable(sender):
            await sender(guild, embed)
    except Exception:
        pass


async def _route_rejection_log(
    message: discord.Message,
    *,
    target: Optional[discord.TextChannel],
    reason: str,
) -> None:
    try:
        embed = discord.Embed(
            title="⚠️ Share Router Blocked a Route",
            description=reason[:4000],
            color=discord.Color.orange(),
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(name="Source", value=f"{message.channel.mention} ({message.channel.id})", inline=False)
        if target is not None:
            embed.add_field(name="Target", value=f"{target.mention} ({target.id})", inline=False)
        embed.add_field(name="Author", value=f"{message.author.mention} ({message.author.id})", inline=False)
        await _send_modlog(message.guild, embed)
    except Exception:
        pass


async def route_message(message: discord.Message) -> None:
    """Route one human message when its channel is a configured proxy source."""

    try:
        guild = message.guild
        if guild is None or not isinstance(message.channel, discord.TextChannel):
            return
        if getattr(message.author, "bot", False):
            return

        routes = await guild_routes(int(guild.id))
        route = route_for_source(routes, int(message.channel.id))
        if route is None:
            return

        age_blocker = source_age_blocker(message.channel)
        if age_blocker:
            await _route_rejection_log(message, target=None, reason=age_blocker)
            return
        privacy_blocker = source_privacy_blocker(message.channel)
        if privacy_blocker:
            await _route_rejection_log(message, target=None, reason=privacy_blocker)
            return

        target_id = _safe_int(route.get("target_channel_id"), 0)
        target = guild.get_channel(target_id)
        if not isinstance(target, discord.TextChannel):
            await _route_rejection_log(message, target=None, reason="The saved destination channel no longer exists.")
            return

        author = message.author
        if not isinstance(author, discord.Member):
            await _route_rejection_log(message, target=target, reason="The sender could not be resolved as a server member.")
            return

        # Never use the bot to bypass normal Discord channel permissions. This
        # does not attempt to infer account age; Discord still controls viewing
        # of age-restricted destinations on the client/account side.
        author_perms = target.permissions_for(author)
        if not author_perms.view_channel or not author_perms.send_messages:
            await _route_rejection_log(
                message,
                target=target,
                reason="The sender does not have normal View Channel + Send Messages permission in the destination.",
            )
            return

        me = guild.me
        if not isinstance(me, discord.Member):
            return
        source_perms = message.channel.permissions_for(me)
        permission_blockers = route_permission_blockers(
            message.channel,
            target,
            delete_source=bool(route.get("delete_source", True)),
        )
        if permission_blockers:
            await _route_rejection_log(
                message,
                target=target,
                reason=permission_blockers[0],
            )
            return

        text = _message_share_text(message)
        if not text:
            return

        now = time.monotonic()
        _prune_recent(now)
        key_text = _dedupe_key(text)
        dedupe = (int(guild.id), int(target.id), key_text)
        duplicate = bool(key_text and dedupe in _RECENT_ROUTE_KEYS)
        if key_text:
            _RECENT_ROUTE_KEYS[dedupe] = now

        if not duplicate:
            native_video = await _prepare_native_video(message, target, text)
            routed_text = text
            if native_video is not None:
                # Keep the source URL clickable without asking Discord to render
                # a second provider preview beside the uploaded native player.
                routed_text = _suppress_url_previews(routed_text)
            routed = f"{routed_text}\n\n↪️ Shared by {message.author.mention} via Dank Shield Share Router"

            send_payload: dict[str, Any] = {
                "content": routed[:2000],
                "allowed_mentions": discord.AllowedMentions.none(),
            }
            if native_video is not None:
                send_payload["file"] = native_video.file

            try:
                await target.send(**send_payload)
                if native_video is not None:
                    _log(
                        f"native video relayed guild={guild.id} source={message.channel.id} "
                        f"target={target.id} bytes={native_video.size_bytes}"
                    )
            except (discord.Forbidden, discord.HTTPException) as exc:
                # A provider-link route is still preferable to losing the share
                # if Discord rejects the file at send time (size/bucket/etc.).
                if native_video is None:
                    raise
                _log(
                    f"native video send fallback guild={guild.id} target={target.id} "
                    f"error={type(exc).__name__}"
                )
                await target.send(
                    f"{text}\n\n↪️ Shared by {message.author.mention} via Dank Shield Share Router"[:2000],
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            finally:
                if native_video is not None:
                    try:
                        native_video.file.close()
                    except Exception:
                        pass

        if bool(route.get("delete_source", True)) and source_perms.manage_messages:
            try:
                await message.delete(reason="Dank Shield Share Router: proxy message routed")
            except discord.NotFound:
                pass
            except Exception:
                pass

        embed = discord.Embed(
            title="🔗 Share Router Routed Message" if not duplicate else "🔁 Share Router Duplicate Cleaned",
            color=discord.Color.green() if not duplicate else discord.Color.orange(),
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(name="Source", value=f"{message.channel.mention} ({message.channel.id})", inline=False)
        embed.add_field(name="Target", value=f"{target.mention} ({target.id})", inline=False)
        embed.add_field(name="Author", value=f"{message.author.mention} ({message.author.id})", inline=False)
        if duplicate:
            embed.add_field(name="Duplicate", value="Already routed recently, so it was not reposted.", inline=False)
        await _send_modlog(guild, embed)

    except Exception as exc:
        _log(f"route failed: {type(exc).__name__}: {exc}")


def _merged_overwrite(
    existing: Optional[discord.PermissionOverwrite],
    **updates: Optional[bool],
) -> discord.PermissionOverwrite:
    try:
        allow, deny = existing.pair() if existing is not None else (discord.Permissions.none(), discord.Permissions.none())
        merged = discord.PermissionOverwrite.from_pair(allow, deny)
    except Exception:
        merged = discord.PermissionOverwrite()

    for name, value in updates.items():
        try:
            setattr(merged, name, value)
        except Exception:
            pass
    return merged


def _permission_overwrite_map(
    guild: discord.Guild,
    user: discord.abc.User,
    *,
    current: Optional[Mapping[Any, discord.PermissionOverwrite]] = None,
) -> dict[Any, discord.PermissionOverwrite]:
    overwrites: dict[Any, discord.PermissionOverwrite] = dict(current or {})
    overwrites[guild.default_role] = _merged_overwrite(
        overwrites.get(guild.default_role),
        view_channel=False,
    )

    me = guild.me
    if isinstance(me, discord.Member):
        overwrites[me] = _merged_overwrite(
            overwrites.get(me),
            view_channel=True,
            send_messages=True,
            read_message_history=True,
            manage_messages=True,
            embed_links=True,
        )

    if isinstance(user, discord.Member):
        overwrites[user] = _merged_overwrite(
            overwrites.get(user),
            view_channel=True,
            send_messages=True,
            read_message_history=True,
        )
    return overwrites


async def _add_configured_staff_overwrites(
    guild: discord.Guild,
    overwrites: dict[Any, discord.PermissionOverwrite],
) -> dict[Any, discord.PermissionOverwrite]:
    try:
        from stoney_verify.guild_config import get_guild_config

        cfg = await get_guild_config(int(guild.id), refresh=False)
    except Exception:
        cfg = None

    role_ids: set[int] = set()
    for key in ("staff_role_id", "server_control_role_id", "control_role_id", "perm_role_id", "vc_staff_role_id"):
        try:
            raw = cfg.get(key) if hasattr(cfg, "get") else getattr(cfg, key, None)
            rid = _safe_int(raw, 0)
            if rid > 0:
                role_ids.add(rid)
        except Exception:
            pass

    for rid in role_ids:
        role = guild.get_role(rid)
        if role is not None and not role.is_default():
            overwrites[role] = _merged_overwrite(
                overwrites.get(role),
                view_channel=True,
                send_messages=True,
                read_message_history=True,
            )
    return overwrites


def find_share_router_categories(guild: discord.Guild) -> list[discord.CategoryChannel]:
    return [
        category
        for category in list(getattr(guild, "categories", []) or [])
        if isinstance(category, discord.CategoryChannel)
        and is_share_router_category_name(getattr(category, "name", ""))
    ]


def find_share_router_category(guild: discord.Guild) -> Optional[discord.CategoryChannel]:
    categories = find_share_router_categories(guild)
    if not categories:
        return None

    def score(category: discord.CategoryChannel) -> tuple[int, int]:
        canonical_children = sum(
            1
            for channel in list(getattr(category, "channels", []) or [])
            if isinstance(channel, discord.TextChannel)
            and share_source_key(getattr(channel, "name", "")) is not None
        )
        exact_name = 1 if str(getattr(category, "name", "")) == SHARE_ROUTER_CATEGORY_NAME else 0
        return canonical_children, exact_name

    # Prefer the hub that already owns the proxy children. This avoids choosing
    # an empty duplicate merely because its category name happens to be exact.
    return max(categories, key=score)


def find_share_source_channel(
    category: discord.CategoryChannel,
    source_name: str,
) -> Optional[discord.TextChannel]:
    wanted = share_source_key(source_name)
    if wanted is None:
        return None

    matches = [
        channel
        for channel in list(getattr(category, "channels", []) or [])
        if isinstance(channel, discord.TextChannel) and share_source_key(getattr(channel, "name", "")) == wanted
    ]
    if not matches:
        return None
    exact = next((channel for channel in matches if str(channel.name) == wanted), None)
    return exact or matches[0]


async def create_or_repair_hidden_share_hub(
    guild: discord.Guild,
    actor: discord.abc.User,
) -> HubRepairResult:
    """Create or repair one canonical hub without deleting duplicate resources."""

    created: list[str] = []
    repaired: list[str] = []
    warnings: list[str] = []

    categories = find_share_router_categories(guild)
    category = find_share_router_category(guild)

    if category is None:
        overwrites = _permission_overwrite_map(guild, actor)
        overwrites = await _add_configured_staff_overwrites(guild, overwrites)
        category = await guild.create_category(
            SHARE_ROUTER_CATEGORY_NAME,
            overwrites=overwrites,
            reason="Dank Shield Share Router proxy hub",
        )
        created.append(SHARE_ROUTER_CATEGORY_NAME)
    else:
        current_overwrites = dict(getattr(category, "overwrites", {}) or {})
        overwrites = _permission_overwrite_map(guild, actor, current=current_overwrites)
        overwrites = await _add_configured_staff_overwrites(guild, overwrites)
        changes: dict[str, Any] = {"overwrites": overwrites}
        if str(category.name) != SHARE_ROUTER_CATEGORY_NAME:
            changes["name"] = SHARE_ROUTER_CATEGORY_NAME
            repaired.append(f"category -> {SHARE_ROUTER_CATEGORY_NAME}")
        await category.edit(
            **changes,
            reason="Dank Shield Share Router privacy/name repair",
        )

    if len(categories) > 1:
        extras = [str(item.name) for item in categories if int(item.id) != int(category.id)]
        warnings.append(
            "Multiple Share Router-like categories exist. Nothing was deleted automatically: "
            + ", ".join(extras[:5])
        )

    for canonical in DEFAULT_SHARE_CHANNELS:
        matches = [
            channel
            for channel in list(getattr(category, "channels", []) or [])
            if isinstance(channel, discord.TextChannel)
            and share_source_key(getattr(channel, "name", "")) == canonical
        ]
        channel = find_share_source_channel(category, canonical)

        if channel is None:
            source_overwrites = _permission_overwrite_map(guild, actor)
            source_overwrites = await _add_configured_staff_overwrites(guild, source_overwrites)
            channel = await guild.create_text_channel(
                canonical,
                category=category,
                nsfw=False,
                overwrites=source_overwrites,
                reason="Dank Shield Share Router proxy source",
            )
            created.append(canonical)
        else:
            current_source_overwrites = dict(getattr(channel, "overwrites", {}) or {})
            source_overwrites = _permission_overwrite_map(
                guild,
                actor,
                current=current_source_overwrites,
            )
            source_overwrites = await _add_configured_staff_overwrites(guild, source_overwrites)
            edits: dict[str, Any] = {"overwrites": source_overwrites}
            old_name = str(channel.name)
            if old_name != canonical:
                edits["name"] = canonical
                repaired.append(f"{old_name} -> {canonical}")
            try:
                if bool(channel.is_nsfw()):
                    edits["nsfw"] = False
                    repaired.append(f"{canonical} -> non-age-restricted proxy")
            except Exception:
                warnings.append(f"Could not verify age-restriction state for {canonical}.")
            await channel.edit(
                **edits,
                reason="Dank Shield Share Router canonical proxy privacy/name repair",
            )

        if len(matches) > 1:
            extras = [str(item.name) for item in matches if int(item.id) != int(channel.id)]
            warnings.append(
                f"Multiple channels match {canonical}. Nothing was deleted automatically: "
                + ", ".join(extras[:5])
            )

    return HubRepairResult(
        category_id=int(category.id),
        created=tuple(created),
        repaired=tuple(repaired),
        warnings=tuple(warnings),
    )


def ensure_share_router_runtime(bot: Any) -> bool:
    """Install the one production on_message listener idempotently."""

    try:
        existing = list((getattr(bot, "extra_events", {}) or {}).get("on_message") or [])
        for fn in existing:
            if (
                getattr(fn, "__name__", "") == "route_message"
                and getattr(fn, "__module__", "") == __name__
            ):
                return True
        bot.add_listener(route_message, "on_message")
        _log("active; private share proxies can route into configured destinations")
        return True
    except Exception as exc:
        _log(f"runtime install failed: {type(exc).__name__}: {exc}")
        return False


__all__ = [
    "HubRepairResult",
    "ROUTES_FILE",
    "RouteHealth",
    "create_or_repair_hidden_share_hub",
    "ensure_share_router_runtime",
    "find_share_router_categories",
    "find_share_router_category",
    "find_share_source_channel",
    "guild_routes",
    "remove_route",
    "route_for_source",
    "route_health",
    "route_message",
    "route_permission_blockers",
    "save_route",
    "source_age_blocker",
    "source_privacy_blocker",
]
