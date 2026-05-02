from __future__ import annotations

import asyncio
from datetime import datetime, timezone
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
#
# Safety contract:
# - setup_builder / explicit_override / force can replace saved setup values.
# - auto_create / auto_discover / runtime_discovery / fill_missing only fill blanks.
# - control keys are never stored in guild_configs.
# - every write records source/mode metadata when the schema supports it.
# ============================================================


_JSON_CONFIG_KEYS = {"settings", "config", "metadata", "meta"}
_BASE_WRITE_KEYS = {"guild_id", "updated_at", "created_at"}
_CONTROL_KEYS = {
    "__config_write_mode",
    "__config_write_source",
    "__config_write_reason",
    "__config_write_actor_id",
    "__config_write_allow_keys",
    "__config_write_dry_run",
}
_OVERWRITE_MODES = {"setup_builder", "explicit_override", "force"}
_FILL_ONLY_MODES = {"fill_missing", "runtime_discovery", "auto_discover", "auto_create"}
_ALLOWED_MODES = _OVERWRITE_MODES | _FILL_ONLY_MODES
_PROTECTED_SUFFIXES = ("_role_id", "_channel_id", "_category_id")
_PROTECTED_KEYS = {
    "ticket_prefix",
    "verify_kick_hours",
    "use_env_fallbacks",
    "allow_runtime_discovery",
}


def _utc_iso() -> str:
    try:
        return now_utc().isoformat()
    except Exception:
        return datetime.now(timezone.utc).isoformat()


def _config_table_name() -> str:
    try:
        import os

        return (os.getenv("STONEY_GUILD_CONFIG_TABLE") or "guild_configs").strip() or "guild_configs"
    except Exception:
        return "guild_configs"


def _safe_str(value: Any, default: str = "") -> str:
    try:
        text = str(value or "").strip()
        return text if text else default
    except Exception:
        return default


def _safe_bool(value: Any, default: bool = False) -> bool:
    try:
        if isinstance(value, bool):
            return value
        if value is None:
            return default
        text = str(value).strip().lower()
        if text in {"1", "true", "yes", "y", "on"}:
            return True
        if text in {"0", "false", "no", "n", "off"}:
            return False
    except Exception:
        pass
    return default


def _is_empty(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, bool):
        return False
    text = str(value).strip()
    return text == "" or text == "0" or text.lower() in {"none", "null"}


def _normalize_value(key: str, value: Any) -> Any:
    if value is None or key in _CONTROL_KEYS:
        return None
    if key.endswith("_id"):
        try:
            num = int(str(value).strip())
            return str(num) if num > 0 else None
        except Exception:
            return None
    if isinstance(value, str):
        text = value.strip()
        return text if text else None
    return value


def _same_value(left: Any, right: Any) -> bool:
    if _is_empty(left) and _is_empty(right):
        return True
    return str(left).strip() == str(right).strip()


def _is_protected_key(key: str) -> bool:
    return key in _PROTECTED_KEYS or key.endswith(_PROTECTED_SUFFIXES)


def _extract_mode(updates: Mapping[str, Any]) -> str:
    mode = _safe_str(updates.get("__config_write_mode"), "").lower()
    if mode in _ALLOWED_MODES:
        return mode
    # This writer is used by the owner-facing setup builder by default.
    return "setup_builder"


def _extract_source(updates: Mapping[str, Any]) -> str:
    return _safe_str(updates.get("__config_write_source"), "public_setup_config_writer")[:300]


def _allowed_key_override(updates: Mapping[str, Any]) -> set[str]:
    raw = updates.get("__config_write_allow_keys")
    try:
        if isinstance(raw, str):
            return {x.strip() for x in raw.split(",") if x.strip()}
        if isinstance(raw, (list, tuple, set)):
            return {str(x).strip() for x in raw if str(x).strip()}
    except Exception:
        pass
    return set()


def _read_row_value(row: Optional[Mapping[str, Any]], key: str) -> Any:
    if not isinstance(row, Mapping):
        return None
    if key in row and row.get(key) is not None:
        return row.get(key)
    for json_key in ("settings", "config", "metadata", "meta"):
        value = row.get(json_key)
        if isinstance(value, Mapping) and key in value:
            return value.get(key)
    return None


def _filter_safe_updates(existing: Optional[Mapping[str, Any]], updates: Mapping[str, Any]) -> tuple[dict[str, Any], list[str], list[str], str, str]:
    mode = _extract_mode(updates)
    source = _extract_source(updates)
    allow_keys = _allowed_key_override(updates)
    clean: dict[str, Any] = {}
    blocked: list[str] = []
    changed: list[str] = []

    for raw_key, raw_value in dict(updates).items():
        key = str(raw_key)
        if key in _CONTROL_KEYS:
            continue
        value = _normalize_value(key, raw_value)
        if value is None:
            continue

        if not _is_protected_key(key):
            clean[key] = value
            continue

        old_value = _read_row_value(existing, key)
        if _is_empty(old_value):
            clean[key] = value
            changed.append(f"{key}=set")
            continue

        if _same_value(old_value, value):
            clean[key] = value
            continue

        if mode in _OVERWRITE_MODES or key in allow_keys:
            clean[key] = value
            changed.append(f"{key}: {old_value} -> {value}")
            continue

        blocked.append(f"{key}: kept existing {old_value}, blocked attempted {value}")

    if clean:
        clean.setdefault("config_last_write_mode", mode)
        clean.setdefault("config_last_write_source", source)
        clean.setdefault("config_last_write_at", _utc_iso())
        if blocked:
            clean.setdefault("config_last_blocked_overwrite", " | ".join(blocked)[:1000])

    try:
        if blocked:
            print(f"⚠️ public_setup_config_writer blocked unsafe setup overwrite mode={mode} source={source} blocked={blocked[:8]}")
        if changed:
            print(f"✅ public_setup_config_writer allowed setup config changes mode={mode} source={source} changes={changed[:8]}")
    except Exception:
        pass

    return clean, blocked, changed, mode, source


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

    # Flat columns first, then nested JSON, then the explicit safe update.
    # This makes owner-confirmed setup values authoritative while preserving
    # older settings from either storage style.
    try:
        if isinstance(original, Mapping):
            for key, value in original.items():
                if key not in _JSON_CONFIG_KEYS and key not in _CONTROL_KEYS and value is not None:
                    base[str(key)] = value
            for key in ("settings", "config", "metadata", "meta"):
                value = original.get(key)
                if isinstance(value, Mapping):
                    for nested_key, nested_value in value.items():
                        if nested_key not in _CONTROL_KEYS and nested_value is not None:
                            base[str(nested_key)] = nested_value
    except Exception:
        base = {}

    for key, value in dict(updates).items():
        if key not in _CONTROL_KEYS and value is not None:
            base[str(key)] = value

    return base


def _known_flat_payload(existing: Optional[Mapping[str, Any]], updates: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(existing, Mapping):
        return {}
    columns = {str(k) for k in existing.keys()}
    return {
        str(k): v
        for k, v in dict(updates).items()
        if str(k) in columns and str(k) not in _JSON_CONFIG_KEYS and str(k) not in _BASE_WRITE_KEYS and str(k) not in _CONTROL_KEYS
    }


def _clean_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    return {str(k): v for k, v in dict(payload).items() if v is not None and str(k) not in _CONTROL_KEYS}


def upsert_guild_config_sync(guild_id: int, updates: Mapping[str, Any]) -> dict[str, Any]:
    sb = get_supabase()
    if sb is None:
        raise RuntimeError("Supabase is not configured/available.")

    table = _config_table_name()
    gid = int(guild_id)
    existing = _fetch_existing_config_row_sync(gid)
    safe_updates, blocked, changed, mode, source = _filter_safe_updates(existing, updates)

    if _safe_bool(updates.get("__config_write_dry_run"), False):
        preview = _settings_payload_update(existing, safe_updates)
        preview["_dry_run"] = True
        preview["_blocked_overwrites"] = blocked
        preview["_allowed_changes"] = changed
        preview["_config_write_mode"] = mode
        preview["_config_write_source"] = source
        return preview

    if not safe_updates:
        refreshed = _fetch_existing_config_row_sync(gid)
        return refreshed or {"guild_id": str(gid), "blocked_overwrites": blocked}

    settings = _settings_payload_update(existing, safe_updates)
    flat_updates = _known_flat_payload(existing, safe_updates)

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
        {**base_fields, **safe_updates},
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
