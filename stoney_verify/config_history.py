from __future__ import annotations

"""Durable guild configuration backup, history, and restore service.

The automatic snapshot owner is the Supabase trigger installed by
``20260720_guild_config_version_history.sql``. This module provides the bot-side
read/manual-backup/restore API without patching config writers or introducing a
parallel configuration source of truth.
"""

import asyncio
import os
from datetime import datetime, timezone
from typing import Any, Mapping, Optional

from .globals import get_supabase, now_utc
from .guild_config import GUILD_CONFIG_TABLE_FALLBACKS, clear_guild_config_cache

CONFIG_HISTORY_TABLE = (
    os.getenv("DANK_GUILD_CONFIG_HISTORY_TABLE") or "guild_config_versions"
).strip() or "guild_config_versions"
CONFIG_HISTORY_RETENTION = 50

_RESTORE_EXCLUDED_KEYS = {
    "guild_id",
    "created_at",
    "updated_at",
    "_source_table",
}
_COMPARISON_EXCLUDED_KEYS = {
    "created_at",
    "updated_at",
    "config_last_write_at",
    "config_last_write_source",
    "config_last_write_mode",
    "config_last_write_actor_id",
    "config_last_write_reason",
    "config_restored_from_version_id",
}


def _utc_iso() -> str:
    try:
        return now_utc().isoformat()
    except Exception:
        return datetime.now(timezone.utc).isoformat()


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or isinstance(value, bool):
            return int(default)
        return int(str(value).strip())
    except Exception:
        return int(default)


def _safe_str(value: Any, default: str = "") -> str:
    try:
        text = str(value or "").strip()
        return text if text else default
    except Exception:
        return default


def _row_dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _is_missing_table_error(error: Exception) -> bool:
    text = repr(error).lower()
    return any(
        marker in text
        for marker in (
            "does not exist",
            "relation",
            "42p01",
            "pgrst205",
            "could not find the table",
        )
    )


def _require_supabase() -> Any:
    sb = get_supabase()
    if sb is None:
        raise RuntimeError("Supabase is not configured/available.")
    return sb


def _fetch_current_config_row_sync(guild_id: int) -> tuple[str, dict[str, Any]]:
    sb = _require_supabase()
    gid = str(int(guild_id))
    last_error: Optional[Exception] = None

    for table_name in GUILD_CONFIG_TABLE_FALLBACKS:
        try:
            response = (
                sb.table(table_name)
                .select("*")
                .eq("guild_id", gid)
                .limit(1)
                .execute()
            )
            rows = getattr(response, "data", None) or []
            if rows and isinstance(rows[0], Mapping):
                return table_name, dict(rows[0])
        except Exception as exc:
            last_error = exc
            if _is_missing_table_error(exc):
                continue
            raise

    if last_error is not None and not _is_missing_table_error(last_error):
        raise last_error
    raise LookupError(f"No saved guild configuration exists for guild {gid}.")


def _fetch_version_sync(guild_id: int, version_id: int) -> dict[str, Any]:
    sb = _require_supabase()
    try:
        response = (
            sb.table(CONFIG_HISTORY_TABLE)
            .select("*")
            .eq("guild_id", str(int(guild_id)))
            .eq("version_id", int(version_id))
            .limit(1)
            .execute()
        )
    except Exception as exc:
        if _is_missing_table_error(exc):
            raise RuntimeError(
                "Configuration history is not installed yet. Apply the guild config version-history migration first."
            ) from exc
        raise

    rows = getattr(response, "data", None) or []
    if not rows or not isinstance(rows[0], Mapping):
        raise LookupError(
            f"Configuration version {int(version_id)} was not found for guild {int(guild_id)}."
        )
    return dict(rows[0])


def _list_config_versions_sync(guild_id: int, limit: int = 10) -> list[dict[str, Any]]:
    sb = _require_supabase()
    bounded = max(1, min(int(limit), CONFIG_HISTORY_RETENTION))
    try:
        response = (
            sb.table(CONFIG_HISTORY_TABLE)
            .select("version_id,guild_id,source,mode,actor_id,reason,is_manual,created_at,snapshot")
            .eq("guild_id", str(int(guild_id)))
            .order("created_at", desc=True)
            .order("version_id", desc=True)
            .limit(bounded)
            .execute()
        )
    except Exception as exc:
        if _is_missing_table_error(exc):
            raise RuntimeError(
                "Configuration history is not installed yet. Apply the guild config version-history migration first."
            ) from exc
        raise

    return [dict(row) for row in (getattr(response, "data", None) or []) if isinstance(row, Mapping)]


def _insert_snapshot_sync(
    guild_id: int,
    snapshot: Mapping[str, Any],
    *,
    source: str,
    mode: str = "manual",
    actor_id: Optional[int] = None,
    reason: str = "",
    is_manual: bool = True,
) -> dict[str, Any]:
    sb = _require_supabase()
    payload = {
        "guild_id": str(int(guild_id)),
        "snapshot": dict(snapshot),
        "source": _safe_str(source, "manual_backup")[:300],
        "mode": _safe_str(mode, "manual")[:100],
        "actor_id": str(int(actor_id)) if _safe_int(actor_id, 0) > 0 else None,
        "reason": _safe_str(reason)[:1000] or None,
        "is_manual": bool(is_manual),
    }
    try:
        response = sb.table(CONFIG_HISTORY_TABLE).insert(payload).execute()
    except Exception as exc:
        if _is_missing_table_error(exc):
            raise RuntimeError(
                "Configuration history is not installed yet. Apply the guild config version-history migration first."
            ) from exc
        raise

    rows = getattr(response, "data", None) or []
    inserted = dict(rows[0]) if rows and isinstance(rows[0], Mapping) else payload
    _prune_versions_sync(guild_id)
    return inserted


def _prune_versions_sync(guild_id: int) -> None:
    sb = _require_supabase()
    try:
        response = (
            sb.table(CONFIG_HISTORY_TABLE)
            .select("version_id")
            .eq("guild_id", str(int(guild_id)))
            .order("created_at", desc=True)
            .order("version_id", desc=True)
            .execute()
        )
        rows = [row for row in (getattr(response, "data", None) or []) if isinstance(row, Mapping)]
        for row in rows[CONFIG_HISTORY_RETENTION:]:
            version_id = _safe_int(row.get("version_id"), 0)
            if version_id > 0:
                sb.table(CONFIG_HISTORY_TABLE).delete().eq("version_id", version_id).execute()
    except Exception:
        # Retention cleanup must never turn a successful backup into a failed write.
        return


def create_manual_backup_sync(
    guild_id: int,
    *,
    actor_id: Optional[int] = None,
    reason: str = "Manual backup",
) -> dict[str, Any]:
    _table_name, current = _fetch_current_config_row_sync(int(guild_id))
    return _insert_snapshot_sync(
        int(guild_id),
        current,
        source="manual_backup",
        mode="manual",
        actor_id=actor_id,
        reason=reason,
        is_manual=True,
    )


def _restore_audit_payload(
    raw: Any,
    *,
    actor_id: Optional[int],
    reason: str,
    version_id: int,
) -> dict[str, Any]:
    payload = dict(raw) if isinstance(raw, Mapping) else {}
    payload["config_last_write_source"] = "config_history_restore"
    payload["config_last_write_mode"] = "restore"
    payload["config_last_write_at"] = _utc_iso()
    payload["config_restored_from_version_id"] = str(int(version_id))
    if _safe_int(actor_id, 0) > 0:
        payload["config_last_write_actor_id"] = str(int(actor_id))
    if _safe_str(reason):
        payload["config_last_write_reason"] = _safe_str(reason)[:1000]
    return payload


def restore_config_version_sync(
    guild_id: int,
    version_id: int,
    *,
    actor_id: Optional[int] = None,
    reason: str = "Restore saved configuration version",
) -> dict[str, Any]:
    gid = int(guild_id)
    vid = int(version_id)
    version = _fetch_version_sync(gid, vid)
    snapshot = _row_dict(version.get("snapshot"))
    if not snapshot:
        raise RuntimeError(f"Configuration version {vid} has no usable snapshot.")
    if _safe_int(snapshot.get("guild_id"), gid) != gid:
        raise RuntimeError("Configuration version belongs to a different guild.")

    table_name, current = _fetch_current_config_row_sync(gid)

    # Preserve the state immediately before restore even if the automatic
    # trigger history is incomplete or the current row was written externally.
    pre_restore = _insert_snapshot_sync(
        gid,
        current,
        source="pre_restore_backup",
        mode="restore_guard",
        actor_id=actor_id,
        reason=f"Automatic backup before restoring version {vid}",
        is_manual=True,
    )

    allowed_columns = {str(key) for key in current.keys()}
    restore_payload = {
        str(key): value
        for key, value in snapshot.items()
        if str(key) in allowed_columns and str(key) not in _RESTORE_EXCLUDED_KEYS
    }

    if "settings" in allowed_columns:
        restore_payload["settings"] = _restore_audit_payload(
            snapshot.get("settings"),
            actor_id=actor_id,
            reason=reason,
            version_id=vid,
        )
    if "config" in allowed_columns:
        restore_payload["config"] = _restore_audit_payload(
            snapshot.get("config"),
            actor_id=actor_id,
            reason=reason,
            version_id=vid,
        )
    if "metadata" in allowed_columns:
        restore_payload["metadata"] = _restore_audit_payload(
            snapshot.get("metadata"),
            actor_id=actor_id,
            reason=reason,
            version_id=vid,
        )

    if not restore_payload:
        raise RuntimeError("Configuration version contains no restorable fields for the current schema.")

    sb = _require_supabase()
    response = (
        sb.table(table_name)
        .update(restore_payload)
        .eq("guild_id", str(gid))
        .execute()
    )
    rows = getattr(response, "data", None) or []
    restored = dict(rows[0]) if rows and isinstance(rows[0], Mapping) else _fetch_current_config_row_sync(gid)[1]
    clear_guild_config_cache(gid)

    return {
        "guild_id": str(gid),
        "restored_from_version_id": vid,
        "restored": restored,
        "pre_restore_backup": pre_restore,
    }


def _flatten_functional_config(row: Mapping[str, Any]) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    for key, value in dict(row).items():
        name = str(key)
        if name in {"settings", "config", "metadata", "meta"}:
            continue
        if name not in _COMPARISON_EXCLUDED_KEYS:
            merged[name] = value

    for container_key in ("settings", "config"):
        nested = row.get(container_key)
        if isinstance(nested, Mapping):
            for key, value in nested.items():
                name = str(key)
                if name not in _COMPARISON_EXCLUDED_KEYS:
                    merged[name] = value
    return merged


def changed_config_keys(left: Mapping[str, Any], right: Mapping[str, Any]) -> list[str]:
    before = _flatten_functional_config(left)
    after = _flatten_functional_config(right)
    keys = sorted(set(before) | set(after))
    return [key for key in keys if before.get(key) != after.get(key)]


async def list_config_versions(guild_id: int, limit: int = 10) -> list[dict[str, Any]]:
    return await asyncio.to_thread(_list_config_versions_sync, int(guild_id), int(limit))


async def get_config_version(guild_id: int, version_id: int) -> dict[str, Any]:
    return await asyncio.to_thread(_fetch_version_sync, int(guild_id), int(version_id))


async def create_manual_backup(
    guild_id: int,
    *,
    actor_id: Optional[int] = None,
    reason: str = "Manual backup",
) -> dict[str, Any]:
    return await asyncio.to_thread(
        create_manual_backup_sync,
        int(guild_id),
        actor_id=actor_id,
        reason=reason,
    )


async def restore_config_version(
    guild_id: int,
    version_id: int,
    *,
    actor_id: Optional[int] = None,
    reason: str = "Restore saved configuration version",
) -> dict[str, Any]:
    return await asyncio.to_thread(
        restore_config_version_sync,
        int(guild_id),
        int(version_id),
        actor_id=actor_id,
        reason=reason,
    )


__all__ = [
    "CONFIG_HISTORY_RETENTION",
    "CONFIG_HISTORY_TABLE",
    "changed_config_keys",
    "create_manual_backup",
    "create_manual_backup_sync",
    "get_config_version",
    "list_config_versions",
    "restore_config_version",
    "restore_config_version_sync",
]
