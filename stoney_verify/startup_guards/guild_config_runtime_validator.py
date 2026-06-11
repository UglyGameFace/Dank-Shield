from __future__ import annotations

"""Validate per-guild runtime config against the live Discord guild.

Public-bot rule: a saved Discord snowflake is only valid if it exists in the
current guild and has the expected resource type. Old copied IDs must be treated
as blank so runtime discovery/setup can repair the server instead of leaking one
server's config into another.
"""

import asyncio
from typing import Any, Mapping

import discord

_PATCHED = False
_ORIGINAL_DISCOVER = None

_ROLE_KEYS: tuple[str, ...] = (
    "unverified_role_id",
    "verified_role_id",
    "resident_role_id",
    "staff_role_id",
    "vc_staff_role_id",
)

_TEXT_CHANNEL_KEYS: tuple[str, ...] = (
    "verify_channel_id",
    "vc_verify_queue_channel_id",
    "transcripts_channel_id",
    "modlog_channel_id",
    "raidlog_channel_id",
    "join_log_channel_id",
    "force_verify_log_channel_id",
    "status_channel_id",
    "bot_status_channel_id",
    "uptime_channel_id",
    "health_channel_id",
)

_VOICE_CHANNEL_KEYS: tuple[str, ...] = (
    "vc_verify_channel_id",
)

_CATEGORY_KEYS: tuple[str, ...] = (
    "ticket_category_id",
    "ticket_archive_category_id",
)

_ALL_KEYS: tuple[str, ...] = _ROLE_KEYS + _TEXT_CHANNEL_KEYS + _VOICE_CHANNEL_KEYS + _CATEGORY_KEYS


def _log(message: str) -> None:
    try:
        print(f"🧭 guild_config_runtime_validator {message}")
    except Exception:
        pass


def _warn(message: str) -> None:
    try:
        print(f"⚠️ guild_config_runtime_validator {message}")
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


def _voice_types() -> tuple[type, ...]:
    items: list[type] = [discord.VoiceChannel]
    stage = getattr(discord, "StageChannel", None)
    if stage is not None:
        items.append(stage)
    return tuple(items)


def _valid_channel(guild: discord.Guild, resource_id: int, expected: str) -> bool:
    try:
        channel = guild.get_channel(int(resource_id))
    except Exception:
        channel = None
    if channel is None:
        return False
    if expected == "text":
        return isinstance(channel, discord.TextChannel)
    if expected == "voice":
        return isinstance(channel, _voice_types())
    if expected == "category":
        return isinstance(channel, discord.CategoryChannel)
    return False


def _invalid_saved_ids(guild: discord.Guild, config: Mapping[str, Any]) -> dict[str, str]:
    invalid: dict[str, str] = {}

    for key in _ROLE_KEYS:
        rid = _safe_int(config.get(key), 0)
        if rid > 0 and guild.get_role(rid) is None:
            invalid[key] = str(rid)

    for key in _TEXT_CHANNEL_KEYS:
        cid = _safe_int(config.get(key), 0)
        if cid > 0 and not _valid_channel(guild, cid, "text"):
            invalid[key] = str(cid)

    for key in _VOICE_CHANNEL_KEYS:
        cid = _safe_int(config.get(key), 0)
        if cid > 0 and not _valid_channel(guild, cid, "voice"):
            invalid[key] = str(cid)

    for key in _CATEGORY_KEYS:
        cid = _safe_int(config.get(key), 0)
        if cid > 0 and not _valid_channel(guild, cid, "category"):
            invalid[key] = str(cid)

    return invalid


def _mapping(value: Any) -> dict[str, Any]:
    try:
        if isinstance(value, Mapping):
            return dict(value)
    except Exception:
        pass
    return {}


def _strip_invalid_nested(value: Any, invalid_keys: set[str]) -> dict[str, Any]:
    out = _mapping(value)
    for key in invalid_keys:
        out.pop(key, None)
    return out


def _purge_invalid_ids_sync(guild_id: int, invalid: Mapping[str, str]) -> None:
    if not invalid:
        return
    try:
        from stoney_verify import guild_config as gc
        from stoney_verify.globals import get_supabase

        sb = get_supabase()
        if sb is None:
            return
        table = str(getattr(gc, "GUILD_CONFIG_TABLE", "guild_configs") or "guild_configs")
        gid = str(int(guild_id))
        res = sb.table(table).select("*").eq("guild_id", gid).limit(1).execute()
        rows = getattr(res, "data", None) or []
        if not rows or not isinstance(rows[0], Mapping):
            return
        row = dict(rows[0])
        columns = {str(k) for k in row.keys()}
        invalid_keys = set(str(k) for k in invalid.keys())

        flat_clear = {key: None for key in invalid_keys if key in columns}
        nested_updates: dict[str, Any] = {}
        for key in ("settings", "config", "metadata", "meta"):
            if key in columns:
                nested_updates[key] = _strip_invalid_nested(row.get(key), invalid_keys)

        payloads: list[dict[str, Any]] = []
        if flat_clear or nested_updates:
            payloads.append({**nested_updates, **flat_clear})
        if nested_updates:
            payloads.append(nested_updates)
        if flat_clear:
            payloads.append(flat_clear)

        for payload in payloads:
            try:
                sb.table(table).update(payload).eq("guild_id", gid).execute()
                try:
                    gc.invalidate_guild_config(gid)
                except Exception:
                    pass
                _warn(f"purged invalid saved IDs guild={guild_id} fields={sorted(invalid_keys)}")
                return
            except Exception:
                continue
    except Exception as e:
        _warn(f"failed purging invalid IDs guild={guild_id}: {e!r}")


async def _purge_invalid_ids(guild_id: int, invalid: Mapping[str, str]) -> None:
    try:
        await asyncio.to_thread(_purge_invalid_ids_sync, int(guild_id), dict(invalid))
    except Exception:
        pass


def _find_role_by_names(guild: discord.Guild, names: list[str]) -> discord.Role | None:
    wanted = [n.lower().strip() for n in names if n.strip()]
    try:
        for role in guild.roles:
            name = str(role.name or "").lower().strip()
            if name in wanted:
                return role
        for role in guild.roles:
            name = str(role.name or "").lower().strip()
            if any(w in name for w in wanted):
                return role
    except Exception:
        return None
    return None


def _find_text_channel_by_names(guild: discord.Guild, names: list[str]) -> discord.TextChannel | None:
    wanted = [n.lower().strip() for n in names if n.strip()]
    try:
        for ch in guild.text_channels:
            name = str(ch.name or "").lower().strip()
            if name in wanted:
                return ch
        for ch in guild.text_channels:
            name = str(ch.name or "").lower().strip()
            if any(w in name for w in wanted):
                return ch
    except Exception:
        return None
    return None


def _find_category_by_names(guild: discord.Guild, names: list[str]) -> discord.CategoryChannel | None:
    wanted = [n.lower().strip() for n in names if n.strip()]
    try:
        for cat in guild.categories:
            name = str(cat.name or "").lower().strip()
            if name in wanted:
                return cat
        for cat in guild.categories:
            name = str(cat.name or "").lower().strip()
            if any(w in name for w in wanted):
                return cat
    except Exception:
        return None
    return None


def _apply_runtime_discovery(guild: discord.Guild, cfg: dict[str, Any]) -> dict[str, Any]:
    discovered: dict[str, Any] = {}

    if not cfg.get("staff_role_id"):
        role = _find_role_by_names(guild, ["staff", "ticket staff", "mod", "moderator", "admin", "support"])
        if role:
            discovered["staff_role_id"] = str(role.id)
            discovered["vc_staff_role_id"] = str(role.id)

    if not cfg.get("verified_role_id"):
        role = _find_role_by_names(guild, ["verified", "member", "resident"])
        if role:
            discovered["verified_role_id"] = str(role.id)

    if not cfg.get("unverified_role_id"):
        role = _find_role_by_names(guild, ["unverified", "not verified", "pending"])
        if role:
            discovered["unverified_role_id"] = str(role.id)

    if not cfg.get("resident_role_id"):
        role = _find_role_by_names(guild, ["resident"])
        if role:
            discovered["resident_role_id"] = str(role.id)

    if not cfg.get("modlog_channel_id"):
        ch = _find_text_channel_by_names(guild, ["mod-log", "modlog", "logs", "staff-log", "staff-logs"])
        if ch:
            discovered["modlog_channel_id"] = str(ch.id)

    if not cfg.get("transcripts_channel_id"):
        ch = _find_text_channel_by_names(guild, ["transcripts", "ticket-transcripts", "ticket-logs"])
        if ch:
            discovered["transcripts_channel_id"] = str(ch.id)

    if not cfg.get("verify_channel_id"):
        ch = _find_text_channel_by_names(guild, ["verify", "verification", "unverified-chat"])
        if ch:
            discovered["verify_channel_id"] = str(ch.id)

    if not cfg.get("ticket_category_id"):
        cat = _find_category_by_names(guild, ["tickets", "support", "verification tickets"])
        if cat:
            discovered["ticket_category_id"] = str(cat.id)

    if discovered:
        cfg.update(discovered)
        cfg["runtime_discovered_fields"] = sorted(discovered.keys())
    return cfg


async def _validated_discover_runtime_guild_config(guild: discord.Guild):
    from stoney_verify import guild_config as gc

    config = await gc.get_guild_config(guild.id)
    cfg = dict(config)
    invalid = _invalid_saved_ids(guild, cfg)
    if invalid:
        for key in invalid.keys():
            cfg[key] = None
        cfg["invalid_saved_config_ids"] = dict(invalid)
        cfg["source"] = f"{cfg.get('source', 'unknown')}+validated_invalid_ids"
        await _purge_invalid_ids(int(guild.id), invalid)
        _warn(f"ignored invalid saved IDs guild={guild.id} fields={sorted(invalid.keys())}")

    if bool(cfg.get("allow_runtime_discovery", True)):
        cfg = _apply_runtime_discovery(guild, cfg)
        if cfg.get("runtime_discovered_fields"):
            cfg["source"] = f"{cfg.get('source', 'unknown')}+runtime_discovery"

    return gc.GuildRuntimeConfig(cfg)


def apply() -> bool:
    global _PATCHED, _ORIGINAL_DISCOVER
    if _PATCHED:
        return True
    try:
        from stoney_verify import guild_config as gc

        current = getattr(gc, "discover_runtime_guild_config", None)
        if not callable(current):
            _warn("guild_config.discover_runtime_guild_config missing")
            return False
        if getattr(current, "_guild_config_runtime_validator_wrapped", False):
            _PATCHED = True
            return True
        _ORIGINAL_DISCOVER = current
        setattr(_validated_discover_runtime_guild_config, "_guild_config_runtime_validator_wrapped", True)
        setattr(gc, "discover_runtime_guild_config", _validated_discover_runtime_guild_config)
        _PATCHED = True
        _log("active; saved Discord IDs are validated against each guild")
        return True
    except Exception as e:
        _warn(f"failed to patch runtime config discovery: {e!r}")
        return False


apply()

__all__ = ["apply"]
