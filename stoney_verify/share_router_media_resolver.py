from __future__ import annotations

"""Provider-neutral Share Router media resolver.

This module owns URL/provider recognition, canonical media identity, bounded
yt-dlp metadata extraction, progressive-format selection, manifest detection,
provider policy, cache/coalescing, and lightweight health diagnostics.

It deliberately does not send Discord messages or download media bytes. The
existing Share Router runtime remains the single route/upload owner.
"""

import asyncio
import ipaddress
import os
import re
import time
from dataclasses import dataclass
from typing import Any, Mapping, Optional
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


MEDIA_URL_RE = re.compile(r"https?://[^\s<>()]+", re.IGNORECASE)
_VIDEO_EXTENSIONS = {".mp4", ".webm", ".mov", ".gif"}
_MANIFEST_EXTENSIONS = {".m3u8", ".mpd"}
_PROGRESSIVE_EXTENSIONS = {"mp4", "webm", "mov", "gif"}
_AUDIO_EXTENSIONS = {"m4a", "mp4", "webm", "aac", "mp3", "opus", "ogg"}
_PROGRESSIVE_PROTOCOLS = {"http", "https"}
_MANIFEST_PROTOCOLS = {"m3u8", "m3u8_native", "http_dash_segments", "dash"}
_TRACKING_QUERY_KEYS = {
    "fbclid",
    "feature",
    "igsh",
    "igshid",
    "ref",
    "ref_src",
    "si",
    "source",
}
_LOCAL_HOST_SUFFIXES = (
    ".local",
    ".localhost",
    ".internal",
    ".lan",
    ".home",
    ".home.arpa",
)
_X_STATUS_PATH_RE = re.compile(r"^/([^/]+)/status/(\d+)(?:/.*)?$", re.IGNORECASE)
_TIKTOK_VIDEO_RE = re.compile(r"/video/(\d+)(?:/|$)", re.IGNORECASE)
_INSTAGRAM_MEDIA_RE = re.compile(r"/(?:reel|reels|p|tv)/([^/?#]+)", re.IGNORECASE)
_REDDIT_POST_RE = re.compile(r"/comments/([^/?#]+)", re.IGNORECASE)


@dataclass(frozen=True)
class MediaProvider:
    key: str
    label: str
    hosts: tuple[str, ...]


@dataclass(frozen=True)
class MediaResolution:
    source_url: str
    canonical_url: str
    identity: str
    provider: str
    delivery: str
    media_url: str = ""
    audio_url: str = ""
    protocol: str = ""
    audio_protocol: str = ""
    ext: str = ""
    audio_ext: str = ""
    known_size: int = 0
    audio_known_size: int = 0
    request_headers: tuple[tuple[str, str], ...] = ()
    audio_request_headers: tuple[tuple[str, str], ...] = ()
    reason: str = ""

    @property
    def progressive(self) -> bool:
        return self.delivery == "progressive" and bool(self.media_url)

    @property
    def manifest(self) -> bool:
        return self.delivery == "manifest"


PROVIDERS: tuple[MediaProvider, ...] = (
    MediaProvider("x", "X / Twitter", ("x.com", "twitter.com")),
    MediaProvider("tiktok", "TikTok", ("tiktok.com", "vm.tiktok.com", "vt.tiktok.com")),
    MediaProvider("instagram", "Instagram / Reels", ("instagram.com",)),
    MediaProvider("youtube", "YouTube / Shorts", ("youtube.com", "youtu.be")),
    MediaProvider("reddit", "Reddit", ("reddit.com", "redd.it", "v.redd.it")),
    MediaProvider("twitch", "Twitch", ("twitch.tv", "clips.twitch.tv")),
    MediaProvider("facebook", "Facebook", ("facebook.com", "fb.watch")),
    MediaProvider("vimeo", "Vimeo", ("vimeo.com",)),
    MediaProvider("streamable", "Streamable", ("streamable.com",)),
    MediaProvider("imgur", "Imgur", ("imgur.com", "i.imgur.com")),
    MediaProvider("tumblr", "Tumblr", ("tumblr.com",)),
    MediaProvider("bluesky", "Bluesky", ("bsky.app",)),
    MediaProvider("pinterest", "Pinterest", ("pinterest.com", "pin.it")),
)
_PROVIDER_KEYS = frozenset(item.key for item in PROVIDERS)
_ALL_POLICY_KEYS = frozenset((*_PROVIDER_KEYS, "direct"))

_DEFAULT_EXTRACT_CONCURRENCY = 2
_DEFAULT_EXTRACT_TIMEOUT_SECONDS = 12.0
_DEFAULT_CACHE_MAX = 256
_POSITIVE_CACHE_TTL_SECONDS = 15 * 60.0
_NEGATIVE_CACHE_TTL_SECONDS = 90.0

_RESOLVER_LOOP: asyncio.AbstractEventLoop | None = None
_RESOLVER_SEMAPHORE: asyncio.Semaphore | None = None
_RESOLVER_INFLIGHT: dict[tuple[str, int], asyncio.Task[MediaResolution]] = {}
_RESOLUTION_CACHE: dict[tuple[str, int], tuple[float, MediaResolution]] = {}
_PROVIDER_HEALTH: dict[str, dict[str, Any]] = {}


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
        return int(value)
    except Exception:
        return int(default)


def _env_int(name: str, default: int, *, minimum: int, maximum: int) -> int:
    try:
        value = int(str(os.getenv(name, str(default)) or default).strip())
    except Exception:
        value = int(default)
    return max(int(minimum), min(int(maximum), value))


def _extract_concurrency() -> int:
    return _env_int(
        "DANK_SHARE_ROUTER_MEDIA_EXTRACT_CONCURRENCY",
        _DEFAULT_EXTRACT_CONCURRENCY,
        minimum=1,
        maximum=4,
    )


def _cache_max() -> int:
    return _env_int(
        "DANK_SHARE_ROUTER_MEDIA_CACHE_MAX",
        _DEFAULT_CACHE_MAX,
        minimum=32,
        maximum=2048,
    )


def _extract_timeout_seconds() -> float:
    try:
        raw = float(
            str(
                os.getenv(
                    "DANK_SHARE_ROUTER_MEDIA_EXTRACT_TIMEOUT_SECONDS",
                    str(_DEFAULT_EXTRACT_TIMEOUT_SECONDS),
                )
                or _DEFAULT_EXTRACT_TIMEOUT_SECONDS
            ).strip()
        )
    except Exception:
        raw = _DEFAULT_EXTRACT_TIMEOUT_SECONDS
    return max(4.0, min(raw, 30.0))


def _host_matches(host: str, root: str) -> bool:
    host = str(host or "").lower().strip(".")
    root = str(root or "").lower().strip(".")
    return bool(host and root and (host == root or host.endswith("." + root)))


def _clean_input_url(value: str) -> str:
    return _safe_str(value).rstrip(".,)")


def _parsed_http_url(value: str):
    raw = _clean_input_url(value)
    if not raw:
        return None
    try:
        parsed = urlsplit(raw)
    except Exception:
        return None
    if str(parsed.scheme or "").lower() not in {"http", "https"}:
        return None
    if not parsed.hostname:
        return None
    return parsed


def _path_extension(parsed: Any) -> str:
    path = str(getattr(parsed, "path", "") or "").lower()
    for ext in _VIDEO_EXTENSIONS:
        if path.endswith(ext):
            return ext
    return ""


def provider_for_url(value: str) -> str:
    parsed = _parsed_http_url(value)
    if parsed is None:
        return ""
    if _path_extension(parsed) or str(parsed.path or "").lower().endswith(tuple(_MANIFEST_EXTENSIONS)):
        return "direct"
    host = str(parsed.hostname or "").lower().strip(".")
    for provider in PROVIDERS:
        if any(_host_matches(host, root) for root in provider.hosts):
            return provider.key
    return ""


def provider_label(provider: str) -> str:
    key = _safe_str(provider).lower()
    if key == "direct":
        return "Direct media"
    for item in PROVIDERS:
        if item.key == key:
            return item.label
    return key or "Unknown"


def _filtered_query(parsed: Any, provider: str) -> str:
    if provider == "direct":
        return str(parsed.query or "")
    kept: list[tuple[str, str]] = []
    for key, value in parse_qsl(str(parsed.query or ""), keep_blank_values=True):
        lowered = str(key or "").lower()
        if lowered.startswith("utm_") or lowered in _TRACKING_QUERY_KEYS:
            continue
        kept.append((key, value))
    return urlencode(kept, doseq=True)


def _youtube_video_id(parsed: Any) -> str:
    host = str(parsed.hostname or "").lower().strip(".")
    path_parts = [part for part in str(parsed.path or "").split("/") if part]
    if _host_matches(host, "youtu.be") and path_parts:
        return path_parts[0]
    if _host_matches(host, "youtube.com"):
        if str(parsed.path or "").rstrip("/") == "/watch":
            for key, value in parse_qsl(str(parsed.query or ""), keep_blank_values=True):
                if key == "v" and value:
                    return value
        if len(path_parts) >= 2 and path_parts[0].lower() in {"shorts", "live", "embed"}:
            return path_parts[1]
    return ""


def canonicalize_media_url(value: str) -> str:
    parsed = _parsed_http_url(value)
    if parsed is None:
        return _clean_input_url(value)

    provider = provider_for_url(value)
    host = str(parsed.hostname or "").lower().strip(".")
    scheme = str(parsed.scheme or "").lower()
    try:
        port = parsed.port
    except ValueError:
        return _clean_input_url(value)
    netloc = host if not port else f"{host}:{port}"
    path = str(parsed.path or "") or "/"

    if provider == "x":
        match = _X_STATUS_PATH_RE.match(path)
        if match:
            handle, status_id = match.groups()
            return f"https://x.com/{handle}/status/{status_id}"

    if provider == "youtube":
        video_id = _youtube_video_id(parsed)
        if video_id:
            return f"https://www.youtube.com/watch?v={video_id}"

    query = _filtered_query(parsed, provider)
    return urlunsplit((scheme, netloc, path, query, ""))


def media_url_identity(value: str) -> str:
    canonical = canonicalize_media_url(value)
    parsed = _parsed_http_url(canonical)
    if parsed is None:
        return canonical
    provider = provider_for_url(canonical)

    if provider == "x":
        match = _X_STATUS_PATH_RE.match(str(parsed.path or ""))
        if match:
            return f"x-status:{match.group(2)}"

    if provider == "youtube":
        video_id = _youtube_video_id(parsed)
        if video_id:
            return f"youtube:{video_id}"

    if provider == "tiktok":
        match = _TIKTOK_VIDEO_RE.search(str(parsed.path or ""))
        if match:
            return f"tiktok:{match.group(1)}"

    if provider == "instagram":
        match = _INSTAGRAM_MEDIA_RE.search(str(parsed.path or ""))
        if match:
            return f"instagram:{match.group(1)}"

    if provider == "reddit":
        match = _REDDIT_POST_RE.search(str(parsed.path or ""))
        if match:
            return f"reddit:{match.group(1)}"

    if provider:
        return f"{provider}:{canonical}"
    return canonical


def _parse_policy_list(name: str) -> set[str]:
    raw = _safe_str(os.getenv(name, ""))
    if not raw:
        return set()
    return {
        item.strip().lower()
        for item in raw.split(",")
        if item.strip().lower() in _ALL_POLICY_KEYS
    }


def media_provider_policy() -> dict[str, bool]:
    explicit_allow = _parse_policy_list("DANK_SHARE_ROUTER_MEDIA_PROVIDERS")
    blocked = _parse_policy_list("DANK_SHARE_ROUTER_MEDIA_BLOCKED_PROVIDERS")
    base = explicit_allow if explicit_allow else set(_ALL_POLICY_KEYS)
    return {key: (key in base and key not in blocked) for key in sorted(_ALL_POLICY_KEYS)}


def provider_allowed(provider: str) -> bool:
    key = _safe_str(provider).lower()
    return bool(key and media_provider_policy().get(key, False))


def is_public_address(value: str) -> bool:
    try:
        return bool(ipaddress.ip_address(str(value or "").strip()).is_global)
    except ValueError:
        return False


def is_safe_media_download_url(value: str) -> bool:
    parsed = _parsed_http_url(value)
    if parsed is None:
        return False
    if parsed.username or parsed.password:
        return False
    try:
        port = parsed.port
    except ValueError:
        return False
    if port not in {None, 80, 443}:
        return False

    host = str(parsed.hostname or "").lower().strip(".")
    if not host or host == "localhost" or host.endswith(_LOCAL_HOST_SUFFIXES):
        return False

    try:
        ipaddress.ip_address(host)
    except ValueError:
        return "." in host
    return is_public_address(host)


def _iter_entries(info: Any) -> list[Mapping[str, Any]]:
    if not isinstance(info, Mapping):
        return []
    entries = info.get("entries")
    if isinstance(entries, (list, tuple)):
        flattened: list[Mapping[str, Any]] = []
        for entry in entries:
            flattened.extend(_iter_entries(entry))
        return flattened
    return [info]


def _candidate_size(fmt: Mapping[str, Any]) -> int:
    return _safe_int(fmt.get("filesize") or fmt.get("filesize_approx"), 0)


_SAFE_REQUEST_HEADERS = {
    "accept",
    "accept-language",
    "origin",
    "referer",
    "user-agent",
}


def _safe_request_headers(*sources: Mapping[str, Any]) -> tuple[tuple[str, str], ...]:
    merged: dict[str, str] = {}
    for source in sources:
        raw = source.get("http_headers")
        if not isinstance(raw, Mapping):
            continue
        for key, value in raw.items():
            name = _safe_str(key)
            text = _safe_str(value)
            if not name or not text or name.lower() not in _SAFE_REQUEST_HEADERS:
                continue
            merged[name] = text[:1000]
    return tuple(sorted(merged.items(), key=lambda item: item[0].lower()))


def _manifest_candidate(fmt: Mapping[str, Any]) -> tuple[str, str, str]:
    protocol = _safe_str(fmt.get("protocol")).lower()
    url = _safe_str(fmt.get("manifest_url") or fmt.get("url"))
    ext = _safe_str(fmt.get("ext")).lower()
    if protocol in _MANIFEST_PROTOCOLS and is_safe_media_download_url(url):
        return url, protocol, ext
    return "", "", ""


def select_media_resolution(
    info: Any,
    *,
    source_url: str,
    provider: str,
    max_bytes: int,
) -> MediaResolution:
    canonical = canonicalize_media_url(source_url)
    identity = media_url_identity(canonical)

    entries = _iter_entries(info)
    if any(
        bool(entry.get("is_live"))
        or _safe_str(entry.get("live_status")).lower() == "is_live"
        for entry in entries
    ):
        return MediaResolution(
            source_url=source_url,
            canonical_url=canonical,
            identity=identity,
            provider=provider,
            delivery="link",
            reason="live_stream_requires_player",
        )
    progressive: list[
        tuple[
            tuple[int, int, float, int],
            str,
            str,
            str,
            int,
            tuple[tuple[str, str], ...],
        ]
    ] = []
    manifest: tuple[
        str,
        str,
        str,
        tuple[tuple[str, str], ...],
    ] | None = None
    video_only: list[
        tuple[
            tuple[int, float, int],
            str,
            str,
            str,
            int,
            tuple[tuple[str, str], ...],
        ]
    ] = []
    audio_only: list[
        tuple[
            tuple[float, int],
            str,
            str,
            str,
            int,
            tuple[tuple[str, str], ...],
        ]
    ] = []
    saw_separate_video = False
    saw_fragmented = False

    for entry in entries:
        formats = entry.get("formats")
        pool: list[Mapping[str, Any]] = []
        if isinstance(formats, list):
            pool.extend(item for item in formats if isinstance(item, Mapping))
        pool.append(entry)

        for fmt in pool:
            url = _safe_str(fmt.get("url"))
            protocol = _safe_str(fmt.get("protocol")).lower()
            ext = _safe_str(fmt.get("ext")).lower().lstrip(".")
            if not protocol and url:
                try:
                    protocol = str(urlsplit(url).scheme or "").lower()
                except Exception:
                    protocol = ""

            fragments = fmt.get("fragments")
            is_fragmented = isinstance(fragments, (list, tuple)) and bool(fragments)
            if is_fragmented:
                saw_fragmented = True
                if manifest is None and is_safe_media_download_url(
                    _safe_str(fmt.get("manifest_url") or url)
                ):
                    manifest = (
                        _safe_str(fmt.get("manifest_url") or url),
                        protocol or "fragmented",
                        ext,
                        _safe_request_headers(entry, fmt),
                    )

            if (
                url
                and not is_fragmented
                and protocol in _PROGRESSIVE_PROTOCOLS
                and is_safe_media_download_url(url)
            ):
                vcodec = _safe_str(fmt.get("vcodec")).lower()
                acodec = _safe_str(fmt.get("acodec")).lower()
                size = _candidate_size(fmt)
                if size > max_bytes > 0:
                    continue
                headers = _safe_request_headers(entry, fmt)

                if vcodec == "none" and acodec != "none":
                    if not ext or ext in _AUDIO_EXTENSIONS:
                        try:
                            abr = float(fmt.get("abr") or fmt.get("tbr") or 0.0)
                        except Exception:
                            abr = 0.0
                        size_score = -size if size > 0 else 0
                        audio_only.append(
                            (
                                (abr, size_score),
                                url,
                                protocol,
                                ext,
                                size,
                                headers,
                            )
                        )
                    continue

                if vcodec != "none" or ext == "gif":
                    if ext and ext not in _PROGRESSIVE_EXTENSIONS:
                        continue
                    if acodec == "none" and ext != "gif":
                        saw_separate_video = True
                        height = _safe_int(fmt.get("height"), 0)
                        try:
                            tbr = float(fmt.get("tbr") or 0.0)
                        except Exception:
                            tbr = 0.0
                        size_score = -size if size > 0 else 0
                        video_only.append(
                            (
                                (height, tbr, size_score),
                                url,
                                protocol,
                                ext,
                                size,
                                headers,
                            )
                        )
                    else:
                        height = _safe_int(fmt.get("height"), 0)
                        try:
                            tbr = float(fmt.get("tbr") or 0.0)
                        except Exception:
                            tbr = 0.0
                        size_score = -size if size > 0 else 0
                        progressive.append(
                            (
                                (1, height, tbr, size_score),
                                url,
                                protocol,
                                ext,
                                size,
                                headers,
                            )
                        )

            if manifest is None:
                candidate = _manifest_candidate(fmt)
                if candidate[0]:
                    manifest = (
                        candidate[0],
                        candidate[1],
                        candidate[2],
                        _safe_request_headers(entry, fmt),
                    )

    if progressive:
        progressive.sort(key=lambda item: item[0], reverse=True)
        _, url, protocol, ext, size, request_headers = progressive[0]
        return MediaResolution(
            source_url=source_url,
            canonical_url=canonical,
            identity=identity,
            provider=provider,
            delivery="progressive",
            media_url=url,
            protocol=protocol,
            ext=ext,
            known_size=size,
            request_headers=request_headers,
        )

    if video_only and audio_only:
        video_only.sort(key=lambda item: item[0], reverse=True)
        audio_only.sort(key=lambda item: item[0], reverse=True)
        for video in video_only:
            for audio in audio_only:
                known_total = int(video[4] or 0) + int(audio[4] or 0)
                if known_total > max_bytes > 0:
                    continue
                return MediaResolution(
                    source_url=source_url,
                    canonical_url=canonical,
                    identity=identity,
                    provider=provider,
                    delivery="merge",
                    media_url=video[1],
                    audio_url=audio[1],
                    protocol=video[2],
                    audio_protocol=audio[2],
                    ext=video[3],
                    audio_ext=audio[3],
                    known_size=video[4],
                    audio_known_size=audio[4],
                    request_headers=video[5],
                    audio_request_headers=audio[5],
                    reason="separate_audio_video_requires_merge",
                )

    if manifest is not None:
        url, protocol, ext, request_headers = manifest
        return MediaResolution(
            source_url=source_url,
            canonical_url=canonical,
            identity=identity,
            provider=provider,
            delivery="manifest",
            media_url=url,
            protocol=protocol,
            ext=ext,
            request_headers=request_headers,
            reason="manifest_requires_controlled_transcode_or_player",
        )

    reason = "no_safe_progressive_format"
    if saw_separate_video:
        reason = "separate_audio_video_requires_merge"
    elif saw_fragmented:
        reason = "fragmented_media_requires_controlled_download"
    return MediaResolution(
        source_url=source_url,
        canonical_url=canonical,
        identity=identity,
        provider=provider,
        delivery="link",
        reason=reason,
    )


def _extract_info_sync(canonical_url: str) -> Any:
    try:
        import yt_dlp
    except Exception:
        return None

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
        "playlistend": 1,
    }
    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            return ydl.extract_info(canonical_url, download=False)
    except Exception:
        return None


def _health_bucket(provider: str) -> dict[str, Any]:
    key = provider or "unknown"
    bucket = _PROVIDER_HEALTH.get(key)
    if bucket is None:
        bucket = {
            "attempts": 0,
            "progressive": 0,
            "manifest": 0,
            "merge": 0,
            "fallback": 0,
            "cache_hits": 0,
            "coalesced": 0,
            "last_reason": "",
        }
        _PROVIDER_HEALTH[key] = bucket
    return bucket


def _record(provider: str, field: str, *, reason: str = "") -> None:
    bucket = _health_bucket(provider)
    bucket[field] = int(bucket.get(field, 0) or 0) + 1
    if reason:
        bucket["last_reason"] = _safe_str(reason)[:120]


def _prune_cache(now: Optional[float] = None) -> None:
    current = time.monotonic() if now is None else float(now)
    for key, (expires_at, _resolution) in list(_RESOLUTION_CACHE.items()):
        if current >= float(expires_at):
            _RESOLUTION_CACHE.pop(key, None)
    limit = _cache_max()
    if len(_RESOLUTION_CACHE) <= limit:
        return
    ordered = sorted(_RESOLUTION_CACHE.items(), key=lambda item: float(item[1][0]))
    for key, _value in ordered[: max(0, len(_RESOLUTION_CACHE) - limit)]:
        _RESOLUTION_CACHE.pop(key, None)


def _ensure_async_state() -> asyncio.Semaphore:
    global _RESOLVER_LOOP
    global _RESOLVER_SEMAPHORE

    loop = asyncio.get_running_loop()
    if _RESOLVER_LOOP is not loop:
        _RESOLVER_LOOP = loop
        _RESOLVER_SEMAPHORE = asyncio.Semaphore(_extract_concurrency())
        _RESOLVER_INFLIGHT.clear()
    assert _RESOLVER_SEMAPHORE is not None
    return _RESOLVER_SEMAPHORE


async def _resolve_extracted(
    source_url: str,
    canonical: str,
    provider: str,
    max_bytes: int,
) -> MediaResolution:
    semaphore = _ensure_async_state()
    _record(provider, "attempts")
    async with semaphore:
        try:
            info = await asyncio.wait_for(
                asyncio.to_thread(_extract_info_sync, canonical),
                timeout=_extract_timeout_seconds(),
            )
        except Exception:
            info = None

    if info is None:
        resolution = MediaResolution(
            source_url=source_url,
            canonical_url=canonical,
            identity=media_url_identity(canonical),
            provider=provider,
            delivery="link",
            reason="extract_failed",
        )
    else:
        resolution = select_media_resolution(
            info,
            source_url=source_url,
            provider=provider,
            max_bytes=max_bytes,
        )

    if resolution.delivery == "progressive":
        _record(provider, "progressive")
    elif resolution.delivery == "manifest":
        _record(provider, "manifest", reason=resolution.reason)
    elif resolution.delivery == "merge":
        _record(provider, "merge", reason=resolution.reason)
    else:
        _record(provider, "fallback", reason=resolution.reason)
    return resolution


async def resolve_media_url(value: str, *, max_bytes: int) -> MediaResolution:
    source = _clean_input_url(value)
    canonical = canonicalize_media_url(source)
    provider = provider_for_url(canonical)
    identity = media_url_identity(canonical)

    if not provider:
        return MediaResolution(
            source_url=source,
            canonical_url=canonical,
            identity=identity,
            provider="",
            delivery="link",
            reason="unsupported_provider",
        )

    if not provider_allowed(provider):
        _record(provider, "fallback", reason="provider_disabled")
        return MediaResolution(
            source_url=source,
            canonical_url=canonical,
            identity=identity,
            provider=provider,
            delivery="link",
            reason="provider_disabled",
        )

    if provider == "direct":
        if is_safe_media_download_url(canonical):
            parsed = urlsplit(canonical)
            lower_path = str(parsed.path or "").lower()
            manifest_ext = next(
                (ext for ext in _MANIFEST_EXTENSIONS if lower_path.endswith(ext)),
                "",
            )
            if manifest_ext:
                return MediaResolution(
                    source_url=source,
                    canonical_url=canonical,
                    identity=identity,
                    provider=provider,
                    delivery="manifest",
                    media_url=canonical,
                    protocol="m3u8_native" if manifest_ext == ".m3u8" else "http_dash_segments",
                    ext=manifest_ext.lstrip("."),
                    reason="manifest_requires_controlled_transcode_or_player",
                )
            return MediaResolution(
                source_url=source,
                canonical_url=canonical,
                identity=identity,
                provider=provider,
                delivery="progressive",
                media_url=canonical,
                protocol=str(parsed.scheme or "").lower(),
                ext=_path_extension(parsed).lstrip("."),
            )
        return MediaResolution(
            source_url=source,
            canonical_url=canonical,
            identity=identity,
            provider=provider,
            delivery="link",
            reason="unsafe_direct_media_url",
        )

    cache_key = (identity, max(0, int(max_bytes or 0)))
    now = time.monotonic()
    _prune_cache(now)
    cached = _RESOLUTION_CACHE.get(cache_key)
    if cached is not None and now < float(cached[0]):
        _record(provider, "cache_hits")
        return cached[1]

    _ensure_async_state()
    existing = _RESOLVER_INFLIGHT.get(cache_key)
    if existing is not None and not existing.done():
        _record(provider, "coalesced")
        return await asyncio.shield(existing)

    task = asyncio.create_task(
        _resolve_extracted(source, canonical, provider, int(max_bytes or 0)),
        name=f"dank-share-media-{provider}",
    )
    _RESOLVER_INFLIGHT[cache_key] = task

    def _cleanup(finished: asyncio.Task[MediaResolution]) -> None:
        if _RESOLVER_INFLIGHT.get(cache_key) is finished:
            _RESOLVER_INFLIGHT.pop(cache_key, None)

    task.add_done_callback(_cleanup)
    resolution = await asyncio.shield(task)
    ttl = (
        _POSITIVE_CACHE_TTL_SECONDS
        if resolution.delivery in {"progressive", "manifest", "merge"}
        else _NEGATIVE_CACHE_TTL_SECONDS
    )
    _RESOLUTION_CACHE[cache_key] = (time.monotonic() + ttl, resolution)
    _prune_cache()
    return resolution


async def resolve_first_media(text: str, *, max_bytes: int) -> Optional[MediaResolution]:
    fallback: Optional[MediaResolution] = None
    seen: set[str] = set()
    for raw in MEDIA_URL_RE.findall(text or ""):
        canonical = canonicalize_media_url(raw)
        identity = media_url_identity(canonical)
        if not identity or identity in seen:
            continue
        seen.add(identity)
        if not provider_for_url(canonical):
            continue
        resolution = await resolve_media_url(canonical, max_bytes=max_bytes)
        if resolution.progressive:
            return resolution
        if fallback is None:
            fallback = resolution
    return fallback


def media_resolver_snapshot() -> dict[str, Any]:
    _prune_cache()
    return {
        "cache_entries": len(_RESOLUTION_CACHE),
        "inflight": sum(1 for task in _RESOLVER_INFLIGHT.values() if not task.done()),
        "extract_concurrency": _extract_concurrency(),
        "policy": media_provider_policy(),
        "providers": {
            key: {
                "attempts": int(value.get("attempts", 0) or 0),
                "progressive": int(value.get("progressive", 0) or 0),
                "manifest": int(value.get("manifest", 0) or 0),
                "merge": int(value.get("merge", 0) or 0),
                "fallback": int(value.get("fallback", 0) or 0),
                "cache_hits": int(value.get("cache_hits", 0) or 0),
                "coalesced": int(value.get("coalesced", 0) or 0),
                "last_reason": _safe_str(value.get("last_reason"))[:120],
            }
            for key, value in sorted(_PROVIDER_HEALTH.items())
        },
    }


def reset_media_resolver_state_for_tests() -> None:
    global _RESOLVER_LOOP
    global _RESOLVER_SEMAPHORE

    _RESOLVER_LOOP = None
    _RESOLVER_SEMAPHORE = None
    _RESOLVER_INFLIGHT.clear()
    _RESOLUTION_CACHE.clear()
    _PROVIDER_HEALTH.clear()


__all__ = [
    "MEDIA_URL_RE",
    "MediaProvider",
    "MediaResolution",
    "PROVIDERS",
    "canonicalize_media_url",
    "is_public_address",
    "is_safe_media_download_url",
    "media_provider_policy",
    "media_resolver_snapshot",
    "media_url_identity",
    "provider_allowed",
    "provider_for_url",
    "provider_label",
    "reset_media_resolver_state_for_tests",
    "resolve_first_media",
    "resolve_media_url",
    "select_media_resolution",
]
