from __future__ import annotations

"""Scalable semantic identity for stylized Discord roles and channels.

Discord native role/channel search only understands the live display name. Dank
Shield cannot inject hidden aliases into Discord's own composer or share picker.
This service gives Dank Shield-controlled lookups a stable semantic identity while
allowing the live Discord name to stay decorative.

Scale rules:
- derive the current semantic name from live Discord objects in memory;
- persist only meaningful *semantic* rename history, never every styled variant;
- keep alias history and tracked records bounded;
- update from rename/delete gateway events instead of guild-wide polling;
- debounce per-guild persistence so a burst of edits becomes one config write.
"""

import asyncio
import re
import time
from collections.abc import Mapping, Sequence
from typing import Any

import discord
from discord import app_commands

from stoney_verify.services import server_design_studio as design

NAMING_IDENTITY_CONFIG_KEY = "naming_identity_v1"
NAMING_IDENTITY_VERSION = 1
MAX_ALIASES_PER_RESOURCE = 3
MAX_TRACKED_RESOURCES = 128
MAX_CACHED_GUILDS = 1024
STATE_CACHE_TTL_SECONDS = 300.0
PERSIST_DEBOUNCE_SECONDS = 1.5

_RUNTIME_FLAG = "_dank_naming_identity_runtime_v1"
_STATE_CACHE: dict[int, tuple[float, dict[str, Any]]] = {}
_PENDING_ALIASES: dict[int, dict[str, set[str]]] = {}
_PENDING_DELETES: dict[int, set[str]] = {}
_FLUSH_TASKS: dict[int, asyncio.Task[Any]] = {}

_ROLE_MENTION_RE = re.compile(r"^<@&(\d+)>$")


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or isinstance(value, bool):
            return int(default)
        return int(str(value).strip())
    except Exception:
        return int(default)


def _resource_key(kind: str, resource_id: Any) -> str:
    clean_kind = "role" if str(kind).strip().lower() == "role" else "channel"
    rid = _safe_int(resource_id, 0)
    return f"{clean_kind}:{rid}" if rid > 0 else ""


def semantic_key(value: Any) -> str:
    """Return a decoration/font-insensitive lowercase key."""
    try:
        return design.normalize_base_name(value, default="")
    except Exception:
        return ""


def search_safe_display_name(value: Any) -> str:
    """Normalize styled letter glyphs without stripping surrounding decoration."""
    raw = str(value or "")
    if not raw:
        return ""
    output: list[str] = []
    for char in raw:
        try:
            decoded = design.strip_known_unicode_fonts(char)
        except Exception:
            decoded = char
        if decoded != char:
            alnum = "".join(part for part in decoded if part.isalnum())
            if alnum:
                output.append(alnum)
                continue
        output.append(char)
    return "".join(output)


def has_stylized_search_text(value: Any) -> bool:
    raw = str(value or "")
    safe = search_safe_display_name(raw)
    return bool(raw and safe != raw and semantic_key(raw))


def previous_alias_for_rename(before_name: Any, after_name: Any) -> str:
    """Persist only semantic renames; style-only changes are derivable live."""
    before = semantic_key(before_name)
    after = semantic_key(after_name)
    if before and after and before != after:
        return before
    return ""


def _empty_state() -> dict[str, Any]:
    return {"version": NAMING_IDENTITY_VERSION, "records": {}}


def _normalize_state(value: Any) -> dict[str, Any]:
    source = value if isinstance(value, Mapping) else {}
    records_raw = source.get("records") if isinstance(source, Mapping) else {}
    records: dict[str, dict[str, Any]] = {}
    if isinstance(records_raw, Mapping):
        for raw_key, raw_record in records_raw.items():
            key = str(raw_key or "")
            if not (key.startswith("role:") or key.startswith("channel:")):
                continue
            if not isinstance(raw_record, Mapping):
                continue
            aliases: list[str] = []
            for raw_alias in list(raw_record.get("aliases") or []):
                alias = semantic_key(raw_alias)
                if alias and alias not in aliases:
                    aliases.append(alias)
                if len(aliases) >= MAX_ALIASES_PER_RESOURCE:
                    break
            if not aliases:
                continue
            try:
                updated_at = float(raw_record.get("updated_at") or 0.0)
            except Exception:
                updated_at = 0.0
            records[key] = {
                "aliases": aliases,
                "updated_at": updated_at,
            }

    if len(records) > MAX_TRACKED_RESOURCES:
        newest = sorted(
            records.items(),
            key=lambda item: float(item[1].get("updated_at") or 0.0),
            reverse=True,
        )[:MAX_TRACKED_RESOURCES]
        records = dict(newest)
    return {"version": NAMING_IDENTITY_VERSION, "records": records}


def remember_alias(
    state: Mapping[str, Any] | None,
    *,
    kind: str,
    resource_id: Any,
    alias: Any,
    updated_at: float | None = None,
) -> dict[str, Any]:
    """Pure bounded-state update used by the runtime and regression tests."""
    result = _normalize_state(state)
    key = _resource_key(kind, resource_id)
    clean_alias = semantic_key(alias)
    if not key or not clean_alias:
        return result

    records = dict(result.get("records") or {})
    current = records.get(key) if isinstance(records.get(key), Mapping) else {}
    aliases = [clean_alias]
    for old in list(current.get("aliases") or []):
        normalized = semantic_key(old)
        if normalized and normalized not in aliases:
            aliases.append(normalized)
        if len(aliases) >= MAX_ALIASES_PER_RESOURCE:
            break
    records[key] = {
        "aliases": aliases[:MAX_ALIASES_PER_RESOURCE],
        "updated_at": float(updated_at if updated_at is not None else time.time()),
    }
    if len(records) > MAX_TRACKED_RESOURCES:
        newest = sorted(
            records.items(),
            key=lambda item: float(item[1].get("updated_at") or 0.0),
            reverse=True,
        )[:MAX_TRACKED_RESOURCES]
        records = dict(newest)
    return {"version": NAMING_IDENTITY_VERSION, "records": records}


def forget_resource(
    state: Mapping[str, Any] | None,
    *,
    kind: str,
    resource_id: Any,
) -> dict[str, Any]:
    result = _normalize_state(state)
    key = _resource_key(kind, resource_id)
    records = dict(result.get("records") or {})
    if key:
        records.pop(key, None)
    return {"version": NAMING_IDENTITY_VERSION, "records": records}


def aliases_for(
    state: Mapping[str, Any] | None,
    *,
    kind: str,
    resource_id: Any,
) -> tuple[str, ...]:
    normalized = _normalize_state(state)
    record = (normalized.get("records") or {}).get(_resource_key(kind, resource_id), {})
    if not isinstance(record, Mapping):
        return ()
    return tuple(str(value) for value in list(record.get("aliases") or []) if str(value))


def _cache_state(guild_id: int, state: Mapping[str, Any]) -> dict[str, Any]:
    """Keep the process cache bounded even if hundreds of thousands of guilds exist."""
    gid = int(guild_id)
    now = time.monotonic()
    normalized = _normalize_state(state)
    _STATE_CACHE[gid] = (now, normalized)

    if len(_STATE_CACHE) > MAX_CACHED_GUILDS:
        expired = [
            cached_gid
            for cached_gid, (stamp, _payload) in _STATE_CACHE.items()
            if now - float(stamp) > STATE_CACHE_TTL_SECONDS
        ]
        for cached_gid in expired:
            _STATE_CACHE.pop(cached_gid, None)

    overflow = len(_STATE_CACHE) - MAX_CACHED_GUILDS
    if overflow > 0:
        oldest = sorted(
            _STATE_CACHE.items(),
            key=lambda item: float(item[1][0]),
        )[:overflow]
        for cached_gid, _payload in oldest:
            _STATE_CACHE.pop(cached_gid, None)
    return normalized


async def _load_state(guild_id: int) -> dict[str, Any]:
    gid = int(guild_id)
    now = time.monotonic()
    cached = _STATE_CACHE.get(gid)
    if cached and now - float(cached[0]) <= STATE_CACHE_TTL_SECONDS:
        return _normalize_state(cached[1])
    if cached:
        _STATE_CACHE.pop(gid, None)

    try:
        from stoney_verify.guild_config import get_guild_config

        config = await get_guild_config(gid)
        state = _normalize_state(config.get(NAMING_IDENTITY_CONFIG_KEY))
    except Exception:
        state = _empty_state()
    return _cache_state(gid, state)


async def _persist_state(guild_id: int, state: Mapping[str, Any]) -> None:
    gid = int(guild_id)
    normalized = _normalize_state(state)
    from stoney_verify.guild_config import upsert_guild_config

    saved = await upsert_guild_config(
        gid,
        {
            NAMING_IDENTITY_CONFIG_KEY: normalized,
            "__config_write_mode": "explicit_override",
            "__config_write_source": "naming_identity_runtime",
            "__config_write_allow_keys": [NAMING_IDENTITY_CONFIG_KEY],
        },
    )
    persisted = _normalize_state(saved.get(NAMING_IDENTITY_CONFIG_KEY, normalized))
    _cache_state(gid, persisted)


def _queue_flush(guild_id: int) -> None:
    gid = int(guild_id)
    existing = _FLUSH_TASKS.get(gid)
    if existing is not None and not existing.done():
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    _FLUSH_TASKS[gid] = loop.create_task(_debounced_flush(gid))


def queue_rename(
    *,
    guild_id: Any,
    kind: str,
    resource_id: Any,
    before_name: Any,
    after_name: Any,
) -> bool:
    """Queue a semantic rename alias. Returns False for style-only changes."""
    gid = _safe_int(guild_id, 0)
    key = _resource_key(kind, resource_id)
    alias = previous_alias_for_rename(before_name, after_name)
    if gid <= 0 or not key or not alias:
        return False
    rows = _PENDING_ALIASES.setdefault(gid, {})
    rows.setdefault(key, set()).add(alias)
    _queue_flush(gid)
    return True


def queue_delete(*, guild_id: Any, kind: str, resource_id: Any) -> bool:
    gid = _safe_int(guild_id, 0)
    key = _resource_key(kind, resource_id)
    if gid <= 0 or not key:
        return False
    _PENDING_DELETES.setdefault(gid, set()).add(key)
    _queue_flush(gid)
    return True


async def _debounced_flush(guild_id: int) -> None:
    gid = int(guild_id)
    try:
        await asyncio.sleep(PERSIST_DEBOUNCE_SECONDS)
        pending_aliases = _PENDING_ALIASES.pop(gid, {})
        pending_deletes = _PENDING_DELETES.pop(gid, set())
        if not pending_aliases and not pending_deletes:
            return

        state = await _load_state(gid)
        before = _normalize_state(state)
        updated = before
        stamp = time.time()

        for key in sorted(pending_deletes):
            kind, _, rid = key.partition(":")
            updated = forget_resource(updated, kind=kind, resource_id=rid)

        for key, aliases in pending_aliases.items():
            kind, _, rid = key.partition(":")
            if key in pending_deletes:
                continue
            for alias in sorted(aliases):
                updated = remember_alias(
                    updated,
                    kind=kind,
                    resource_id=rid,
                    alias=alias,
                    updated_at=stamp,
                )

        if updated != before:
            await _persist_state(gid, updated)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        try:
            print(
                "⚠️ naming_identity flush failed "
                f"guild={gid} error={type(exc).__name__}"
            )
        except Exception:
            pass
    finally:
        _FLUSH_TASKS.pop(gid, None)
        if _PENDING_ALIASES.get(gid) or _PENDING_DELETES.get(gid):
            _queue_flush(gid)


def _raw_name(resource: Any) -> str:
    return str(getattr(resource, "name", "") or "").strip()


def _resource_id(resource: Any) -> int:
    return _safe_int(getattr(resource, "id", 0), 0)


def _position(resource: Any) -> int:
    return _safe_int(getattr(resource, "position", 0), 0)


def _live_match_score(resource: Any, query_text: str, query_key: str) -> int | None:
    raw = _raw_name(resource)
    raw_fold = raw.casefold()
    semantic = semantic_key(raw)
    if not query_text and not query_key:
        return 50
    if query_text and raw_fold == query_text:
        return 0
    if query_key and semantic == query_key:
        return 1
    if query_text and raw_fold.startswith(query_text):
        return 2
    if query_key and semantic.startswith(query_key):
        return 3
    if query_text and query_text in raw_fold:
        return 4
    if query_key and query_key in semantic:
        return 5
    return None


def _alias_match_score(
    aliases: Sequence[str],
    *,
    query_key: str,
) -> int | None:
    if not query_key:
        return None
    normalized = [semantic_key(alias) for alias in aliases]
    if query_key in normalized:
        return 6
    if any(alias.startswith(query_key) for alias in normalized if alias):
        return 7
    if any(query_key in alias for alias in normalized if alias):
        return 8
    return None


def _search_resources(
    resources: Sequence[Any],
    *,
    kind: str,
    current: str,
    state: Mapping[str, Any] | None = None,
    limit: int = 25,
) -> list[Any]:
    query_text = str(current or "").strip().casefold()
    query_key = semantic_key(current)
    rows: list[tuple[int, int, str, Any]] = []
    for resource in resources:
        rid = _resource_id(resource)
        if rid <= 0:
            continue
        try:
            if kind == "role" and bool(resource.is_default()):
                continue
        except Exception:
            pass

        score = _live_match_score(resource, query_text, query_key)
        if score is None and state is not None:
            score = _alias_match_score(
                aliases_for(state, kind=kind, resource_id=rid),
                query_key=query_key,
            )
        if score is None:
            continue
        rows.append((score, -_position(resource), _raw_name(resource).casefold(), resource))

    rows.sort(key=lambda row: (row[0], row[1], row[2]))
    return [row[3] for row in rows[: max(1, min(int(limit), 25))]]


def _choice_name(resource: Any) -> str:
    raw = _raw_name(resource)
    safe = search_safe_display_name(raw).strip()
    if safe and safe != raw:
        combined = f"{raw} · {safe}"
    else:
        combined = raw
    return combined[:100] or str(_resource_id(resource))


async def role_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[str]]:
    guild = interaction.guild
    if guild is None:
        return []
    roles = list(getattr(guild, "roles", []) or [])

    # Common case: current stylized name can be decoded live without DB I/O.
    live = _search_resources(roles, kind="role", current=current, state=None, limit=25)
    if live or not str(current or "").strip():
        return [
            app_commands.Choice(name=_choice_name(role), value=str(_resource_id(role)))
            for role in live
        ]

    state = await _load_state(int(guild.id))
    matches = _search_resources(roles, kind="role", current=current, state=state, limit=25)
    return [
        app_commands.Choice(name=_choice_name(role), value=str(_resource_id(role)))
        for role in matches
    ]


async def channel_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[str]]:
    guild = interaction.guild
    if guild is None:
        return []
    channels = list(getattr(guild, "channels", []) or [])
    live = _search_resources(channels, kind="channel", current=current, state=None, limit=25)
    if live or not str(current or "").strip():
        return [
            app_commands.Choice(name=_choice_name(channel), value=str(_resource_id(channel)))
            for channel in live
        ]
    state = await _load_state(int(guild.id))
    matches = _search_resources(channels, kind="channel", current=current, state=state, limit=25)
    return [
        app_commands.Choice(name=_choice_name(channel), value=str(_resource_id(channel)))
        for channel in matches
    ]


def _parse_resource_id_query(value: Any) -> int:
    text = str(value or "").strip()
    match = _ROLE_MENTION_RE.fullmatch(text)
    if match:
        return _safe_int(match.group(1), 0)
    return _safe_int(text, 0) if text.isdigit() else 0


async def resolve_role_query(
    guild: discord.Guild,
    query: Any,
) -> tuple[discord.Role | None, str]:
    text = str(query or "").strip()
    if not text:
        return None, "Choose a role."

    direct_id = _parse_resource_id_query(text)
    if direct_id > 0:
        role = guild.get_role(direct_id)
        if isinstance(role, discord.Role) and not role.is_default():
            return role, ""
        return None, "That role no longer exists."

    roles = [
        role
        for role in list(getattr(guild, "roles", []) or [])
        if isinstance(role, discord.Role) and not role.is_default()
    ]
    raw_exact = [role for role in roles if _raw_name(role).casefold() == text.casefold()]
    if len(raw_exact) == 1:
        return raw_exact[0], ""
    if len(raw_exact) > 1:
        return None, "More than one role has that exact display name. Choose one from autocomplete."

    query_key = semantic_key(text)
    semantic_exact = [role for role in roles if semantic_key(_raw_name(role)) == query_key and query_key]
    if len(semantic_exact) == 1:
        return semantic_exact[0], ""
    if len(semantic_exact) > 1:
        return None, "More than one role has that searchable name. Choose one from autocomplete."

    state = await _load_state(int(guild.id))
    alias_exact = [
        role
        for role in roles
        if query_key
        and query_key in aliases_for(state, kind="role", resource_id=role.id)
    ]
    if len(alias_exact) == 1:
        return alias_exact[0], ""
    if len(alias_exact) > 1:
        return None, "That old role name matches more than one role. Choose one from autocomplete."
    return None, "No role matched that name or saved alias. Choose one from autocomplete."


async def _on_guild_role_update(before: discord.Role, after: discord.Role) -> None:
    if str(getattr(before, "name", "")) == str(getattr(after, "name", "")):
        return
    queue_rename(
        guild_id=getattr(getattr(after, "guild", None), "id", 0),
        kind="role",
        resource_id=getattr(after, "id", 0),
        before_name=getattr(before, "name", ""),
        after_name=getattr(after, "name", ""),
    )


async def _on_guild_channel_update(
    before: discord.abc.GuildChannel,
    after: discord.abc.GuildChannel,
) -> None:
    if str(getattr(before, "name", "")) == str(getattr(after, "name", "")):
        return
    queue_rename(
        guild_id=getattr(getattr(after, "guild", None), "id", 0),
        kind="channel",
        resource_id=getattr(after, "id", 0),
        before_name=getattr(before, "name", ""),
        after_name=getattr(after, "name", ""),
    )


async def _on_guild_role_delete(role: discord.Role) -> None:
    queue_delete(
        guild_id=getattr(getattr(role, "guild", None), "id", 0),
        kind="role",
        resource_id=getattr(role, "id", 0),
    )


async def _on_guild_channel_delete(channel: discord.abc.GuildChannel) -> None:
    queue_delete(
        guild_id=getattr(getattr(channel, "guild", None), "id", 0),
        kind="channel",
        resource_id=getattr(channel, "id", 0),
    )


def install_naming_identity_runtime(bot: Any) -> bool:
    """Attach sparse rename/delete listeners once. No startup guild scan."""
    if bool(getattr(bot, _RUNTIME_FLAG, False)):
        return False
    bot.add_listener(_on_guild_role_update, "on_guild_role_update")
    bot.add_listener(_on_guild_channel_update, "on_guild_channel_update")
    bot.add_listener(_on_guild_role_delete, "on_guild_role_delete")
    bot.add_listener(_on_guild_channel_delete, "on_guild_channel_delete")
    setattr(bot, _RUNTIME_FLAG, True)
    try:
        print("🔎 naming_identity runtime active: event-driven aliases, no guild-wide polling")
    except Exception:
        pass
    return True


__all__ = [
    "MAX_ALIASES_PER_RESOURCE",
    "MAX_CACHED_GUILDS",
    "MAX_TRACKED_RESOURCES",
    "NAMING_IDENTITY_CONFIG_KEY",
    "aliases_for",
    "channel_autocomplete",
    "has_stylized_search_text",
    "install_naming_identity_runtime",
    "previous_alias_for_rename",
    "queue_delete",
    "queue_rename",
    "remember_alias",
    "resolve_role_query",
    "role_autocomplete",
    "search_safe_display_name",
    "semantic_key",
]
