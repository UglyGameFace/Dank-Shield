from __future__ import annotations

"""Whole-server structure backups: serializer + storage (Phase 3, Slice 1).

A structure snapshot is a portable JSON document of one guild's Discord
structure — categories, channels (with forum tags), roles, permission
overwrites and the editable guild settings — stored in
``public.dank_server_backups`` on the same Discloud PostgreSQL as the other
dank_* ledgers (see postgres/migrations/202611180002_server_backups.sql),
reached through the shared ``verification_reminder_postgres._connect``
helper, with sync cores wrapped in ``asyncio.to_thread`` exactly like the
membership link store.

Boundaries:

- Dank Shield's own guild config is NOT duplicated here. Config History
  (Supabase ``guild_config_versions``) versions it; a snapshot only records
  the newest config version id as a link (best-effort — ``None`` when the
  read is unavailable, never a blocker).
- This module never writes to Discord. Capturing a snapshot is a pure read,
  so it carries no Pro gate; the gate sits on the panel actions (Slice 3a).
  Restore planning/apply lives in ``server_backup_restore`` (Slice 2).
- Member permission overwrites are never captured (stale snowflakes +
  privacy); they are counted in the snapshot's ``omitted`` block instead.
"""

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any, Mapping, Optional

import discord

from stoney_verify.services.channel_builder_runtime import channel_kind
from stoney_verify.verification_reminder_postgres import _connect

logger = logging.getLogger(__name__)

BACKUPS_TABLE = "public.dank_server_backups"
MAX_BACKUPS_PER_GUILD = 10
MAX_SNAPSHOT_BYTES = 2 * 1024 * 1024  # serialized JSON ceiling (spec §1.5)
SNAPSHOT_SCHEMA_VERSION = 1
SNAPSHOT_KIND = "dank_server_structure_backup"

_METADATA_COLUMNS = (
    "backup_id, guild_id, created_by, label, created_at, "
    "schema_version, snapshot_bytes, restore_state"
)


class SnapshotTooLargeError(ValueError):
    """A serialized snapshot exceeded ``MAX_SNAPSHOT_BYTES``."""


# ---------------------------------------------------------------------------
# Small coercion helpers (duck-typed: real discord.py objects and test fakes)
# ---------------------------------------------------------------------------


def _int(value: Any, default: int = 0) -> int:
    try:
        if value is None or isinstance(value, bool):
            return int(default)
        return int(value)
    except (TypeError, ValueError):
        return int(default)


def _int_or_none(value: Any) -> Optional[int]:
    raw = getattr(value, "value", value)
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _enum_int(value: Any, default: Optional[int] = None) -> Optional[int]:
    return _int_or_none(value) if value is not None else default


def _str(value: Any, default: str = "") -> str:
    try:
        text = str(value or "").strip()
    except Exception:
        return default
    return text if text else default


def _optional_str(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value)
    return text if text else None


def _asset_hash(asset: Any) -> Optional[str]:
    """Icon/banner hashes are informational only; asset bytes never captured."""

    if asset is None:
        return None
    if isinstance(asset, str):
        return asset or None
    key = getattr(asset, "key", None)
    return str(key) if key else None


def _iso(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


# ---------------------------------------------------------------------------
# Serializer (spec §1.6) — pure reads of discord.py-shaped objects
# ---------------------------------------------------------------------------


def _is_default_role(role: Any, guild_id: int) -> bool:
    marker = getattr(role, "is_default", None)
    if callable(marker):
        try:
            return bool(marker())
        except Exception:
            pass
    elif isinstance(marker, bool):
        return marker
    return guild_id > 0 and _int(getattr(role, "id", 0)) == guild_id


def _role_key(role: Any, guild_id: int) -> str:
    if _is_default_role(role, guild_id):
        return "role:@everyone"
    return f"role:{_int(getattr(role, 'id', 0))}"


def _permission_names(perms: Any) -> list[str]:
    out: list[str] = []
    try:
        for name, enabled in perms:
            if enabled:
                out.append(str(name))
    except Exception:
        pass
    return sorted(set(out))


def _overwrite_pair(overwrite: Any) -> dict[str, list[str]]:
    # The exact {allow, deny} name-list shape of
    # permission_repair_core._overwrite_snapshot (spec §1.2).
    try:
        allow, deny = overwrite.pair()
        return {"allow": _permission_names(allow), "deny": _permission_names(deny)}
    except Exception:
        return {"allow": [], "deny": []}


def _overwrite_role_key(target: Any, guild_id: int) -> Optional[str]:
    """Snapshot key for a role overwrite target; None for member targets."""

    if isinstance(target, discord.Role):
        return _role_key(target, guild_id)
    if isinstance(target, discord.Member):
        return None
    # Duck-typed fallback (test fakes, future discord.py shapes): roles
    # carry hoist/permissions, members do not.
    if hasattr(target, "hoist") and hasattr(target, "permissions"):
        return _role_key(target, guild_id)
    return None


def _serialize_overwrites(source: Any, guild_id: int, omitted: dict[str, int]) -> list[dict[str, Any]]:
    raw = getattr(source, "overwrites", None) or {}
    items = raw.items() if hasattr(raw, "items") else ()
    out: list[dict[str, Any]] = []
    for target, overwrite in items:
        target_key = _overwrite_role_key(target, guild_id)
        if target_key is None:
            omitted["member_overwrites"] += 1
            continue
        pair = _overwrite_pair(overwrite)
        out.append(
            {
                "target_type": "role",
                "target_key": target_key,
                "allow": pair["allow"],
                "deny": pair["deny"],
            }
        )
    out.sort(key=lambda row: row["target_key"])
    return out


def _serialize_role(role: Any, guild_id: int) -> dict[str, Any]:
    colour = getattr(role, "colour", None)
    if colour is None:
        colour = getattr(role, "color", None)
    permissions = getattr(role, "permissions", 0)
    return {
        "key": _role_key(role, guild_id),
        "source_id": str(_int(getattr(role, "id", 0))),
        "name": _str(getattr(role, "name", "")),
        "color": _int(getattr(colour, "value", colour)),
        "hoist": bool(getattr(role, "hoist", False)),
        "mentionable": bool(getattr(role, "mentionable", False)),
        "position": _int(getattr(role, "position", 0)),
        # Lossless: the raw permissions bitfield, restored via
        # discord.Permissions(int(value)) — never a name list.
        "permissions": str(_int(getattr(permissions, "value", permissions))),
        "managed": bool(getattr(role, "managed", False)),
        "is_default": _is_default_role(role, guild_id),
    }


_KIND_TO_TYPE = {"text": "text", "news": "news", "voice": "voice", "forum": "forum"}
_RAW_TYPE_TO_TYPE = {0: "text", 2: "voice", 5: "news", 13: "stage", 15: "forum", 16: "forum"}


def _is_category(channel: Any) -> bool:
    if isinstance(channel, discord.CategoryChannel):
        return True
    try:
        if channel_kind(channel) == "category":
            return True
    except Exception:
        pass
    raw = getattr(channel, "type", None)
    return _int(getattr(raw, "value", raw), -1) == 4


def _channel_type(channel: Any) -> Optional[str]:
    """One of text|news|voice|forum|stage, or None (category / unknown)."""

    kind = ""
    try:
        kind = channel_kind(channel)
    except Exception:
        kind = ""
    if kind in _KIND_TO_TYPE:
        return _KIND_TO_TYPE[kind]
    if kind == "category":
        return None
    # channel_kind() has no stage branch (and media forums classify as
    # unknown), so fall back to the raw Discord channel type.
    raw = getattr(channel, "type", None)
    value = getattr(raw, "value", raw)
    if isinstance(value, int) and value in _RAW_TYPE_TO_TYPE:
        return _RAW_TYPE_TO_TYPE[value]
    text = str(raw or "").strip().lower()
    if text in {"stage", "stage_voice"}:
        return "stage"
    if text in _KIND_TO_TYPE:
        return text
    return None


def _emoji_name(emoji: Any) -> Optional[str]:
    """Unicode emoji keep their name; custom-emoji ids are guild-scoped and
    non-portable, so they serialize as None (spec §1.6)."""

    if emoji is None:
        return None
    if isinstance(emoji, str):
        return emoji or None
    if _int(getattr(emoji, "id", 0)) > 0:
        return None
    name = getattr(emoji, "name", None)
    return str(name) if name else None


def _serialize_forum(channel: Any) -> dict[str, Any]:
    tags: list[dict[str, Any]] = []
    for tag in list(getattr(channel, "available_tags", None) or []):
        tags.append(
            {
                "name": _str(getattr(tag, "name", "")),
                "moderated": bool(getattr(tag, "moderated", False)),
                "emoji": _emoji_name(getattr(tag, "emoji", None)),
            }
        )
    layout = getattr(channel, "default_layout", None)
    if layout is None:
        layout = getattr(channel, "default_forum_layout", None)
    return {
        "available_tags": tags,
        "default_sort_order": _enum_int(getattr(channel, "default_sort_order", None)),
        "default_forum_layout": _enum_int(layout),
        "default_thread_slowmode_delay": _int(getattr(channel, "default_thread_slowmode_delay", 0)),
        "default_reaction_emoji": _emoji_name(getattr(channel, "default_reaction_emoji", None)),
        "default_auto_archive_duration": _int(
            getattr(channel, "default_auto_archive_duration", 1440), 1440
        ),
    }


def _serialize_category(category: Any, guild_id: int, omitted: dict[str, int]) -> dict[str, Any]:
    return {
        "key": f"cat:{_int(getattr(category, 'id', 0))}",
        "source_id": str(_int(getattr(category, "id", 0))),
        "name": _str(getattr(category, "name", "")),
        "position": _int(getattr(category, "position", 0)),
        "nsfw": bool(getattr(category, "nsfw", False)),
        "overwrites": _serialize_overwrites(category, guild_id, omitted),
    }


def _serialize_channel(channel: Any, guild_id: int, omitted: dict[str, int]) -> Optional[dict[str, Any]]:
    channel_type = _channel_type(channel)
    if channel_type is None:
        return None
    if channel_type == "stage":
        # Captured for completeness, flagged here; v1 restore plans stage
        # channels as blocked (no creation precedent in the codebase).
        omitted["stage_channels"] += 1
    category = getattr(channel, "category", None)
    category_id = _int(getattr(category, "id", 0)) if category is not None else 0
    return {
        "key": f"chan:{_int(getattr(channel, 'id', 0))}",
        "source_id": str(_int(getattr(channel, "id", 0))),
        "type": channel_type,
        "name": _str(getattr(channel, "name", "")),
        "category_key": f"cat:{category_id}" if category_id > 0 else None,
        "position": _int(getattr(channel, "position", 0)),
        "topic": _optional_str(getattr(channel, "topic", None)),
        "nsfw": bool(getattr(channel, "nsfw", False)),
        "slowmode_delay": _int(getattr(channel, "slowmode_delay", 0)),
        "bitrate": _int_or_none(getattr(channel, "bitrate", None)),
        "user_limit": _int_or_none(getattr(channel, "user_limit", None)),
        "rtc_region": _optional_str(getattr(channel, "rtc_region", None)),
        "video_quality_mode": _enum_int(getattr(channel, "video_quality_mode", None)),
        "forum": _serialize_forum(channel) if channel_type == "forum" else None,
        "overwrites": _serialize_overwrites(channel, guild_id, omitted),
    }


def _channel_ref_key(channel: Any) -> Optional[str]:
    if channel is None:
        return None
    channel_id = _int(getattr(channel, "id", 0))
    return f"chan:{channel_id}" if channel_id > 0 else None


def _serialize_guild_block(guild: Any, guild_id: int) -> dict[str, Any]:
    locale = getattr(guild, "preferred_locale", None)
    return {
        "source_id": str(guild_id),
        "name": _str(getattr(guild, "name", "")),
        "description": _optional_str(getattr(guild, "description", None)),
        "preferred_locale": str(locale) if locale else None,
        "icon_hash": _asset_hash(getattr(guild, "icon", None)),
        "banner_hash": _asset_hash(getattr(guild, "banner", None)),
        "features": [str(feature) for feature in (getattr(guild, "features", None) or [])],
        "verification_level": _enum_int(getattr(guild, "verification_level", None), 0),
        "explicit_content_filter": _enum_int(getattr(guild, "explicit_content_filter", None), 0),
        "default_notifications": _enum_int(getattr(guild, "default_notifications", None), 0),
        "afk_timeout": _int(getattr(guild, "afk_timeout", 0)),
        "afk_channel_key": _channel_ref_key(getattr(guild, "afk_channel", None)),
        "system_channel_key": _channel_ref_key(getattr(guild, "system_channel", None)),
        "rules_channel_key": _channel_ref_key(getattr(guild, "rules_channel", None)),
        "public_updates_channel_key": _channel_ref_key(getattr(guild, "public_updates_channel", None)),
    }


def serialize_guild_structure(
    guild: Any,
    *,
    actor_id: int = 0,
    channels: Optional[list] = None,
    config_history_version_id: Optional[int] = None,
    captured_at: Optional[datetime] = None,
) -> dict[str, Any]:
    """Serialize one guild's structure into the §1.6 snapshot document.

    Pure function of the guild-shaped object it is given; ``channels`` lets
    the caller pass a freshly fetched channel list (see capture_snapshot).
    """

    guild_id = _int(getattr(guild, "id", 0))
    omitted = {"member_overwrites": 0, "stage_channels": 0}
    roles = [_serialize_role(role, guild_id) for role in list(getattr(guild, "roles", None) or [])]
    all_channels = (
        list(channels)
        if channels is not None
        else list(getattr(guild, "channels", None) or [])
    )
    category_objects = list(getattr(guild, "categories", None) or [])
    if not category_objects:
        category_objects = [channel for channel in all_channels if _is_category(channel)]
    categories = [
        _serialize_category(category, guild_id, omitted)
        for category in sorted(
            category_objects,
            key=lambda item: (_int(getattr(item, "position", 0)), _int(getattr(item, "id", 0))),
        )
    ]
    channel_rows: list[dict[str, Any]] = []
    for channel in all_channels:
        row = _serialize_channel(channel, guild_id, omitted)
        if row is not None:
            channel_rows.append(row)
    return {
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "kind": SNAPSHOT_KIND,
        "captured_at": (captured_at or datetime.now(timezone.utc)).isoformat(),
        "captured_by": str(_int(actor_id)),
        "guild": _serialize_guild_block(guild, guild_id),
        "roles": roles,
        "categories": categories,
        "channels": channel_rows,
        "omitted": omitted,
        "dank_config": {
            "config_history_version_id": (
                int(config_history_version_id) if config_history_version_id else None
            ),
            "note": (
                "Dank Shield settings are versioned by Config History; "
                "restore links there instead of copying config."
            ),
        },
    }


async def capture_snapshot(guild: Any, *, actor_id: int = 0) -> dict[str, Any]:
    """Capture a fresh snapshot of ``guild`` (pure read; no Discord writes).

    Uses ``guild.fetch_channels()`` when available (the fresh_channel_map
    discipline) and links the newest Config History version id on a
    best-effort basis — a failed link read leaves the id None (spec R8).
    """

    channels: Optional[list] = None
    fetch = getattr(guild, "fetch_channels", None)
    if callable(fetch):
        try:
            rows = await fetch()
            channels = list(rows or [])
        except Exception:
            channels = None
    version_id: Optional[int] = None
    try:
        from stoney_verify import config_history

        versions = await config_history.list_config_versions(
            _int(getattr(guild, "id", 0)), 1
        )
        if versions:
            version_id = _int(versions[0].get("version_id")) or None
    except Exception:
        version_id = None
    return serialize_guild_structure(
        guild,
        actor_id=actor_id,
        channels=channels,
        config_history_version_id=version_id,
    )


# ---------------------------------------------------------------------------
# Storage (Discloud PostgreSQL) — sync cores + asyncio.to_thread wrappers
# ---------------------------------------------------------------------------


def _snapshot_payload(snapshot: Mapping[str, Any]) -> tuple[dict[str, Any], int]:
    """Serialize once; the byte count enforces the cap and fills the row."""

    document = dict(snapshot)
    text = json.dumps(document, ensure_ascii=False, sort_keys=True)
    return document, len(text.encode("utf-8"))


def _restore_running(state: Any) -> bool:
    if isinstance(state, str):
        try:
            state = json.loads(state)
        except ValueError:
            return False
    return isinstance(state, Mapping) and state.get("status") == "running"


def _metadata_row(
    backup_id: Any,
    guild_id: Any,
    created_by: Any,
    label: Any,
    created_at: Any,
    schema_version: Any,
    snapshot_bytes: Any,
    restore_state: Any,
) -> dict[str, Any]:
    return {
        "backup_id": _int(backup_id),
        "guild_id": _int(guild_id),
        "created_by": _int(created_by),
        "label": str(label or ""),
        "created_at": _iso(created_at),
        "schema_version": _int(schema_version, SNAPSHOT_SCHEMA_VERSION),
        "snapshot_bytes": _int(snapshot_bytes),
        "restore_state": restore_state if isinstance(restore_state, Mapping) else None,
    }


def backups_ready_sync() -> bool:
    """Prove the backups table exists with the privileges the service needs."""

    try:
        with _connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"select {_METADATA_COLUMNS} from {BACKUPS_TABLE} limit 0"
                )
                for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE"):
                    cursor.execute(
                        "select has_table_privilege(current_user, %s, %s)",
                        (BACKUPS_TABLE, privilege),
                    )
                    row = cursor.fetchone()
                    if not row or row[0] is not True:
                        return False
        return True
    except Exception:
        return False


def create_backup_sync(
    guild_id: int,
    *,
    created_by: int,
    label: str,
    snapshot: Mapping[str, Any],
) -> dict[str, Any]:
    """Insert one snapshot, enforcing the size ceiling and the per-guild cap.

    Cap mechanics (one transaction): insert, then delete the oldest rows
    beyond ``MAX_BACKUPS_PER_GUILD`` — except a row whose restore is in
    flight (``restore_state.status == "running"``), which is never deleted
    by the cap. Returns the stored row's metadata plus the snapshot body.
    """

    gid = _int(guild_id)
    if gid <= 0:
        raise ValueError("guild_id is required")
    if not isinstance(snapshot, Mapping):
        raise ValueError("snapshot must be a mapping")
    document, snapshot_bytes = _snapshot_payload(snapshot)
    if snapshot_bytes > MAX_SNAPSHOT_BYTES:
        raise SnapshotTooLargeError(
            f"snapshot is {snapshot_bytes} bytes; the limit is {MAX_SNAPSHOT_BYTES}"
        )
    from psycopg.types.json import Jsonb

    with _connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                f"insert into {BACKUPS_TABLE} "
                "(guild_id, created_by, label, schema_version, snapshot, snapshot_bytes) "
                "values (%s, %s, %s, %s, %s, %s) returning backup_id, created_at",
                (
                    gid,
                    _int(created_by),
                    str(label or ""),
                    _int(document.get("schema_version"), SNAPSHOT_SCHEMA_VERSION),
                    Jsonb(document),
                    snapshot_bytes,
                ),
            )
            inserted = cursor.fetchone()
            backup_id = _int(inserted[0]) if inserted else 0
            created_at = inserted[1] if inserted else None
            cursor.execute(
                f"select backup_id, restore_state from {BACKUPS_TABLE} "
                "where guild_id = %s order by created_at desc, backup_id desc",
                (gid,),
            )
            rows = cursor.fetchall()
            excess = [
                _int(row[0])
                for row in rows[MAX_BACKUPS_PER_GUILD:]
                if not _restore_running(row[1])
            ]
            if excess:
                cursor.execute(
                    f"delete from {BACKUPS_TABLE} "
                    "where guild_id = %s and backup_id = any(%s)",
                    (gid, excess),
                )
    result = _metadata_row(
        backup_id,
        gid,
        _int(created_by),
        str(label or ""),
        created_at,
        _int(document.get("schema_version"), SNAPSHOT_SCHEMA_VERSION),
        snapshot_bytes,
        None,
    )
    result["snapshot"] = document
    return result


def list_backups_sync(guild_id: int, *, limit: int = 10) -> tuple[dict[str, Any], ...]:
    """Backup metadata for one guild, newest first; never loads snapshots."""

    gid = _int(guild_id)
    if gid <= 0:
        return ()
    bounded = max(1, min(_int(limit, MAX_BACKUPS_PER_GUILD), MAX_BACKUPS_PER_GUILD))
    with _connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                f"select {_METADATA_COLUMNS} from {BACKUPS_TABLE} "
                "where guild_id = %s order by created_at desc, backup_id desc limit %s",
                (gid, bounded),
            )
            rows = cursor.fetchall()
    return tuple(_metadata_row(*row) for row in rows)


def get_backup_sync(guild_id: int, backup_id: int) -> Optional[dict[str, Any]]:
    """One full backup row (metadata + snapshot), scoped to its guild."""

    gid = _int(guild_id)
    bid = _int(backup_id)
    if gid <= 0 or bid <= 0:
        return None
    with _connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                f"select {_METADATA_COLUMNS}, snapshot from {BACKUPS_TABLE} "
                "where guild_id = %s and backup_id = %s",
                (gid, bid),
            )
            row = cursor.fetchone()
    if not row:
        return None
    result = _metadata_row(*row[:8])
    snapshot = row[8]
    if isinstance(snapshot, str):
        try:
            snapshot = json.loads(snapshot)
        except ValueError:
            snapshot = None
    result["snapshot"] = snapshot if isinstance(snapshot, Mapping) else None
    return result


def delete_backup_sync(guild_id: int, backup_id: int) -> bool:
    gid = _int(guild_id)
    bid = _int(backup_id)
    if gid <= 0 or bid <= 0:
        return False
    with _connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                f"delete from {BACKUPS_TABLE} where guild_id = %s and backup_id = %s",
                (gid, bid),
            )
            return int(getattr(cursor, "rowcount", 0) or 0) > 0


def update_restore_state_sync(
    guild_id: int, backup_id: int, state: Optional[Mapping[str, Any]]
) -> bool:
    """Write the resumability cursor for one backup (None clears it)."""

    gid = _int(guild_id)
    bid = _int(backup_id)
    if gid <= 0 or bid <= 0:
        return False
    from psycopg.types.json import Jsonb

    payload = Jsonb(dict(state)) if state is not None else None
    with _connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                f"update {BACKUPS_TABLE} set restore_state = %s "
                "where guild_id = %s and backup_id = %s",
                (payload, gid, bid),
            )
            return int(getattr(cursor, "rowcount", 0) or 0) > 0


async def create_backup(guild: Any, *, created_by: int, label: str) -> dict[str, Any]:
    """Capture a fresh snapshot of ``guild`` and store it under the cap."""

    snapshot = await capture_snapshot(guild, actor_id=created_by)
    return await asyncio.to_thread(
        create_backup_sync,
        _int(getattr(guild, "id", 0)),
        created_by=created_by,
        label=label,
        snapshot=snapshot,
    )


async def list_backups(guild_id: int, *, limit: int = 10) -> tuple[dict[str, Any], ...]:
    try:
        return await asyncio.to_thread(list_backups_sync, guild_id, limit=limit)
    except Exception:
        logger.warning("server backup list failed for guild %s", guild_id, exc_info=True)
        return ()


async def get_backup(guild_id: int, backup_id: int) -> Optional[dict[str, Any]]:
    try:
        return await asyncio.to_thread(get_backup_sync, guild_id, backup_id)
    except Exception:
        logger.warning(
            "server backup fetch failed for guild %s backup %s",
            guild_id,
            backup_id,
            exc_info=True,
        )
        return None


async def delete_backup(guild_id: int, backup_id: int) -> bool:
    try:
        return await asyncio.to_thread(delete_backup_sync, guild_id, backup_id)
    except Exception:
        logger.warning(
            "server backup delete failed for guild %s backup %s",
            guild_id,
            backup_id,
            exc_info=True,
        )
        return False


async def update_restore_state(
    guild_id: int, backup_id: int, state: Optional[Mapping[str, Any]]
) -> bool:
    try:
        return await asyncio.to_thread(update_restore_state_sync, guild_id, backup_id, state)
    except Exception:
        logger.warning(
            "server backup restore-state update failed for guild %s backup %s",
            guild_id,
            backup_id,
            exc_info=True,
        )
        return False


__all__ = [
    "BACKUPS_TABLE",
    "MAX_BACKUPS_PER_GUILD",
    "MAX_SNAPSHOT_BYTES",
    "SNAPSHOT_KIND",
    "SNAPSHOT_SCHEMA_VERSION",
    "SnapshotTooLargeError",
    "backups_ready_sync",
    "capture_snapshot",
    "create_backup",
    "create_backup_sync",
    "delete_backup",
    "delete_backup_sync",
    "get_backup",
    "get_backup_sync",
    "list_backups",
    "list_backups_sync",
    "serialize_guild_structure",
    "update_restore_state",
    "update_restore_state_sync",
]
