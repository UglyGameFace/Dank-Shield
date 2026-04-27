from __future__ import annotations

import asyncio
from typing import Any, Mapping, Optional

from ..globals import get_supabase, now_utc


# ============================================================
# public_setup_config_writer.py
# ------------------------------------------------------------
# Durable writer for public setup commands.
#
# Why this exists:
# Some guild_configs deployments have BOTH flat columns and a
# JSON settings/config payload. Older setup writes could update
# only the JSON payload while stale flat columns stayed behind.
# Then the runtime resolver saw the stale flat column and the
# saved setup confirmation looked wrong.
#
# This writer keeps JSON and known flat columns in sync. It is
# schema-tolerant: it only writes flat columns that are actually
# visible on the current row, then falls back to JSON-only writes
# for deployments that only have settings/config.
# ============================================================


_JSON_CONFIG_KEYS = {"settings", "config", "metadata", "meta"}
_BASE_WRITE_KEYS = {"guild_id", "updated_at", "created_at"}


def _utc_iso() -> str:
    try:
        return now_utc().isoformat()
    except Exception:
        return ""


def _config_table_name() -> str:
    try:
        import os

        return (os.getenv("STONEY_GUILD_CONFIG_TABLE") or "guild_configs").strip() or "guild_configs"
    except Exception:
        return "guild_configs"


def _fetch_existing_config_row_sync(guild_id: int) -> Optional[dict[str, Any]]:
    sb = get_supabase()
    if sb is None:
        return None

    response = (
        sb.table(_config_table_name())
        .select("*")
        .eq("guild_id", str(int(guild_id)))
        .limit(1)
        .execute()
    )
    rows = getattr(response, "data", None) or []
    if not rows:
        return None
    row = rows[0]
    return dict(row) if isinstance(row, Mapping) else None


def _settings_payload_update(original: Optional[Mapping[str, Any]], updates: Mapping[str, Any]) -> dict[str, Any]:
    base: dict[str, Any] = {}

    # Flat columns first, then nested JSON, then the explicit update.
    # This makes the newest setup command values authoritative while
    # still preserving older settings from either storage style.
    try:
        if isinstance(original, Mapping):
            for key, value in original.items():
                if key not in _JSON_CONFIG_KEYS and value is not None:
                    base[str(key)] = value
            for key in ("settings", "config", "metadata", "meta"):
                value = original.get(key)
                if isinstance(value, Mapping):
                    for nested_key, nested_value in value.items():
                        if nested_value is not None:
                            base[str(nested_key)] = nested_value
    except Exception:
        base = {}

    for key, value in dict(updates).items():
        if value is not None:
            base[str(key)] = value

    return base


def _known_flat_payload(existing: Optional[Mapping[str, Any]], updates: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(existing, Mapping):
        return {}
    columns = {str(k) for k in existing.keys()}
    return {
        str(k): v
        for k, v in dict(updates).items()
        if str(k) in columns and str(k) not in _JSON_CONFIG_KEYS and str(k) not in _BASE_WRITE_KEYS
    }


def _clean_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    return {str(k): v for k, v in dict(payload).items() if v is not None}


def upsert_guild_config_sync(guild_id: int, updates: Mapping[str, Any]) -> dict[str, Any]:
    sb = get_supabase()
    if sb is None:
        raise RuntimeError("Supabase is not configured/available.")

    table = _config_table_name()
    gid = int(guild_id)
    existing = _fetch_existing_config_row_sync(gid)
    settings = _settings_payload_update(existing, updates)
    flat_updates = _known_flat_payload(existing, updates)

    base_fields = {
        "guild_id": str(gid),
        "updated_at": _utc_iso(),
    }

    # Prefer keeping both storage styles synchronized. If a deployment
    # lacks one JSON column or a flat column, the later attempts safely
    # fall back without losing the authoritative settings payload.
    attempts: list[dict[str, Any]] = [
        {**base_fields, "settings": settings, **flat_updates},
        {**base_fields, "config": settings, **flat_updates},
        {**base_fields, **flat_updates},
        {**base_fields, "settings": settings},
        {**base_fields, "config": settings},
        {**base_fields, **dict(updates)},
    ]

    last_error: Optional[Exception] = None

    for payload in attempts:
        clean_payload = _clean_payload(payload)
        if not clean_payload:
            continue
        try:
            if existing:
                response = (
                    sb.table(table)
                    .update(clean_payload)
                    .eq("guild_id", str(gid))
                    .execute()
                )
            else:
                try:
                    response = sb.table(table).upsert(clean_payload, on_conflict="guild_id").execute()
                except TypeError:
                    response = sb.table(table).upsert(clean_payload).execute()

            rows = getattr(response, "data", None) or []
            if rows and isinstance(rows[0], Mapping):
                return dict(rows[0])

            refreshed = _fetch_existing_config_row_sync(gid)
            return refreshed or clean_payload
        except Exception as e:
            last_error = e
            continue

    raise RuntimeError(f"Failed writing guild config: {last_error!r}")


async def upsert_guild_config(guild_id: int, updates: Mapping[str, Any]) -> dict[str, Any]:
    return await asyncio.to_thread(upsert_guild_config_sync, int(guild_id), dict(updates))


def apply_public_setup_writer_patch() -> bool:
    """
    Attach this writer to public_setup_group without changing the top-level
    slash command surface. Existing command callbacks resolve module globals
    at runtime, so replacing _upsert_config keeps setup-tickets/setup-verify/
    setup-logs on the durable writer path.
    """
    try:
        from . import public_setup_group as group

        group._upsert_config_sync = upsert_guild_config_sync  # type: ignore[attr-defined]
        group._upsert_config = upsert_guild_config  # type: ignore[attr-defined]
        return True
    except Exception:
        return False


__all__ = [
    "upsert_guild_config_sync",
    "upsert_guild_config",
    "apply_public_setup_writer_patch",
]
