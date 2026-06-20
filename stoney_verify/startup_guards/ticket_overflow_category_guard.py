from __future__ import annotations

"""Ticket overflow category support.

Discord categories have a hard child-channel limit. A production ticket bot should
not fail ticket creation just because the primary active ticket category is full.

This guard adds a shared overflow resolver for both current creation paths:
- tickets_new.service.create_ticket_channel
- commands_ext.public_ticket_panel_clean._active_category

Behavior:
- use the primary active category while it has room
- use configured/detected overflow categories when the primary is near full
- create a safe overflow category when allowed and none exists
- never use archive/closed categories as overflow
"""

import asyncio
import os
import re
from typing import Any, Dict, Iterable, List, Optional, Sequence

import discord

_GUILD_LOCKS: Dict[int, asyncio.Lock] = {}

_DEFAULT_SOFT_LIMIT = 49
_DEFAULT_MAX_OVERFLOW_CATEGORIES = 3
_OVERFLOW_NAME_RE = re.compile(r"(?:overflow|extra|more|additional).*(?:ticket|support)|(?:ticket|support).*(?:overflow|extra|more|additional)", re.I)


def _log(message: str) -> None:
    try:
        print(f"✅ ticket_overflow_category_guard: {message}")
    except Exception:
        pass


def _warn(message: str) -> None:
    try:
        print(f"⚠️ ticket_overflow_category_guard: {message}")
    except Exception:
        pass


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or isinstance(value, bool):
            return int(default)
        text = str(value).strip()
        return int(text) if text else int(default)
    except Exception:
        return int(default)


def _safe_str(value: Any, default: str = "") -> str:
    try:
        text = str(value or "").strip()
        return text if text else default
    except Exception:
        return default


def _env_int(name: str, default: int) -> int:
    return _safe_int(os.getenv(name), default)


def _env_true(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == "":
        return bool(default)
    return str(raw).strip().lower() in {"1", "true", "yes", "y", "on"}


def _soft_limit() -> int:
    # Discord's practical category child-channel cap is 50. Switch at 49 by
    # default so two fast ticket creates do not race into the hard limit.
    value = _env_int("DANK_TICKET_CATEGORY_SOFT_LIMIT", _DEFAULT_SOFT_LIMIT)
    return max(1, min(value, 50))


def _max_overflow_categories() -> int:
    value = _env_int("DANK_TICKET_MAX_OVERFLOW_CATEGORIES", _DEFAULT_MAX_OVERFLOW_CATEGORIES)
    return max(0, min(value, 10))


def _auto_create_enabled() -> bool:
    return _env_true("DANK_TICKET_OVERFLOW_AUTO_CREATE", True)


def _guild_lock(guild_id: int) -> asyncio.Lock:
    gid = int(guild_id)
    lock = _GUILD_LOCKS.get(gid)
    if lock is None:
        lock = asyncio.Lock()
        _GUILD_LOCKS[gid] = lock
    return lock


def _child_count(category: Optional[discord.CategoryChannel]) -> int:
    if not isinstance(category, discord.CategoryChannel):
        return 999
    try:
        return len(list(category.channels or []))
    except Exception:
        return 999


def _category_has_room(category: Optional[discord.CategoryChannel]) -> bool:
    return isinstance(category, discord.CategoryChannel) and _child_count(category) < _soft_limit()


def _category_permission_missing(category: discord.CategoryChannel, me: Optional[discord.Member]) -> List[str]:
    if me is None:
        return ["bot member unavailable"]
    try:
        p = category.permissions_for(me)
        checks = [
            ("View Channel", p.view_channel),
            ("Send Messages", p.send_messages),
            ("Read Message History", p.read_message_history),
            ("Embed Links", p.embed_links),
            ("Attach Files", p.attach_files),
            ("Manage Channels", p.manage_channels),
            ("Manage Permissions", p.manage_permissions),
        ]
        return [name for name, ok in checks if not ok]
    except Exception:
        return ["permission check failed"]


def _category_is_usable(category: Optional[discord.CategoryChannel], *, me: Optional[discord.Member]) -> bool:
    if not isinstance(category, discord.CategoryChannel):
        return False
    if _is_archive_or_closed_category(category):
        return False
    if not _category_has_room(category):
        return False
    return not _category_permission_missing(category, me)


def _is_archive_or_closed_category(category: discord.CategoryChannel) -> bool:
    name = _safe_str(getattr(category, "name", "")).lower()
    return any(marker in name for marker in ("archive", "archived", "closed"))


def _looks_like_overflow(category: discord.CategoryChannel, primary: Optional[discord.CategoryChannel]) -> bool:
    name = _safe_str(getattr(category, "name", ""))
    if not name or _is_archive_or_closed_category(category):
        return False
    if _OVERFLOW_NAME_RE.search(name):
        return True
    if isinstance(primary, discord.CategoryChannel):
        p = _safe_str(primary.name).lower()
        n = name.lower()
        if p and p in n and any(token in n for token in ("2", "3", "overflow", "extra", "more")):
            return True
    return False


def _parse_ids(value: Any) -> List[int]:
    out: List[int] = []
    if value is None:
        return out
    raw_items: Iterable[Any]
    if isinstance(value, (list, tuple, set)):
        raw_items = value
    else:
        text = _safe_str(value)
        text = text.replace(";", ",").replace("|", ",").replace(" ", ",")
        raw_items = text.split(",")
    for item in raw_items:
        cid = _safe_int(item, 0)
        if cid > 0 and cid not in out:
            out.append(cid)
    return out


def _config_values(config: Dict[str, Any], keys: Sequence[str]) -> List[int]:
    out: List[int] = []
    for key in keys:
        try:
            for cid in _parse_ids(config.get(key)):
                if cid not in out:
                    out.append(cid)
        except Exception:
            continue
    return out


async def _read_guild_config(guild_id: int) -> Dict[str, Any]:
    try:
        from ..commands_ext import public_ticket_panel_clean as panel_mod
        cfg = await panel_mod._cfg(int(guild_id))
        return cfg if isinstance(cfg, dict) else {}
    except Exception:
        pass

    try:
        from ..globals import get_supabase
        sb = get_supabase()
    except Exception:
        sb = None
    if sb is None:
        return {}

    def sync() -> Dict[str, Any]:
        try:
            rows = getattr(
                sb.table("guild_configs").select("*").eq("guild_id", str(guild_id)).limit(1).execute(),
                "data",
                None,
            ) or []
            if rows and isinstance(rows[0], dict):
                return dict(rows[0])
        except Exception:
            return {}
        return {}

    try:
        return await asyncio.to_thread(sync)
    except Exception:
        return {}


def _configured_overflow_ids(config: Dict[str, Any]) -> List[int]:
    keys = (
        "ticket_overflow_category_ids",
        "ticket_overflow_categories",
        "ticket_overflow_category_id",
        "ticket_active_overflow_category_id",
        "ticket_active_overflow_category_ids",
        "active_ticket_overflow_category_id",
        "active_ticket_overflow_category_ids",
        "ticket_overflow_1_category_id",
        "ticket_overflow_2_category_id",
        "ticket_overflow_3_category_id",
        "overflow_ticket_category_id",
        "overflow_ticket_category_ids",
    )
    ids = _config_values(config, keys)
    for env_name in (
        "DANK_TICKET_OVERFLOW_CATEGORY_IDS",
        "DANK_TICKET_OVERFLOW_CATEGORY_IDS",
        "TICKET_OVERFLOW_CATEGORY_IDS",
    ):
        for cid in _parse_ids(os.getenv(env_name, "")):
            if cid not in ids:
                ids.append(cid)
    return ids


def _category_by_id(guild: discord.Guild, cid: int) -> Optional[discord.CategoryChannel]:
    try:
        ch = guild.get_channel(int(cid))
        return ch if isinstance(ch, discord.CategoryChannel) else None
    except Exception:
        return None


def _dedupe_categories(items: Iterable[Optional[discord.CategoryChannel]]) -> List[discord.CategoryChannel]:
    out: List[discord.CategoryChannel] = []
    seen: set[int] = set()
    for item in items:
        if not isinstance(item, discord.CategoryChannel):
            continue
        try:
            cid = int(item.id)
        except Exception:
            continue
        if cid in seen:
            continue
        seen.add(cid)
        out.append(item)
    return out


def _detected_overflow_categories(guild: discord.Guild, primary: Optional[discord.CategoryChannel]) -> List[discord.CategoryChannel]:
    try:
        cats = list(guild.categories or [])
    except Exception:
        cats = []
    found = [cat for cat in cats if _looks_like_overflow(cat, primary)]
    found.sort(key=lambda c: (getattr(c, "position", 999), _safe_str(c.name).lower()))
    return found


def _candidate_categories(guild: discord.Guild, primary: Optional[discord.CategoryChannel], config: Dict[str, Any]) -> List[discord.CategoryChannel]:
    configured = [_category_by_id(guild, cid) for cid in _configured_overflow_ids(config)]
    detected = _detected_overflow_categories(guild, primary)
    return _dedupe_categories([primary, *configured, *detected])


def _overflow_name(primary: Optional[discord.CategoryChannel], index: int) -> str:
    base = "Tickets"
    if isinstance(primary, discord.CategoryChannel):
        raw = _safe_str(primary.name, "Tickets")
        base = raw[:76] if raw else "Tickets"
        base = re.sub(r"\s+Overflow\s*\d*$", "", base, flags=re.I).strip() or "Tickets"
    return f"{base} Overflow {int(index)}"[:100]


def _next_overflow_index(guild: discord.Guild, primary: Optional[discord.CategoryChannel]) -> int:
    existing = _detected_overflow_categories(guild, primary)
    return min(len(existing) + 1, _max_overflow_categories())


def _can_create_category(guild: discord.Guild) -> bool:
    try:
        me = guild.me
        if not isinstance(me, discord.Member):
            return False
        perms = me.guild_permissions
        return bool(perms.manage_channels)
    except Exception:
        return False


async def _create_overflow_category(guild: discord.Guild, primary: Optional[discord.CategoryChannel]) -> Optional[discord.CategoryChannel]:
    if not _auto_create_enabled():
        return None
    if _max_overflow_categories() <= 0:
        return None
    if not _can_create_category(guild):
        return None

    existing = _detected_overflow_categories(guild, primary)
    if len(existing) >= _max_overflow_categories():
        return None

    index = _next_overflow_index(guild, primary)
    name = _overflow_name(primary, index)

    overwrites = None
    try:
        if isinstance(primary, discord.CategoryChannel):
            overwrites = dict(primary.overwrites)
    except Exception:
        overwrites = None

    try:
        category = await guild.create_category_channel(
            name=name,
            overwrites=overwrites,
            reason="Dank Shield ticket overflow category created automatically",
        )
        try:
            if isinstance(primary, discord.CategoryChannel):
                await category.edit(position=int(primary.position) + index, reason="Position ticket overflow near active tickets")
        except Exception:
            pass
        _log(f"created overflow category guild={guild.id} category={category.id} name={category.name!r}")
        return category
    except Exception as e:
        _warn(f"failed creating overflow category guild={guild.id}: {type(e).__name__}: {e}")
        return None


async def resolve_ticket_category(guild: discord.Guild, primary: Optional[discord.CategoryChannel]) -> Optional[discord.CategoryChannel]:
    if not isinstance(guild, discord.Guild):
        return primary

    async with _guild_lock(int(guild.id)):
        me = guild.me
        config = await _read_guild_config(int(guild.id))
        candidates = _candidate_categories(guild, primary, config)

        for candidate in candidates:
            if _category_is_usable(candidate, me=me):
                if isinstance(primary, discord.CategoryChannel) and int(candidate.id) != int(primary.id):
                    _log(
                        f"using overflow category guild={guild.id} primary={primary.id} "
                        f"primary_count={_child_count(primary)} selected={candidate.id} selected_count={_child_count(candidate)}"
                    )
                return candidate

        created = await _create_overflow_category(guild, primary)
        if _category_is_usable(created, me=me):
            return created

        # Last resort: return primary so the original caller can show its normal
        # missing-permissions/full-category error instead of hiding the problem.
        return primary


def _find_primary_active_category_sync(service_mod: Any, guild: discord.Guild, explicit_parent_category_id: Optional[int]) -> Optional[discord.CategoryChannel]:
    try:
        resolver = getattr(service_mod, "_resolve_ticket_parent_category", None)
        if callable(resolver):
            return resolver(guild, explicit_parent_category_id=explicit_parent_category_id)
    except TypeError:
        try:
            return service_mod._resolve_ticket_parent_category(guild, explicit_parent_category_id)
        except Exception:
            return None
    except Exception:
        return None
    return None


def _wrap_service_create() -> bool:
    try:
        from ..tickets_new import service as service_mod
    except Exception as e:
        _warn(f"could not import tickets_new.service: {e!r}")
        return False

    original = getattr(service_mod, "create_ticket_channel", None)
    if not callable(original) or getattr(original, "_overflow_category_wrapped", False):
        return False

    async def wrapped_create_ticket_channel(*args: Any, **kwargs: Any):
        guild = kwargs.get("guild")
        if not isinstance(guild, discord.Guild):
            return await original(*args, **kwargs)

        explicit = kwargs.get("parent_category_id")
        primary = _find_primary_active_category_sync(service_mod, guild, _safe_int(explicit, 0) or None)
        selected = await resolve_ticket_category(guild, primary)
        if isinstance(selected, discord.CategoryChannel):
            kwargs["parent_category_id"] = int(selected.id)
        return await original(*args, **kwargs)

    setattr(wrapped_create_ticket_channel, "_overflow_category_wrapped", True)
    service_mod.create_ticket_channel = wrapped_create_ticket_channel
    return True


def _wrap_service_reopen_move() -> bool:
    try:
        from ..tickets_new import service as service_mod
    except Exception:
        return False

    original = getattr(service_mod, "_move_ticket_to_active_if_configured", None)
    if not callable(original) or getattr(original, "_overflow_category_wrapped", False):
        return False

    async def wrapped_move(channel: discord.TextChannel) -> bool:
        try:
            primary = service_mod._resolve_active_ticket_category(channel.guild)
            selected = await resolve_ticket_category(channel.guild, primary)
            if isinstance(selected, discord.CategoryChannel):
                if service_mod._channel_is_in_category(channel, selected):
                    return True
                await channel.edit(
                    category=selected,
                    sync_permissions=False,
                    reason="Ticket reopened -> move to active/overflow category",
                )
                return True
        except Exception as e:
            _warn(f"overflow active move failed channel={getattr(channel, 'id', 0)}: {type(e).__name__}: {e}")
        return await original(channel)

    setattr(wrapped_move, "_overflow_category_wrapped", True)
    service_mod._move_ticket_to_active_if_configured = wrapped_move
    return True


def _wrap_public_panel_active_category() -> bool:
    try:
        from ..commands_ext import public_ticket_panel_clean as panel_mod
    except Exception as e:
        _warn(f"could not import public_ticket_panel_clean: {e!r}")
        return False

    original = getattr(panel_mod, "_active_category", None)
    if not callable(original) or getattr(original, "_overflow_category_wrapped", False):
        return False

    async def wrapped_active_category(guild: discord.Guild):
        primary = await original(guild)
        if not isinstance(primary, discord.CategoryChannel):
            return primary
        return await resolve_ticket_category(guild, primary)

    setattr(wrapped_active_category, "_overflow_category_wrapped", True)
    panel_mod._active_category = wrapped_active_category
    return True


def apply() -> bool:
    wrapped = 0
    try:
        if _wrap_service_create():
            wrapped += 1
    except Exception as e:
        _warn(f"service create wrapper failed: {e!r}")

    try:
        if _wrap_service_reopen_move():
            wrapped += 1
    except Exception as e:
        _warn(f"service reopen move wrapper failed: {e!r}")

    try:
        if _wrap_public_panel_active_category():
            wrapped += 1
    except Exception as e:
        _warn(f"public panel wrapper failed: {e!r}")

    _log(f"installed overflow category support wrapped={wrapped} soft_limit={_soft_limit()} auto_create={_auto_create_enabled()}")
    return wrapped > 0


apply()

__all__ = ["apply", "resolve_ticket_category"]
