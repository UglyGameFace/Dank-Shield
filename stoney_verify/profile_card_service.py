from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Mapping, Optional
from urllib.parse import quote, unquote, urlparse, urlunparse

from .globals import get_supabase, reset_supabase
from .profile_signature_style import (
    DEFAULT_MEMBER_PROFILE_STYLE,
    normalize_member_profile_style,
)


PROFILE_USER_TABLE = "dank_profile_users"
PROFILE_GUILD_SETTINGS_TABLE = "dank_profile_guild_settings"
LIVE_CARD_STATE_TABLE = "dank_live_profile_cards"

PROFILE_FIELDS = frozenset({"roles", "account_dates", "platforms"})
DEFAULT_PROFILE_PREFERENCES: dict[str, bool] = {
    "live_cards_enabled": True,
    "show_roles": True,
    "show_account_dates": True,
    "show_platforms": True,
}

_USERNAME_MAX = 80
_URL_MAX = 500
_DB_ATTEMPTS = 3
_CACHE_TTL_SECONDS = 60.0
_BIDI_CONTROL_RE = re.compile("[\u061c\u200e\u200f\u202a-\u202e\u2066-\u2069]")


class ProfileStorageUnavailable(RuntimeError):
    """Raised when private profile state cannot be read or written safely."""


class InvalidPlatformProfile(ValueError):
    """Raised when a member supplies an unsafe or unsupported platform value."""


@dataclass(frozen=True)
class PlatformSpec:
    key: str
    label: str
    emoji: str
    hosts: tuple[str, ...] = ()
    path_kind: str = "username_only"

    @property
    def supports_url(self) -> bool:
        return bool(self.hosts) and self.path_kind != "username_only"


PLATFORM_SPECS: dict[str, PlatformSpec] = {
    "steam": PlatformSpec("steam", "Steam", "🎮", ("steamcommunity.com",), "steam"),
    "epic": PlatformSpec("epic", "Epic Games", "🛡️"),
    # Xbox and PlayStation do not expose one stable, reliably canonical
    # public-profile URL format across regions. Keep them username-only rather
    # than accepting a generic official-site page or inventing a link.
    "xbox": PlatformSpec("xbox", "Xbox", "🟢"),
    "playstation": PlatformSpec("playstation", "PlayStation", "🔷"),
    "nintendo": PlatformSpec("nintendo", "Nintendo", "🔴"),
    "riot": PlatformSpec("riot", "Riot Games", "⚔️"),
    "battle_net": PlatformSpec("battle_net", "Battle.net", "🌀"),
    "roblox": PlatformSpec("roblox", "Roblox", "⬜", ("roblox.com", "www.roblox.com"), "roblox"),
    "twitch": PlatformSpec("twitch", "Twitch", "🟣", ("twitch.tv", "www.twitch.tv"), "single"),
    "youtube": PlatformSpec("youtube", "YouTube", "▶️", ("youtube.com", "www.youtube.com"), "youtube"),
    "kick": PlatformSpec("kick", "Kick", "🟩", ("kick.com", "www.kick.com"), "single"),
    "custom": PlatformSpec("custom", "Other", "🔗"),
}

_RESERVED_SINGLE_PATHS = {
    "about",
    "account",
    "api",
    "directory",
    "downloads",
    "jobs",
    "login",
    "logout",
    "privacy",
    "search",
    "settings",
    "signup",
    "support",
    "terms",
}

_USER_LOCKS: dict[int, asyncio.Lock] = {}
_USER_CACHE: dict[int, tuple[float, dict[str, Any]]] = {}
_GUILD_USER_CACHE: dict[tuple[int, int], tuple[float, dict[str, Any]]] = {}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def clean_platform_key(value: Any) -> str:
    key = str(value or "").strip().lower().replace("-", "_").replace(".", "_")
    if key not in PLATFORM_SPECS:
        raise InvalidPlatformProfile("Choose a supported platform.")
    return key


def clean_profile_username(value: Any) -> str:
    text = str(value or "").replace("@everyone", "everyone").replace("@here", "here")
    text = _BIDI_CONTROL_RE.sub("", text)
    text = " ".join(text.replace("\r", " ").replace("\n", " ").split()).strip()
    if len(text) < 1:
        raise InvalidPlatformProfile("Enter the username or handle shown on that platform.")
    if len(text) > _USERNAME_MAX:
        raise InvalidPlatformProfile(f"Platform usernames must be {_USERNAME_MAX} characters or shorter.")
    lowered = text.lower()
    if "://" in lowered or "discord.gg" in lowered:
        raise InvalidPlatformProfile("Put the username in the username field, not a link.")
    return text


def display_profile_username(value: Any) -> str:
    """Return a Discord-safe username that cannot create markdown links."""
    return clean_profile_username(value).replace("`", "ʼ")


def _normalized_path(parsed_path: str) -> str:
    decoded = unquote(str(parsed_path or ""))
    if "\\" in decoded or "\x00" in decoded:
        raise InvalidPlatformProfile("That profile URL contains an unsafe path.")
    parts = [part for part in decoded.split("/") if part]
    if any(part in {".", ".."} for part in parts):
        raise InvalidPlatformProfile("That profile URL contains an unsafe path.")
    return "/" + "/".join(quote(part, safe="@._-") for part in parts) if parts else "/"


def _validate_platform_path(spec: PlatformSpec, path: str) -> None:
    parts = [unquote(part) for part in path.split("/") if part]
    kind = spec.path_kind

    if kind == "steam":
        if len(parts) != 2 or parts[0].lower() not in {"id", "profiles"}:
            raise InvalidPlatformProfile("Use a Steam Community /id/… or /profiles/… link.")
        if parts[0].lower() == "profiles" and not parts[1].isdigit():
            raise InvalidPlatformProfile("Steam numeric profile links must contain only the Steam ID.")
        return

    if kind == "roblox":
        if len(parts) != 3 or parts[0].lower() != "users" or not parts[1].isdigit() or parts[2].lower() != "profile":
            raise InvalidPlatformProfile("Use the official Roblox /users/ID/profile link.")
        return

    if kind == "youtube":
        valid = False
        if len(parts) == 1 and parts[0].startswith("@") and len(parts[0]) > 1:
            valid = True
        if len(parts) == 2 and parts[0].lower() in {"channel", "c", "user"} and parts[1]:
            valid = True
        if not valid:
            raise InvalidPlatformProfile("Use a YouTube @handle or official channel URL.")
        return

    if kind == "single":
        if len(parts) != 1 or parts[0].lower() in _RESERVED_SINGLE_PATHS:
            raise InvalidPlatformProfile(f"Use the public {spec.label} channel/profile URL.")
        return

    if kind == "official":
        if not parts:
            raise InvalidPlatformProfile(f"Use a specific public {spec.label} profile URL, not the home page.")
        return

    raise InvalidPlatformProfile(f"{spec.label} is username-only in Dank Shield; leave the URL blank.")


def normalize_platform_url(platform: Any, value: Any) -> str:
    key = clean_platform_key(platform)
    spec = PLATFORM_SPECS[key]
    raw = str(value or "").strip()
    if not raw:
        return ""
    if len(raw) > _URL_MAX:
        raise InvalidPlatformProfile("Profile URLs must be 500 characters or shorter.")
    if not spec.supports_url:
        raise InvalidPlatformProfile(f"{spec.label} is shown by username only; leave the URL blank.")

    parsed = urlparse(raw)
    if parsed.scheme.lower() != "https":
        raise InvalidPlatformProfile("Profile links must use HTTPS.")
    try:
        parsed_port = parsed.port
    except ValueError as exc:
        raise InvalidPlatformProfile("Profile links cannot contain an invalid port.") from exc
    if parsed.username or parsed.password or parsed_port is not None:
        raise InvalidPlatformProfile("Profile links cannot contain credentials or custom ports.")

    host = str(parsed.hostname or "").lower().rstrip(".")
    if host not in spec.hosts:
        raise InvalidPlatformProfile(f"Use an official {spec.label} profile link.")
    if parsed.query or parsed.fragment or parsed.params:
        raise InvalidPlatformProfile("Remove tracking parameters and fragments from the profile link.")

    path = _normalized_path(parsed.path)
    _validate_platform_path(spec, path)
    canonical_host = spec.hosts[0]
    return urlunparse(("https", canonical_host, path.rstrip("/") or "/", "", "", ""))


def normalize_platform_entry(
    platform: Any,
    *,
    username: Any,
    profile_url: Any = "",
    shared: bool = False,
) -> dict[str, Any]:
    key = clean_platform_key(platform)
    return {
        "platform": key,
        "username": clean_profile_username(username),
        "url": normalize_platform_url(key, profile_url),
        "shared": bool(shared),
        "updated_at": utc_now_iso(),
    }


def normalize_preferences(value: Optional[Mapping[str, Any]]) -> dict[str, Any]:
    raw = dict(value or {}) if isinstance(value, Mapping) else {}
    result: dict[str, Any] = dict(DEFAULT_PROFILE_PREFERENCES)
    for key in DEFAULT_PROFILE_PREFERENCES:
        if key in raw:
            result[key] = bool(raw.get(key))
    result.update(normalize_member_profile_style(raw))
    return result


def effective_preferences(
    user_preferences: Optional[Mapping[str, Any]],
    guild_settings: Optional[Mapping[str, Any]],
) -> dict[str, Any]:
    global_values = normalize_preferences(user_preferences)
    local = dict(guild_settings or {})
    resolved = dict(global_values)
    for key in DEFAULT_PROFILE_PREFERENCES:
        resolved[key] = bool(global_values[key]) and bool(local.get(key, True))
    return resolved


def normalize_server_allowed_fields(value: Any) -> set[str]:
    if isinstance(value, str):
        candidates = re.split(r"[,\s]+", value)
    elif isinstance(value, (list, tuple, set, frozenset)):
        candidates = list(value)
    else:
        return set(PROFILE_FIELDS)
    cleaned = {str(item or "").strip().lower() for item in candidates}
    return cleaned & set(PROFILE_FIELDS)


def visible_platform_entries(platforms: Any, *, allowed: bool) -> list[dict[str, Any]]:
    if not allowed or not isinstance(platforms, Mapping):
        return []
    entries: list[dict[str, Any]] = []
    for key in PLATFORM_SPECS:
        raw = platforms.get(key)
        if not isinstance(raw, Mapping) or not bool(raw.get("shared")):
            continue
        try:
            normalized = normalize_platform_entry(
                key,
                username=raw.get("username"),
                profile_url=raw.get("url"),
                shared=True,
            )
        except (InvalidPlatformProfile, ValueError):
            continue
        entries.append(normalized)
    return entries


def _cache_get(cache: dict[Any, tuple[float, dict[str, Any]]], key: Any) -> Optional[dict[str, Any]]:
    found = cache.get(key)
    if not found:
        return None
    timestamp, payload = found
    if time.monotonic() - timestamp > _CACHE_TTL_SECONDS:
        cache.pop(key, None)
        return None
    return dict(payload)


def _cache_put(cache: dict[Any, tuple[float, dict[str, Any]]], key: Any, payload: Mapping[str, Any]) -> None:
    if len(cache) > 5000:
        oldest = sorted(cache.items(), key=lambda item: item[1][0])[:1000]
        for stale_key, _value in oldest:
            cache.pop(stale_key, None)
    cache[key] = (time.monotonic(), dict(payload))


def invalidate_profile_cache(*, user_id: Optional[int] = None, guild_id: Optional[int] = None) -> None:
    if user_id is None and guild_id is None:
        _USER_CACHE.clear()
        _GUILD_USER_CACHE.clear()
        return
    if user_id is not None:
        _USER_CACHE.pop(int(user_id), None)
    for key in list(_GUILD_USER_CACHE):
        if user_id is not None and key[1] != int(user_id):
            continue
        if guild_id is not None and key[0] != int(guild_id):
            continue
        _GUILD_USER_CACHE.pop(key, None)


def _rows(response: Any) -> list[dict[str, Any]]:
    raw = getattr(response, "data", None) or []
    return [dict(item) for item in raw if isinstance(item, Mapping)]


def _is_retryable(error: Exception) -> bool:
    text = repr(error).lower()
    return any(
        marker in text
        for marker in (
            "timeout",
            "timed out",
            "connection reset",
            "connection aborted",
            "temporarily unavailable",
            "server disconnected",
            "remoteprotocolerror",
            "broken pipe",
            "eof",
        )
    )


def _execute_sync(label: str, operation: Callable[[Any], Any]) -> Any:
    last_error: Optional[Exception] = None
    for attempt in range(1, _DB_ATTEMPTS + 1):
        try:
            client = get_supabase()
            if client is None:
                raise ProfileStorageUnavailable("Supabase service-role storage is unavailable.")
            return operation(client)
        except ProfileStorageUnavailable:
            raise
        except Exception as exc:
            last_error = exc
            if _is_retryable(exc) and attempt < _DB_ATTEMPTS:
                reset_supabase()
                time.sleep(0.15 * attempt)
                continue
            break
    raise ProfileStorageUnavailable(f"{label} failed safely: {type(last_error).__name__ if last_error else 'unknown error'}")


async def _execute(label: str, operation: Callable[[Any], Any]) -> Any:
    return await asyncio.to_thread(_execute_sync, label, operation)


def _default_user_row(user_id: int) -> dict[str, Any]:
    return {
        "user_id": str(int(user_id)),
        "preferences": dict(DEFAULT_PROFILE_PREFERENCES),
        "platforms": {},
    }


async def get_profile_user(user_id: int, *, refresh: bool = False) -> dict[str, Any]:
    resolved = int(user_id)
    if not refresh:
        cached = _cache_get(_USER_CACHE, resolved)
        if cached is not None:
            return cached

    def read(client: Any):
        return client.table(PROFILE_USER_TABLE).select("*").eq("user_id", str(resolved)).limit(1).execute()

    rows = _rows(await _execute(f"read profile user {resolved}", read))
    payload = _default_user_row(resolved)
    if rows:
        payload.update(rows[0])
    payload["preferences"] = normalize_preferences(payload.get("preferences"))
    payload["platforms"] = dict(payload.get("platforms") or {}) if isinstance(payload.get("platforms"), Mapping) else {}
    _cache_put(_USER_CACHE, resolved, payload)
    return dict(payload)


async def upsert_profile_user_preferences(
    user_id: int,
    updates: Mapping[str, Any],
) -> dict[str, Any]:
    """Update cross-server privacy defaults without touching saved identities."""
    uid = int(user_id)
    lock = _USER_LOCKS.setdefault(uid, asyncio.Lock())
    async with lock:
        current = await get_profile_user(uid, refresh=True)
        preferences = normalize_preferences(current.get("preferences"))
        for key in DEFAULT_PROFILE_PREFERENCES:
            if key in updates and updates.get(key) is not None:
                preferences[key] = bool(updates.get(key))
        for key in DEFAULT_MEMBER_PROFILE_STYLE:
            if key in updates and updates.get(key) is not None:
                preferences[key] = updates.get(key)
        preferences = normalize_preferences(preferences)
        payload = {
            "user_id": str(uid),
            "preferences": preferences,
            "platforms": dict(current.get("platforms") or {}),
            "updated_at": utc_now_iso(),
        }

        def write(client: Any):
            try:
                return client.table(PROFILE_USER_TABLE).upsert(
                    payload,
                    on_conflict="user_id",
                ).execute()
            except TypeError:
                return client.table(PROFILE_USER_TABLE).upsert(payload).execute()

        await _execute(f"write profile user preferences {uid}", write)
        invalidate_profile_cache(user_id=uid)
        return await get_profile_user(uid, refresh=True)


async def get_profile_guild_settings(guild_id: int, user_id: int, *, refresh: bool = False) -> dict[str, Any]:
    key = (int(guild_id), int(user_id))
    if not refresh:
        cached = _cache_get(_GUILD_USER_CACHE, key)
        if cached is not None:
            return cached

    def read(client: Any):
        return (
            client.table(PROFILE_GUILD_SETTINGS_TABLE)
            .select("*")
            .eq("guild_id", str(key[0]))
            .eq("user_id", str(key[1]))
            .limit(1)
            .execute()
        )

    rows = _rows(await _execute(f"read profile guild settings {key[0]}/{key[1]}", read))
    payload = {
        "guild_id": str(key[0]),
        "user_id": str(key[1]),
        "settings": {},
    }
    if rows:
        payload.update(rows[0])
    payload["settings"] = dict(payload.get("settings") or {}) if isinstance(payload.get("settings"), Mapping) else {}
    _cache_put(_GUILD_USER_CACHE, key, payload)
    return dict(payload)


async def get_effective_profile_settings(guild_id: int, user_id: int) -> dict[str, Any]:
    user_row, guild_row = await asyncio.gather(
        get_profile_user(user_id),
        get_profile_guild_settings(guild_id, user_id),
    )
    return {
        "preferences": effective_preferences(user_row.get("preferences"), guild_row.get("settings")),
        "platforms": dict(user_row.get("platforms") or {}),
    }


async def upsert_profile_guild_settings(guild_id: int, user_id: int, updates: Mapping[str, Any]) -> dict[str, Any]:
    gid = int(guild_id)
    uid = int(user_id)
    lock = _USER_LOCKS.setdefault(uid, asyncio.Lock())
    async with lock:
        current = await get_profile_guild_settings(gid, uid, refresh=True)
        settings = dict(current.get("settings") or {})
        for key in DEFAULT_PROFILE_PREFERENCES:
            if key not in updates:
                continue
            value = updates.get(key)
            if value is None or bool(value):
                # Per-server settings are deny-only. True means inherit the
                # cross-server default rather than storing a misleading allow.
                settings.pop(key, None)
            else:
                settings[key] = False
        payload = {
            "guild_id": str(gid),
            "user_id": str(uid),
            "settings": settings,
            "updated_at": utc_now_iso(),
        }

        def write(client: Any):
            try:
                return client.table(PROFILE_GUILD_SETTINGS_TABLE).upsert(
                    payload,
                    on_conflict="guild_id,user_id",
                ).execute()
            except TypeError:
                return client.table(PROFILE_GUILD_SETTINGS_TABLE).upsert(payload).execute()

        await _execute(f"write profile guild settings {gid}/{uid}", write)
        invalidate_profile_cache(user_id=uid, guild_id=gid)
        return await get_profile_guild_settings(gid, uid, refresh=True)


async def save_platform_identity(
    user_id: int,
    platform: Any,
    *,
    username: Any,
    profile_url: Any = "",
    shared: bool = False,
) -> dict[str, Any]:
    uid = int(user_id)
    key = clean_platform_key(platform)
    entry = normalize_platform_entry(key, username=username, profile_url=profile_url, shared=shared)
    lock = _USER_LOCKS.setdefault(uid, asyncio.Lock())
    async with lock:
        current = await get_profile_user(uid, refresh=True)
        platforms = dict(current.get("platforms") or {})
        platforms[key] = entry
        payload = {
            "user_id": str(uid),
            "preferences": normalize_preferences(current.get("preferences")),
            "platforms": platforms,
            "updated_at": utc_now_iso(),
        }

        def write(client: Any):
            try:
                return client.table(PROFILE_USER_TABLE).upsert(payload, on_conflict="user_id").execute()
            except TypeError:
                return client.table(PROFILE_USER_TABLE).upsert(payload).execute()

        await _execute(f"save profile platform {uid}/{key}", write)
        invalidate_profile_cache(user_id=uid)
        return entry


async def remove_platform_identity(user_id: int, platform: Any) -> bool:
    uid = int(user_id)
    key = clean_platform_key(platform)
    lock = _USER_LOCKS.setdefault(uid, asyncio.Lock())
    async with lock:
        current = await get_profile_user(uid, refresh=True)
        platforms = dict(current.get("platforms") or {})
        existed = key in platforms
        platforms.pop(key, None)
        payload = {
            "user_id": str(uid),
            "preferences": normalize_preferences(current.get("preferences")),
            "platforms": platforms,
            "updated_at": utc_now_iso(),
        }

        def write(client: Any):
            try:
                return client.table(PROFILE_USER_TABLE).upsert(payload, on_conflict="user_id").execute()
            except TypeError:
                return client.table(PROFILE_USER_TABLE).upsert(payload).execute()

        await _execute(f"remove profile platform {uid}/{key}", write)
        invalidate_profile_cache(user_id=uid)
        return existed


async def get_live_card_state(guild_id: int, channel_id: int) -> Optional[dict[str, Any]]:
    gid = int(guild_id)
    cid = int(channel_id)

    def read(client: Any):
        return (
            client.table(LIVE_CARD_STATE_TABLE)
            .select("*")
            .eq("guild_id", str(gid))
            .eq("channel_id", str(cid))
            .limit(1)
            .execute()
        )

    rows = _rows(await _execute(f"read live profile state {gid}/{cid}", read))
    return rows[0] if rows else None


async def list_live_card_states() -> list[dict[str, Any]]:
    def read(client: Any):
        return client.table(LIVE_CARD_STATE_TABLE).select("*").execute()

    return _rows(await _execute("list live profile states", read))


async def list_live_card_states_for_user(
    user_id: int,
    *,
    guild_id: Optional[int] = None,
) -> list[dict[str, Any]]:
    uid = int(user_id)
    gid = int(guild_id) if guild_id is not None else None

    def read(client: Any):
        query = (
            client.table(LIVE_CARD_STATE_TABLE)
            .select("*")
            .eq("user_id", str(uid))
        )
        if gid is not None:
            query = query.eq("guild_id", str(gid))
        return query.execute()

    label = f"list live profile states for user {uid}"
    if gid is not None:
        label += f" in guild {gid}"
    return _rows(await _execute(label, read))


async def upsert_live_card_state(
    guild_id: int,
    channel_id: int,
    *,
    message_id: int,
    user_id: int,
    trigger_message_id: int,
) -> dict[str, Any]:
    payload = {
        "guild_id": str(int(guild_id)),
        "channel_id": str(int(channel_id)),
        "message_id": str(int(message_id)),
        "user_id": str(int(user_id)),
        "trigger_message_id": str(int(trigger_message_id)),
        "updated_at": utc_now_iso(),
    }

    def write(client: Any):
        try:
            return client.table(LIVE_CARD_STATE_TABLE).upsert(
                payload,
                on_conflict="guild_id,channel_id",
            ).execute()
        except TypeError:
            return client.table(LIVE_CARD_STATE_TABLE).upsert(payload).execute()

    await _execute(f"write live profile state {guild_id}/{channel_id}", write)
    return payload


async def delete_live_card_state(guild_id: int, channel_id: int) -> None:
    gid = int(guild_id)
    cid = int(channel_id)

    def delete(client: Any):
        return (
            client.table(LIVE_CARD_STATE_TABLE)
            .delete()
            .eq("guild_id", str(gid))
            .eq("channel_id", str(cid))
            .execute()
        )

    await _execute(f"delete live profile state {gid}/{cid}", delete)


__all__ = [
    "DEFAULT_MEMBER_PROFILE_STYLE",
    "DEFAULT_PROFILE_PREFERENCES",
    "InvalidPlatformProfile",
    "PLATFORM_SPECS",
    "PROFILE_FIELDS",
    "ProfileStorageUnavailable",
    "clean_platform_key",
    "display_profile_username",
    "effective_preferences",
    "get_effective_profile_settings",
    "get_live_card_state",
    "get_profile_guild_settings",
    "get_profile_user",
    "invalidate_profile_cache",
    "list_live_card_states",
    "list_live_card_states_for_user",
    "normalize_platform_entry",
    "normalize_platform_url",
    "normalize_preferences",
    "normalize_server_allowed_fields",
    "remove_platform_identity",
    "save_platform_identity",
    "upsert_live_card_state",
    "upsert_profile_guild_settings",
    "upsert_profile_user_preferences",
    "delete_live_card_state",
    "visible_platform_entries",
]
