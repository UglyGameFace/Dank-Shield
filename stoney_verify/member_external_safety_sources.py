from __future__ import annotations

"""Owner-requested external security evidence. No scanning or punishment.

Only explicitly documented, fixed-host provider endpoints are queried. Other
providers are represented as manual reviews until they publish a verifiable API.
Discord API data must not be used to construct social relationship profiles.
"""

import asyncio
from collections import OrderedDict
import os
import re
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote, urlsplit

import aiohttp


PROTECTOR_URL = "https://protector-bot.com/api/v1"
REVIEWS_URL = "https://api.discordreviews.com/v1"
CHEATERSTATS_URL = "https://api.cheaterstats.cc/v2"
DOMAIN_LIST_URL = "https://raw.githubusercontent.com/Discord-AntiScam/scam-links/main/list.txt"
_SNOWFLAKE = re.compile(r"^[0-9]{15,22}$")
_ACCOUNT_TTL_SECONDS = 900
_DOMAIN_TTL_SECONDS = 12 * 3600
_CACHE_LIMIT = 256
_account_cache: OrderedDict[tuple[str, str, str], tuple[float, "SourceFinding"]] = OrderedDict()
_domain_cache: tuple[float, frozenset[str]] | None = None
_domain_lock = asyncio.Lock()


@dataclass(frozen=True)
class SourceFinding:
    source: str
    status: str  # reported | none_reported | unavailable | manual_review
    detail: str
    url: str = ""

    def __post_init__(self) -> None:
        if self.status not in {"reported", "none_reported", "unavailable", "manual_review"}:
            raise ValueError("Unknown external source status")


def _key(name: str) -> str:
    return str(os.environ.get(name, "")).strip()


def _cache_get(source: str, uid: str, identity: str = "") -> SourceFinding | None:
    cache_key = (source, uid, identity)
    saved = _account_cache.get(cache_key)
    if not saved:
        return None
    age = max(0.0, time.monotonic() - saved[0])
    if age >= _ACCOUNT_TTL_SECONDS:
        _account_cache.pop(cache_key, None)
        return None
    _account_cache.move_to_end(cache_key)
    finding = saved[1]
    # Preserve the provider's actual result in storage. Only the returned
    # copy discloses its age. Never call cached negative evidence a clearance.
    return SourceFinding(
        finding.source, finding.status,
        finding.detail + f" Cached {int(age)}s ago; not a fresh provider check.",
        finding.url,
    )


def _cache_set(source: str, uid: str, identity: str, result: SourceFinding) -> SourceFinding:
    now = time.monotonic()
    for cache_key, (stored_at, _) in list(_account_cache.items()):
        if now - stored_at >= _ACCOUNT_TTL_SECONDS:
            _account_cache.pop(cache_key, None)
    cache_key = (source, uid, identity)
    _account_cache[cache_key] = (now, result)
    _account_cache.move_to_end(cache_key)
    # Evict only the oldest unused entries, not all previously cached
    # provider results (which could cause a burst of repeated API calls).
    while len(_account_cache) > _CACHE_LIMIT:
        _account_cache.popitem(last=False)
    return result


async def _get_json(
    session: aiohttp.ClientSession, url: str, *, api_key: str,
    params: dict[str, str] | None = None,
) -> dict[str, Any]:
    async with session.get(
        url,
        params=params,
        headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json"},
        allow_redirects=False,
    ) as response:
        if response.status != 200:
            # Do not log provider bodies: they might contain credentials.
            raise RuntimeError(f"HTTP {response.status}")
        if int(response.headers.get("Content-Length", "0")) > 262144:
            raise ValueError("Oversized response")
        data = await response.content.read(262145)
        if len(data) > 262144:
            raise ValueError("Oversized response")
        import json
        parsed = json.loads(data)
        if not isinstance(parsed, dict):
            raise ValueError("Unexpected JSON response")
        return parsed


async def _protector(session: aiohttp.ClientSession, uid: str) -> SourceFinding:
    name = "Protector"
    key = _key("DANK_PROTECTOR_API_KEY")
    if not key:
        return SourceFinding(name, "unavailable", "Set DANK_PROTECTOR_API_KEY to enable on-demand checks.")
    existing = _cache_get(name, uid)
    if existing:
        return existing
    try:
        data = await _get_json(session, f"{PROTECTOR_URL}/blacklist/{uid}", api_key=key)
        if str(data.get("user_id")) != uid or type(data.get("blacklisted")) is not bool:
            raise ValueError("Response does not verify this Discord ID")
        finding = SourceFinding(
            name, "reported" if data["blacklisted"] else "none_reported",
            "Blacklist flag reported by provider; no incident proof supplied."
            if data["blacklisted"] else "No blacklist flag returned; not a clearance.",
            "https://protector-bot.com/docs/api",
        )
        return _cache_set(name, uid, "", finding)
    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError, RuntimeError, TypeError):
        return SourceFinding(name, "unavailable", "Lookup failed or was rate limited.")


async def _discord_reviews(
    session: aiohttp.ClientSession, uid: str, username: str | None,
) -> SourceFinding:
    name = "Discord Reviews"
    key = _key("DANK_DISCORDREVIEWS_API_KEY")
    if not key:
        return SourceFinding(name, "unavailable", "Set DANK_DISCORDREVIEWS_API_KEY to enable on-demand checks.")
    if not username or not re.fullmatch(r"[a-zA-Z0-9_.]{2,32}", username):
        return SourceFinding(name, "unavailable", "Verified current username unavailable; no ID-only endpoint documented.")
    cached = _cache_get(name, uid, username)
    if cached:
        return cached
    try:
        data = await _get_json(session, f"{REVIEWS_URL}/reputation/user/{quote(username, safe='')}", api_key=key)
        profile = data.get("data")
        if data.get("success") is not True or not isinstance(profile, dict):
            raise ValueError("Unexpected API response")
        if str(profile.get("discord_id") or "") != uid:
            raise ValueError("Username resolves to a different or unknown account")
        rep = profile.get("reputation")
        if not isinstance(rep, dict):
            raise ValueError("Reputation missing")
        count = rep.get("negative")
        if type(count) is not int or count < 0:
            raise ValueError("Invalid review count")
        finding = SourceFinding(
            name, "reported" if count else "none_reported",
            f"{count} negative public review(s); opinions are not verified misconduct."
            if count else "No negative public reviews returned; not a clearance.",
            "https://discordreviews.com/docs",
        )
        return _cache_set(name, uid, username, finding)
    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError, RuntimeError, TypeError):
        return SourceFinding(name, "unavailable", "Lookup failed, ID mismatch or provider rate limited.")


async def _cheaterstats_bans(session: aiohttp.ClientSession, uid: str) -> SourceFinding:
    name = "CheaterStats (game bans)"
    key = _key("DANK_CHEATERSTATS_API_KEY")
    if not key or _key("DANK_CHEATERSTATS_PAID_LOOKUPS") != "1":
        return SourceFinding(name, "unavailable", "Paid 10-token game-ban checks disabled until explicitly enabled.")
    cached = _cache_get(name, uid)
    if cached:
        return cached
    try:
        data = await _get_json(
            session, f"{CHEATERSTATS_URL}/csbans", api_key=key,
            params={"discordId": uid},
        )
        if str(data.get("user") or "") != uid or data.get("success") is not True:
            raise ValueError("Account ID not verified by provider")
        total = data.get("total")
        if type(total) is not int or total < 0:
            raise ValueError("Invalid ban count")
        finding = SourceFinding(
            name, "reported" if total else "none_reported",
            f"{total} reported game ban(s); requires individual verification."
            if total else "No game-ban records returned; not a clearance.",
            "https://docs.cheaterstats.cc/docs",
        )
        return _cache_set(name, uid, "", finding)
    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError, RuntimeError, TypeError):
        return SourceFinding(name, "unavailable", "Paid lookup failed or was rate limited.")


async def inspect_external_sources(
    user_id: int | str, *, username: str | None = None,
) -> list[SourceFinding]:
    """On-demand exact-user security lookups, not an automatic membership crawl."""
    uid = str(user_id)
    if not _SNOWFLAKE.fullmatch(uid):
        raise ValueError("Valid Discord user ID required")
    timeout = aiohttp.ClientTimeout(total=6, connect=3)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        checked = await asyncio.gather(
            _protector(session, uid),
            _discord_reviews(session, uid, username),
            _cheaterstats_bans(session, uid),
            return_exceptions=True,
        )
    findings = [
        value if isinstance(value, SourceFinding) else
        SourceFinding(name, "unavailable", "The provider did not complete a valid lookup.")
        for name, value in zip(("Protector", "Discord Reviews", "CheaterStats (game bans)"), checked)
    ]
    findings.extend((
        SourceFinding(
            "Trustify", "manual_review", "Public scammer search available; public API route unverified.",
            "https://trustify.fun/scammers",
        ),
        SourceFinding(
            "Project Redacted", "manual_review", "Human-reviewed ban search; public API route unverified.",
            "https://project-redacted.org/",
        ),
        SourceFinding(
            "Discord AntiScam", "manual_review",
            "Historical phishing-domain list (archived October 2026), not a user blacklist.",
            "https://github.com/Discord-AntiScam/scam-links",
        ),
    ))
    return findings


def normalize_host(value: str) -> str:
    """Parse a domain/URL only; never visit user-supplied links."""
    candidate = value.strip()
    if not candidate or len(candidate) > 2048:
        raise ValueError("Enter a URL or hostname")
    parts = urlsplit(candidate if "://" in candidate else "https://" + candidate)
    if parts.scheme not in {"https", "http"} or parts.username or parts.password:
        raise ValueError("Only ordinary HTTP(S) links may be checked")
    host = (parts.hostname or "").rstrip(".").lower()
    try:
        host = host.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise ValueError("Invalid hostname") from exc
    if not re.fullmatch(r"[a-z0-9.-]{4,253}", host) or "." not in host:
        raise ValueError("Invalid hostname")
    return host


async def check_historical_scam_domain(value: str) -> SourceFinding:
    """Exact apex or subdomain matching against a fixed-host archived list."""
    global _domain_cache
    host = normalize_host(value)
    now = time.monotonic()
    async with _domain_lock:
        if _domain_cache is None or now - _domain_cache[0] >= _DOMAIN_TTL_SECONDS:
            timeout = aiohttp.ClientTimeout(total=8, connect=3)
            try:
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    async with session.get(DOMAIN_LIST_URL, allow_redirects=False) as response:
                        if response.status != 200:
                            raise RuntimeError("List unavailable")
                        raw = await response.content.read(5_000_001)
                        if len(raw) > 5_000_000:
                            raise ValueError("List oversized")
                domains = frozenset(
                    x.strip().lower() for x in raw.decode("utf-8").splitlines()
                    if x.strip() and not x.lstrip().startswith("#")
                    and re.fullmatch(r"[a-z0-9.-]{4,253}", x.strip().lower())
                )
                if not domains:
                    raise ValueError("No valid domains")
                _domain_cache = (now, domains)
            except (aiohttp.ClientError, asyncio.TimeoutError, RuntimeError, ValueError, UnicodeError):
                return SourceFinding(
                    "Discord AntiScam", "unavailable",
                    "Historical list could not be loaded; no safety conclusion.",
                    "https://github.com/Discord-AntiScam/scam-links",
                )
    domains = _domain_cache[1]
    parts = host.split(".")
    matched = next((name for i in range(len(parts) - 1)
                    if (name := ".".join(parts[i:])) in domains), None)
    return SourceFinding(
        "Discord AntiScam",
        "reported" if matched else "none_reported",
        f"Listed historical scam domain: {matched}" if matched else
        "Not found in the archived list; this does NOT establish that the URL is safe.",
        "https://github.com/Discord-AntiScam/scam-links",
    )
