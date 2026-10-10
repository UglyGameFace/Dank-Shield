"""Subtitle provider routing for Dank Cinema.

Providers: SubSource (API v1), Wyzie Subs and optional licensed SubDL.
Each credential is assigned to a specific authorized guild (or explicitly
operator-wide). One credential per provider is chosen deterministically per
guild. Exhaustion NEVER rotates to another credential of the same provider.
Fallback is between independent subtitle providers, respecting their limits.
"""
from __future__ import annotations

import asyncio
import hashlib
import io
import json
import os
import re
import secrets
import time
import zipfile
from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import parse_qs, parse_qsl, urlencode, urlsplit, urlunsplit

import aiohttp

from .cinema_subtitle_providers import (
    SubtitleProviderUnavailable, _assert_paid, _parse_results,
    _media_query, _safe_download_url,
)
from .cinema_subtitles import srt_to_vtt

_PROVIDERS = ("subsource", "wyzie", "subdl")
_BASES = {
    "subsource": "https://api.subsource.net/api/v1",
    "wyzie": "https://sub.wyzie.io",
    "subdl": "https://api.subdl.com",
}
_ALLOWED_ID = re.compile(r"^[A-Za-z0-9_-]{1,96}$")
_LANG = re.compile(r"^[a-z]{2,3}(?:-[a-z]{2,8})?$")
_OPTIONS: dict[str, tuple[float, dict]] = {}
_RESULTS_CACHE: dict[tuple, tuple[float, list[dict]]] = {}
_VTT_CACHE: dict[tuple, tuple[float, bytes]] = {}
_WINDOWS: dict[tuple, int] = {}
_COOLDOWN: dict[str, float] = {}
_SEARCH_INFLIGHT: dict[tuple, asyncio.Task] = {}
_DOWNLOAD_INFLIGHT: dict[tuple, asyncio.Task] = {}
_MAX_FILE = 1_000_000
_TIMEOUT = aiohttp.ClientTimeout(total=12, connect=5)


@dataclass(frozen=True)
class SubtitleAccount:
    provider: str
    name: str
    key: str
    guild_ids: frozenset[int]
    global_scope: bool = False

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256((self.provider + ":" + self.key).encode()).hexdigest()


def configured_accounts() -> tuple[SubtitleAccount, ...]:
    """Read private operator settings, without logging or exposing credentials."""
    raw = os.getenv("DANK_CINEMA_SUBTITLE_ACCOUNTS_JSON", "").strip()
    entries: list[object] = []
    if raw:
        try:
            payload = json.loads(raw)
            if isinstance(payload, list):
                entries = payload[:60]
        except ValueError:
            entries = []
    accounts: list[SubtitleAccount] = []
    seen: set[tuple[str, str]] = set()
    for item in entries:
        if not isinstance(item, Mapping):
            continue
        provider = str(item.get("provider") or "").strip().lower()
        name = str(item.get("name") or "").strip()
        key = str(item.get("key") or "").strip()
        if (
            provider not in _PROVIDERS
            or not re.fullmatch(r"[A-Za-z0-9_-]{2,40}", name)
            or not 8 <= len(key) <= 256
            or "\n" in key or "\r" in key
            or (provider, name) in seen
        ):
            continue
        raw_guilds = item.get("guild_ids") or []
        guilds: set[int] = set()
        if isinstance(raw_guilds, list):
            for raw_id in raw_guilds[:200]:
                value = str(raw_id)
                if value.isascii() and value.isdigit() and 0 < int(value) < 2**64:
                    guilds.add(int(value))
        global_scope = item.get("scope") == "operator"
        if not guilds and not global_scope:
            continue
        if provider == "subdl" and item.get("licensed_commercial") is not True:
            continue
        seen.add((provider, name))
        accounts.append(SubtitleAccount(
            provider=provider, name=name, key=key,
            guild_ids=frozenset(guilds), global_scope=global_scope,
        ))
    # Backwards compatibility with the first SubDL implementation.
    legacy = os.getenv("DANK_CINEMA_SUBDL_API_KEY", "").strip()
    consent = os.getenv("DANK_CINEMA_SUBDL_COMMERCIAL_OK", "").strip().lower()
    if legacy and consent in {"true", "1", "yes"} and not any(
        account.provider == "subdl" and account.global_scope for account in accounts
    ):
        accounts.append(SubtitleAccount(
            provider="subdl", name="legacy-operator", key=legacy,
            guild_ids=frozenset(), global_scope=True,
        ))
    return tuple(accounts)


def account_for(guild_id: int, provider: str) -> SubtitleAccount | None:
    """No same-provider key rotation after errors or quota exhaustion."""
    matches = [
        account for account in configured_accounts()
        if account.provider == provider and (
            guild_id in account.guild_ids or account.global_scope
        )
    ]
    matches.sort(key=lambda a: (guild_id not in a.guild_ids, a.name))
    return matches[0] if matches else None


def provider_configured(guild_id: int) -> bool:
    return any(account_for(guild_id, source) for source in _PROVIDERS)


def provider_names(guild_id: int) -> list[str]:
    return [provider for provider in _PROVIDERS if account_for(guild_id, provider)]


def _budget(account: SubtitleAccount, *, cost: int = 1) -> None:
    """Small conservative in-process budget. Provider remains quota authority."""
    now = time.time()
    fingerprint = account.fingerprint
    if _COOLDOWN.get(fingerprint, 0) > now:
        raise SubtitleProviderUnavailable("Subtitle account temporarily unavailable.")
    # Paid SubDL validates its own two quota buckets server-side.
    bounds = {
        "subsource": ((60, 55), (3600, 1650), (86400, 6900)),
        "wyzie": ((86400, 950),),
        "subdl": ((60, 50),),
    }
    windows = bounds[account.provider]
    for seconds, ceiling in windows:
        bucket = int(now // seconds)
        if _WINDOWS.get((fingerprint, seconds, bucket), 0) + cost > ceiling:
            raise SubtitleProviderUnavailable("Subtitle account request budget exhausted.")
    for seconds, _ceiling in windows:
        bucket = int(now // seconds)
        tag = (fingerprint, seconds, bucket)
        _WINDOWS[tag] = _WINDOWS.get(tag, 0) + cost
    if len(_WINDOWS) > 4096:
        # Keep active counters. Clearing them would effectively reset an
        # account's allowance during a busy day.
        for old in list(_WINDOWS):
            _fingerprint, window_size, bucket = old
            if bucket < int(now // window_size) - 1:
                _WINDOWS.pop(old, None)


def _handle_response(response: aiohttp.ClientResponse, account: SubtitleAccount) -> None:
    if response.status in {402, 403, 429}:
        # A provider quota/hold is authoritative; don't try alternate keys
        # on this provider to bypass the restriction.
        cooldown = 86400 if response.status == 429 else 1800
        if response.status == 429:
            try:
                cooldown = min(86400, max(60, int(response.headers.get("Retry-After", "3600"))))
            except ValueError:
                cooldown = 3600
        _COOLDOWN[account.fingerprint] = time.time() + cooldown
        raise SubtitleProviderUnavailable("Subtitle provider quota or access restriction.")
    if response.status in {400, 404}:
        raise SubtitleProviderUnavailable("No suitable subtitles found.")
    if response.status != 200:
        raise SubtitleProviderUnavailable("Subtitle provider temporarily unavailable.")


async def _json_request(
    session: aiohttp.ClientSession, account: SubtitleAccount,
    path: str, *, params: dict | None = None,
) -> object:
    _budget(account)
    headers = {"Accept": "application/json"}
    if account.provider == "subsource":
        headers["X-API-Key"] = account.key
    elif account.provider == "subdl":
        headers["Authorization"] = "Bearer " + account.key
    else:
        params = {**(params or {}), "key": account.key}
    async with session.get(
        _BASES[account.provider] + path,
        params=params, headers=headers, allow_redirects=False,
    ) as response:
        _handle_response(response, account)
        if response.content_length and response.content_length > 350_000:
            raise SubtitleProviderUnavailable("Subtitle search payload too large.")
        data = await response.content.read(350_001)
        if len(data) > 350_000:
            raise SubtitleProviderUnavailable("Subtitle search payload too large.")
        try:
            return json.loads(data)
        except (UnicodeError, ValueError) as exc:
            raise SubtitleProviderUnavailable("Invalid subtitle API response.") from exc


def _safe_wyzie_url(value: object) -> str:
    raw = str(value or "")
    try:
        parsed = urlsplit(raw)
        port_ok = parsed.port in {None, 443}
    except ValueError as exc:
        raise SubtitleProviderUnavailable("Invalid Wyzie download URL.") from exc
    if not (
        parsed.scheme == "https" and parsed.hostname == "sub.wyzie.io"
        and port_ok and parsed.username is None and parsed.password is None
        and parsed.path.startswith("/c/") and len(parsed.path) <= 320
        and parse_qs(parsed.query).get("tok")
    ):
        raise SubtitleProviderUnavailable("Wyzie returned a disallowed download URL.")
    return raw


def _parse_wyzie(payload: object, *, language: str) -> list[dict]:
    rows = payload if isinstance(payload, list) else (
        payload.get("subtitles", payload.get("results", []))
        if isinstance(payload, Mapping) else []
    )
    result: list[dict] = []
    if not isinstance(rows, list):
        return result
    for row in rows[:40]:
        if not isinstance(row, Mapping):
            continue
        if str(row.get("language") or "").lower() != language:
            continue
        try:
            url = _safe_wyzie_url(row.get("url"))
        except SubtitleProviderUnavailable:
            continue
        release = str(row.get("release") or row.get("fileName") or "").strip()[:90]
        result.append({
            "source": "wyzie", "reference": url,
            "language": language,
            "label": ("Wyzie • " + (release or language.upper()))[:116],
        })
        if len(result) >= 10:
            break
    return result


def _subsource_rows(payload: object) -> list[dict]:
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if isinstance(payload, Mapping):
        for name in ("data", "results", "subtitles", "movies"):
            rows = payload.get(name)
            if isinstance(rows, list):
                return [x for x in rows if isinstance(x, dict)]
            if isinstance(rows, Mapping):
                inner = rows.get("data") or rows.get("results")
                if isinstance(inner, list):
                    return [x for x in inner if isinstance(x, dict)]
    return []


async def _search_subsource(
    session: aiohttp.ClientSession, account: SubtitleAccount,
    metadata: Mapping, language: str,
) -> list[dict]:
    title = str((metadata.get("series_title") or metadata.get("title"))
                if metadata.get("media_type") == "episode"
                else metadata.get("title") or "").strip()[:120]
    if len(title) < 2:
        return []
    # SubSource uses an internal movie identifier; never treat TMDB ID as its ID.
    titles = await _json_request(
        session, account, "/movies/search", params={"searchType": "text", "q": title.lower()},
    )
    movie_id = None
    year = int(metadata.get("year") or 0)
    for item in _subsource_rows(titles)[:20]:
        name = str(item.get("title") or item.get("name") or "").casefold()
        if name != title.casefold():
            continue
        candidate_year = int(item.get("releaseYear") or item.get("year") or 0)
        if year and candidate_year and abs(year - candidate_year) > 1:
            continue
        raw = str(item.get("movieId") or item.get("id") or "")
        if raw.isdigit():
            movie_id = int(raw)
            break
    if movie_id is None:
        return []
    # SubSource lists one language per request.
    lang_names = {"en": "english", "es": "spanish", "fr": "french",
                  "ja": "japanese", "de": "german", "it": "italian",
                  "pt": "portuguese", "ko": "korean", "zh": "chinese"}
    params = {"movieId": str(movie_id), "language": lang_names.get(language, language), "limit": "100"}
    season = int(metadata.get("season_number") or 0)
    episode = int(metadata.get("episode_number") or 0)
    if metadata.get("media_type") == "episode":
        params.update({"seasonNumber": str(season), "episodeNumber": str(episode)})
    payload = await _json_request(session, account, "/subtitles", params=params)
    rows = []
    for item in _subsource_rows(payload)[:80]:
        raw_id = str(item.get("subtitleId") or item.get("id") or "")
        if not raw_id.isdigit():
            continue
        release_raw = item.get("releaseInfo") or item.get("release_name") or item.get("release") or item.get("name") or ""
        release = " ".join(str(part) for part in release_raw) if isinstance(release_raw, list) else str(release_raw)
        if season or episode:
            pattern = rf"(?i)(?:s0*{season}e0*{episode}|(?<!\d){season}x0*{episode}(?!\d))"
            if not re.search(pattern, release):
                continue
        rows.append({
            "source": "subsource", "reference": raw_id,
            "language": language,
            "label": ("SubSource • " + (release[:88] or language.upper()))[:116],
        })
        if len(rows) >= 10:
            break
    return rows


async def _search_wyzie(
    session: aiohttp.ClientSession, account: SubtitleAccount,
    metadata: Mapping, language: str,
) -> list[dict]:
    query = _media_query(metadata)
    if not query or len(language) != 2:
        return []
    _type, tmdb, season, episode = query
    params = {"id": str(tmdb), "language": language, "limit": "25"}
    if _type == "tv":
        params.update({"season": str(season), "episode": str(episode)})
    payload = await _json_request(session, account, "/search", params=params)
    return _parse_wyzie(payload, language=language)


async def _search_subdl(
    session: aiohttp.ClientSession, account: SubtitleAccount,
    metadata: Mapping, language: str,
) -> list[dict]:
    query = _media_query(metadata)
    if not query:
        return []
    _type, tmdb, season, episode = query
    await _assert_paid(session, account.key)
    params = {"tmdb_id": str(tmdb), "type": _type,
              "languages": language, "unpack": "1"}
    if _type == "tv":
        params.update({"season": str(season), "episode": str(episode)})
    payload = await _json_request(session, account, "/api/v2/subtitles/search", params=params)
    return [
        {
            "source": "subdl", "reference": str(row["id"]),
            "language": str(row["language"]), "label": str(row["label"]),
        }
        for row in _parse_results(payload, language=language)
    ]


async def _discover(account: SubtitleAccount, metadata: Mapping, language: str) -> list[dict]:
    query = _media_query(metadata)
    if query is None:
        return []
    identity = (*query, language)
    cache_key = (account.fingerprint, identity)
    cached = _RESULTS_CACHE.get(cache_key)
    if cached and cached[0] > time.monotonic():
        return cached[1]
    # One upstream search per provider/credential/title/language, even if all
    # twenty Watch Party viewers click Find subtitles simultaneously.
    pending_key = (asyncio.get_running_loop(), cache_key)
    pending = _SEARCH_INFLIGHT.get(pending_key)
    if pending is not None:
        return await pending

    async def perform() -> list[dict]:
        async with aiohttp.ClientSession(timeout=_TIMEOUT) as session:
            if account.provider == "wyzie":
                rows = await _search_wyzie(session, account, metadata, language)
            elif account.provider == "subsource":
                rows = await _search_subsource(session, account, metadata, language)
            else:
                rows = await _search_subdl(session, account, metadata, language)
        if len(_RESULTS_CACHE) >= 90:
            _RESULTS_CACHE.clear()
        _RESULTS_CACHE[cache_key] = (time.monotonic() + 600, rows)
        return rows

    task = asyncio.create_task(perform())
    _SEARCH_INFLIGHT[pending_key] = task
    task.add_done_callback(
        lambda finished, k=pending_key: (
            _SEARCH_INFLIGHT.pop(k, None)
            if _SEARCH_INFLIGHT.get(k) is finished else None
        )
    )
    return await asyncio.shield(task)


async def discover_subtitles(
    metadata: Mapping, language: str, *, guild_id: int,
    viewer_id: int, stream_token: str,
) -> list[dict]:
    """Search authorized provider accounts with cross-provider fallback."""
    if not stream_token or not guild_id or not viewer_id:
        return []
    lang = str(language or "").strip().lower()
    if not _LANG.fullmatch(lang) or _media_query(metadata) is None:
        return []
    # Independent providers run concurrently. One failing source cannot
    # delay or abort results from the other two.
    assigned = [
        (provider, account_for(guild_id, provider)) for provider in _PROVIDERS
    ]
    assigned = [(provider, account) for provider, account in assigned if account]

    async def query_provider(provider: str, account: SubtitleAccount) -> tuple[str, SubtitleAccount, list[dict]]:
        try:
            rows = await _discover(account, metadata, lang)
        except (SubtitleProviderUnavailable, TimeoutError, aiohttp.ClientError, OSError):
            return provider, account, []
        return provider, account, rows

    responses = await asyncio.gather(*[
        query_provider(provider, account) for provider, account in assigned
    ])
    matches: list[dict] = []
    for provider, account, rows in responses:
        per_provider_count = 0
        for row in rows:
            if len(matches) >= 15 or per_provider_count >= 5:
                break
            ref = str(row.get("reference") or "")
            if not ref or len(ref) > 2048:
                continue
            opaque_id = secrets.token_urlsafe(18)
            if len(_OPTIONS) >= 512:
                now = time.monotonic()
                for old, (expiry, _) in list(_OPTIONS.items()):
                    if expiry <= now:
                        _OPTIONS.pop(old, None)
                if len(_OPTIONS) >= 512:
                    oldest = min(_OPTIONS, key=lambda i: _OPTIONS[i][0])
                    _OPTIONS.pop(oldest, None)
            _OPTIONS[opaque_id] = (
                time.monotonic() + 900, {
                    "account": account, "provider": provider,
                    "reference": ref, "stream_token": stream_token,
                    "guild_id": int(guild_id), "viewer_id": int(viewer_id),
                    "media": _media_query(metadata),
                },
            )
            matches.append({
                "id": opaque_id, "source": provider,
                "language": lang, "label": str(row.get("label") or "")[:116],
            })
            per_provider_count += 1
    return matches


def lookup_selection(
    selection_id: str, *, guild_id: int, viewer_id: int,
    stream_token: str,
) -> dict:
    candidate = _OPTIONS.get(str(selection_id or ""))
    if candidate is None or candidate[0] <= time.monotonic():
        raise SubtitleProviderUnavailable("Subtitle selection expired; search again.")
    item = candidate[1]
    if not (
        item["guild_id"] == int(guild_id)
        and item["viewer_id"] == int(viewer_id)
        and item["stream_token"] == str(stream_token)
        and item["account"] == account_for(guild_id, item["provider"])
    ):
        raise SubtitleProviderUnavailable("Subtitle selection not authorized.")
    return item


async def _binary_get(
    session: aiohttp.ClientSession, account: SubtitleAccount,
    url: str, *, headers: dict[str, str] | None = None,
) -> bytes:
    _budget(account)
    async with session.get(url, headers=headers or {}, allow_redirects=False) as response:
        _handle_response(response, account)
        if response.content_length and response.content_length > _MAX_FILE:
            raise SubtitleProviderUnavailable("Subtitle file too large.")
        raw = await response.content.read(_MAX_FILE + 1)
        if len(raw) > _MAX_FILE:
            raise SubtitleProviderUnavailable("Subtitle file too large.")
        return raw


def _zip_to_vtt(raw: bytes, *, media: tuple) -> bytes:
    try:
        archive = zipfile.ZipFile(io.BytesIO(raw))
    except (OSError, ValueError, zipfile.BadZipFile) as exc:
        raise SubtitleProviderUnavailable("Invalid subtitle archive.") from exc
    _media_type, _tmdb, season, episode = media
    valid = []
    for info in archive.infolist()[:30]:
        name = info.filename.replace("\\", "/")
        if (info.is_dir() or info.flag_bits & 1 or
            name.startswith("/") or ".." in name.split("/") or
            info.file_size > _MAX_FILE or
            not name.lower().endswith((".srt", ".vtt"))):
            continue
        if _media_type == "tv" and not re.search(
            rf"(?i)(?:s0*{season}e0*{episode}|(?<!\d){season}x0*{episode}(?!\d))",
            name,
        ):
            continue
        valid.append(info)
    if len(valid) != 1:
        raise SubtitleProviderUnavailable("Archive does not identify one exact subtitle file.")
    data = archive.read(valid[0], pwd=None)
    if len(data) > _MAX_FILE:
        raise SubtitleProviderUnavailable("Subtitle archive expanded beyond safe limit.")
    return _as_vtt(data)


def _as_vtt(raw: bytes) -> bytes:
    if not raw or len(raw) > _MAX_FILE:
        raise SubtitleProviderUnavailable("Subtitle file unavailable or too large.")
    if raw.startswith(b"PK"):
        raise SubtitleProviderUnavailable("Subtitle archive requires safe extraction.")
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
    if raw.startswith(b"WEBVTT"):
        return raw
    try:
        return srt_to_vtt(raw, max_bytes=_MAX_FILE).encode("utf-8")
    except (UnicodeError, ValueError) as exc:
        raise SubtitleProviderUnavailable("Unsupported subtitle text format.") from exc


async def _download_selection_uncached(
    selection_id: str, *, guild_id: int, viewer_id: int,
    stream_token: str,
) -> bytes:
    item = lookup_selection(selection_id, guild_id=guild_id,
                            viewer_id=viewer_id, stream_token=stream_token)
    account: SubtitleAccount = item["account"]
    provider, ref = item["provider"], item["reference"]
    cache_key = (account.fingerprint, item["media"], provider, ref)
    cached = _VTT_CACHE.get(cache_key)
    if cached and cached[0] > time.monotonic():
        return cached[1]
    async with aiohttp.ClientSession(timeout=_TIMEOUT) as session:
        if provider == "wyzie":
            url = _safe_wyzie_url(ref)
            parts = urlsplit(url)
            params = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
                      if k != "to"]
            params.append(("to", "vtt"))
            vtt_url = urlunsplit((parts.scheme, parts.netloc, parts.path,
                                  urlencode(params), ""))
            raw = await _binary_get(session, account, vtt_url,
                                    headers={"Accept": "text/vtt"})
            result = _as_vtt(raw)
        elif provider == "subsource":
            if not ref.isdigit():
                raise SubtitleProviderUnavailable("Invalid SubSource subtitle ID.")
            raw = await _binary_get(
                session, account,
                _BASES["subsource"] + "/subtitles/" + ref + "/download",
                headers={"X-API-Key": account.key},
            )
            result = _zip_to_vtt(raw, media=item["media"]) if raw.startswith(b"PK") else _as_vtt(raw)
        else:
            if not _ALLOWED_ID.fullmatch(ref):
                raise SubtitleProviderUnavailable("Invalid SubDL subtitle ID.")
            await _assert_paid(session, account.key)
            _budget(account)
            headers = {"Authorization": "Bearer " + account.key}
            url = _BASES["subdl"] + "/api/v2/subtitles/" + ref + "/download?format=file"
            async with session.get(url, headers=headers, allow_redirects=False) as response:
                if response.status in {301, 302, 303, 307, 308}:
                    target = _safe_download_url(response.headers.get("Location"))
                    raw = await _binary_get(session, account, target)
                else:
                    _handle_response(response, account)
                    raw = await response.content.read(_MAX_FILE + 1)
                    if len(raw) > _MAX_FILE:
                        raise SubtitleProviderUnavailable("Subtitle file too large.")
            result = _zip_to_vtt(raw, media=item["media"]) if raw.startswith(b"PK") else _as_vtt(raw)
    if len(_VTT_CACHE) >= 24:
        _VTT_CACHE.clear()
    _VTT_CACHE[cache_key] = (time.monotonic() + 1800, result)
    return result


async def download_subtitle_selection(
    selection_id: str, *, guild_id: int, viewer_id: int,
    stream_token: str,
) -> bytes:
    """Authenticate every viewer, but share one provider download per file."""
    selected = lookup_selection(
        selection_id, guild_id=guild_id,
        viewer_id=viewer_id, stream_token=stream_token,
    )
    account = selected["account"]
    key = (
        account.fingerprint, selected["media"],
        selected["provider"], selected["reference"],
    )
    cached = _VTT_CACHE.get(key)
    if cached and cached[0] > time.monotonic():
        return cached[1]
    task_key = (asyncio.get_running_loop(), key)
    task = _DOWNLOAD_INFLIGHT.get(task_key)
    if task is None:
        task = asyncio.create_task(_download_selection_uncached(
            selection_id, guild_id=guild_id,
            viewer_id=viewer_id, stream_token=stream_token,
        ))
        _DOWNLOAD_INFLIGHT[task_key] = task
        task.add_done_callback(
            lambda finished, k=task_key: (
                _DOWNLOAD_INFLIGHT.pop(k, None)
                if _DOWNLOAD_INFLIGHT.get(k) is finished else None
            )
        )
    return await asyncio.shield(task)
