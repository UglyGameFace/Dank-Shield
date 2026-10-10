"""Optional licensed SubDL subtitle discovery for Dank Cinema.

A shared public bot must not pool users' free keys. This adapter activates only
when the operator supplies their own authorized paid API key and opts in.
The key never reaches a browser response, log, or query-string URL.
"""
from __future__ import annotations

import asyncio
import hashlib
import os
import re
import time
from collections.abc import Mapping
from urllib.parse import urlsplit

import aiohttp

from .cinema_subtitles import srt_to_vtt

_API = "https://api.subdl.com"
_ID = re.compile(r"^[A-Za-z0-9_-]{2,64}$")
_LANGUAGE = re.compile(r"^[a-z]{2,3}(?:-[a-z]{2,8})?$")
_HEADERS = {"Accept": "application/json"}
_PAID_CHECK: dict[str, tuple[float, bool]] = {}
_SEARCH_CACHE: dict[tuple[str, str, str, int, int, int], tuple[float, list[dict]]] = {}
_DOWNLOAD_CACHE: dict[tuple[str, str], tuple[float, bytes]] = {}
_ELIGIBLE_IDS: dict[tuple[str, str], float] = {}


class SubtitleProviderUnavailable(RuntimeError):
    """Provider disabled, quota-exhausted, or temporarily unreachable."""


def _operator_key() -> str:
    approved = str(os.getenv("DANK_CINEMA_SUBDL_COMMERCIAL_OK", "")).strip().lower()
    if approved not in {"1", "yes", "true"}:
        return ""
    return str(os.getenv("DANK_CINEMA_SUBDL_API_KEY", "") or "").strip()


def provider_configured() -> bool:
    return bool(_operator_key())


def _key_hash(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


async def _assert_paid(session: aiohttp.ClientSession, key: str) -> None:
    fingerprint = _key_hash(key)
    cached = _PAID_CHECK.get(fingerprint)
    if cached and cached[0] > time.monotonic():
        if not cached[1]:
            raise SubtitleProviderUnavailable("SubDL requires an authorized paid API plan.")
        return
    async with session.get(_API + "/api/v2/me", headers={
        **_HEADERS, "Authorization": f"Bearer {key}",
    }, allow_redirects=False) as response:
        if response.status != 200:
            raise SubtitleProviderUnavailable("SubDL account validation failed.")
        payload = await response.json(content_type=None)
    plan = payload.get("plan") if isinstance(payload, Mapping) else {}
    paid = bool(isinstance(plan, Mapping) and (
        plan.get("is_pro") or str(plan.get("name") or "").strip().lower()
        in {"pro", "ultra"}
    ))
    if len(_PAID_CHECK) > 16:
        _PAID_CHECK.clear()
    _PAID_CHECK[fingerprint] = (time.monotonic() + 900, paid)
    if not paid:
        raise SubtitleProviderUnavailable("SubDL requires an authorized paid API plan.")


def _media_query(metadata: Mapping) -> tuple[str, int, int, int] | None:
    media_type = str(metadata.get("media_type") or "").lower()
    try:
        if media_type in {"episode", "tv"}:
            title_id = int(
                metadata.get("series_id") or (
                    metadata.get("tmdb_id") if media_type == "tv" else 0
                ) or 0
            )
            season = int(metadata.get("season_number") or 0)
            episode = int(metadata.get("episode_number") or 0)
            if title_id <= 0 or not 0 <= season <= 100 or not 1 <= episode <= 300:
                return None
            return "tv", title_id, season, episode
        if media_type == "movie":
            title_id = int(metadata.get("tmdb_id") or 0)
            if title_id <= 0:
                return None
            return "movie", title_id, 0, 0
    except (TypeError, ValueError):
        return None
    return None


def _parse_results(payload: object, *, language: str) -> list[dict]:
    if not isinstance(payload, Mapping):
        return []
    rows = payload.get("subtitles")
    if not isinstance(rows, list):
        return []
    options: list[dict] = []
    for item in rows[:60]:
        if not isinstance(item, Mapping):
            continue
        n_id = str(item.get("n_id") or item.get("id") or "")
        if not _ID.fullmatch(n_id):
            continue
        lang = str(item.get("lang") or item.get("language") or language).strip().lower()
        language_names = {
            "en": {"en", "eng", "english"},
            "es": {"es", "spa", "spanish"},
            "fr": {"fr", "fra", "fre", "french"},
            "ja": {"ja", "jpn", "japanese"},
            "de": {"de", "deu", "ger", "german"},
            "it": {"it", "ita", "italian"},
            "pt": {"pt", "por", "portuguese"},
            "ko": {"ko", "kor", "korean"},
            "zh": {"zh", "zho", "chi", "chinese"},
        }
        if lang not in language_names.get(language, {language}):
            continue
        release = str(item.get("release_name") or item.get("name") or "").strip()[:96]
        options.append({
            "id": n_id,
            "language": language,
            "label": ("SubDL • " + (release or language.upper()))[:116],
            "source": "subdl",
        })
        if len(options) >= 12:
            break
    return options


async def search_subdl(metadata: Mapping, language: str, *, stream_token: str) -> list[dict]:
    key = _operator_key()
    if not key:
        return []
    lang = str(language or "").strip().lower()
    query = _media_query(metadata)
    if not _LANGUAGE.fullmatch(lang) or query is None or not stream_token:
        return []
    media_type, tmdb_id, season, episode = query
    fingerprint = _key_hash(key)
    cache_key = (fingerprint, media_type, lang, tmdb_id, season, episode)
    cached = _SEARCH_CACHE.get(cache_key)
    if cached and cached[0] > time.monotonic():
        results = cached[1]
    else:
        timeout = aiohttp.ClientTimeout(total=10, connect=5)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            await _assert_paid(session, key)
            params = {
                "tmdb_id": str(tmdb_id), "type": media_type,
                "languages": lang, "unpack": "1",
            }
            if media_type == "tv":
                params.update({"season": str(season), "episode": str(episode)})
            async with session.get(
                _API + "/api/v2/subtitles/search",
                params=params, headers={**_HEADERS, "Authorization": f"Bearer {key}"},
                allow_redirects=False,
            ) as response:
                if response.status == 429:
                    raise SubtitleProviderUnavailable("SubDL daily quota exhausted.")
                if response.status != 200:
                    raise SubtitleProviderUnavailable("SubDL subtitle search unavailable.")
                payload = await response.json(content_type=None)
        results = _parse_results(payload, language=lang)
        if len(_SEARCH_CACHE) >= 64:
            _SEARCH_CACHE.clear()
        _SEARCH_CACHE[cache_key] = (time.monotonic() + 600, results)
    for option in results:
        if len(_ELIGIBLE_IDS) >= 128:
            _ELIGIBLE_IDS.clear()
        _ELIGIBLE_IDS[(stream_token, str(option["id"]))] = time.monotonic() + 900
    return list(results)


def allowed_subdl_id(stream_token: str, n_id: str) -> bool:
    return bool(
        _ID.fullmatch(str(n_id or ""))
        and _ELIGIBLE_IDS.get((str(stream_token), str(n_id)), 0) > time.monotonic()
    )


def _safe_download_url(url: object) -> str:
    raw = str(url or "").strip()
    try:
        parsed = urlsplit(raw)
        safe_port = parsed.port in {None, 443}
    except ValueError as exc:
        raise SubtitleProviderUnavailable("SubDL returned an invalid subtitle URL.") from exc
    if (
        parsed.scheme != "https"
        or parsed.hostname != "dl.subdl.com"
        or parsed.username is not None
        or parsed.password is not None
        or not safe_port
        or not parsed.path.startswith("/subtitle/")
    ):
        raise SubtitleProviderUnavailable("SubDL returned an unsafe subtitle URL.")
    return raw


async def _read_limited_response(response: aiohttp.ClientResponse) -> bytes:
    if response.status == 429:
        raise SubtitleProviderUnavailable("SubDL daily download quota exhausted.")
    if response.status != 200:
        raise SubtitleProviderUnavailable("SubDL subtitle download unavailable.")
    if response.content_length and response.content_length > 1_000_000:
        raise SubtitleProviderUnavailable("SubDL subtitle file is too large.")
    data = await response.content.read(1_000_001)
    if len(data) > 1_000_000:
        raise SubtitleProviderUnavailable("SubDL subtitle file is too large.")
    return data


async def download_subdl_vtt(stream_token: str, n_id: str) -> bytes:
    key = _operator_key()
    if not key or not allowed_subdl_id(stream_token, n_id):
        raise SubtitleProviderUnavailable("Subtitle selection expired. Search again.")
    cache_key = (_key_hash(key), n_id)
    cached = _DOWNLOAD_CACHE.get(cache_key)
    if cached and cached[0] > time.monotonic():
        return cached[1]
    timeout = aiohttp.ClientTimeout(total=14, connect=5)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        await _assert_paid(session, key)
        async with session.get(
            f"{_API}/api/v2/subtitles/{n_id}/download",
            params={"format": "file"},
            headers={"Authorization": f"Bearer {key}"},
            allow_redirects=False,
        ) as response:
            if response.status in {301, 302, 303, 307, 308}:
                target = _safe_download_url(response.headers.get("Location"))
                data = b""
            else:
                data = await _read_limited_response(response)
                if response.content_type == "application/json":
                    try:
                        import json
                        payload = json.loads(data)
                    except (ValueError, UnicodeError) as exc:
                        raise SubtitleProviderUnavailable(
                            "SubDL returned an invalid download response."
                        ) from exc
                    if not isinstance(payload, Mapping):
                        raise SubtitleProviderUnavailable("SubDL returned no subtitle.")
                    target = _safe_download_url(
                        payload.get("download_url") or payload.get("url")
                    )
                else:
                    target = ""
        if target:
            # Follow only SubDL's documented downloads host and do not forward
            # API authorization headers to that separate origin.
            async with session.get(
                target, headers={"Accept": "text/plain"},
                allow_redirects=False,
            ) as response:
                data = await _read_limited_response(response)
        # Only a raw single-file subtitle is supported; archives need safe
        # unpacking plus exact-episode checks before they can be offered.
        if data[:2] == b"PK":
            raise SubtitleProviderUnavailable("SubDL returned a subtitle archive.")
        if data.startswith(b"WEBVTT"):
            result = data
        else:
            try:
                result = srt_to_vtt(data).encode("utf-8")
            except (UnicodeError, ValueError) as exc:
                raise SubtitleProviderUnavailable(
                    "SubDL subtitle is not a compatible text file."
                ) from exc
    if len(_DOWNLOAD_CACHE) >= 12:
        _DOWNLOAD_CACHE.clear()
    _DOWNLOAD_CACHE[cache_key] = (time.monotonic() + 3600, result)
    return result
