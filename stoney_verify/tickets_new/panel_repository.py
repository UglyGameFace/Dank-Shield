from __future__ import annotations

import asyncio
import random
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ..globals import get_supabase, now_utc, reset_supabase

# ============================================================
# tickets_new/panel_repository.py
# ------------------------------------------------------------
# Durable repository for multi-panel ticket configuration.
#
# Goals:
# - panel config must live in the DB, not only in memory
# - reads/writes should be retry-safe
# - supports per-panel rules, category bindings, and presets
# - exposes process-local locks/semaphores to prevent command storms
# - keeps guilds isolated under high concurrent load
# ============================================================

TICKET_PANELS_TABLE = "ticket_panels"
TICKET_PANEL_CATEGORIES_TABLE = "ticket_panel_categories"
TICKET_PANEL_RULES_TABLE = "ticket_panel_rules"
TICKET_PANEL_PRESETS_TABLE = "ticket_panel_presets"

VALID_PANEL_STYLES = {
    "buttons",
    "select",
    "hybrid",
    "modal",
}

VALID_TRANSCRIPT_MODES = {
    "always",
    "on_close",
    "manual",
    "disabled",
}

DEFAULT_PANEL_STYLE = "buttons"
DEFAULT_TRANSCRIPT_MODE = "on_close"

DEFAULT_PANEL_RULES: Dict[str, Any] = {
    "cooldown_seconds": 0,
    "max_tickets_per_window": 0,
    "window_minutes": 0,
    "auto_close_enabled": False,
    "auto_close_minutes": 1440,
    "inactivity_reminders_enabled": True,
    "inactivity_reminder_minutes": 240,
    "staff_alert_channel_id": None,
    "allow_unverified": True,
    "allow_verified": True,
    "allow_resident": True,
    "allow_staff": True,
    "ghost_allowed": False,
    "transcript_mode": DEFAULT_TRANSCRIPT_MODE,
    "close_confirmation_required": True,
    "per_owner_open_limit": 1,
}

_DB_MAX_ATTEMPTS = 5
_GUILD_PANEL_SEMAPHORES: Dict[str, asyncio.Semaphore] = {}
_PANEL_CREATION_LOCKS: Dict[str, asyncio.Lock] = {}
_PANEL_MUTATION_LOCKS: Dict[str, asyncio.Lock] = {}


# ============================================================
# Small helpers
# ============================================================

def _repo_debug(msg: str) -> None:
    try:
        print(f"🧩 panel_repository {msg}")
    except Exception:
        pass


def _now_iso() -> str:
    try:
        return now_utc().isoformat()
    except Exception:
        return datetime.now(timezone.utc).isoformat()


def _sb():
    try:
        return get_supabase()
    except Exception:
        return None


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or isinstance(value, bool):
            return default
        return int(str(value).strip())
    except Exception:
        return default


def _safe_bool(value: Any, default: bool = False) -> bool:
    try:
        if isinstance(value, bool):
            return value
        if value is None:
            return default
        raw = str(value).strip().lower()
        if raw in {"1", "true", "yes", "y", "on"}:
            return True
        if raw in {"0", "false", "no", "n", "off"}:
            return False
        return default
    except Exception:
        return default


def _safe_str(value: Any, default: str = "") -> str:
    try:
        text = str(value or "").strip()
        return text if text else default
    except Exception:
        return default


def _safe_json_dict(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    return {}


def _safe_list(value: Any) -> List[Any]:
    if isinstance(value, list):
        return list(value)
    return []


def _clean_text(value: Any, limit: int = 500) -> Optional[str]:
    try:
        if value is None:
            return None
        text = str(value).strip()
        if not text:
            return None
        return text[:limit]
    except Exception:
        return None


def _slugify(value: Any, limit: int = 80) -> str:
    raw = _safe_str(value).lower().replace("&", " and ")
    out: List[str] = []
    prev_dash = False
    for ch in raw:
        if ch.isalnum():
            out.append(ch)
            prev_dash = False
        elif ch in {" ", "-", "_", "/"}:
            if not prev_dash:
                out.append("-")
                prev_dash = True
    text = "".join(out).strip("-")
    if not text:
        return ""
    return text[:limit]


def _normalize_panel_style(value: Any) -> str:
    raw = _safe_str(value, DEFAULT_PANEL_STYLE).lower()
    if raw in VALID_PANEL_STYLES:
        return raw
    return DEFAULT_PANEL_STYLE


def _normalize_transcript_mode(value: Any) -> str:
    raw = _safe_str(value, DEFAULT_TRANSCRIPT_MODE).lower()
    if raw in VALID_TRANSCRIPT_MODES:
        return raw
    return DEFAULT_TRANSCRIPT_MODE


def _normalize_channel_id(value: Any) -> Optional[str]:
    text = _safe_str(value)
    if not text:
        return None
    digits = "".join(ch for ch in text if ch.isdigit())
    return digits or None


def _normalize_bool_flag(value: Any, default: bool) -> bool:
    return _safe_bool(value, default)


def _normalize_panel_key(value: Any) -> str:
    return _slugify(value, limit=80)


def _normalize_preset_key(value: Any) -> str:
    return _slugify(value, limit=80)


def _result_rows(resp: Any) -> List[Dict[str, Any]]:
    try:
        rows = getattr(resp, "data", None) or []
        return [r for r in rows if isinstance(r, dict)]
    except Exception:
        return []


# ============================================================
# Retry helpers
# ============================================================

def _is_retryable_db_error(error: Exception) -> bool:
    text = repr(error).lower()
    markers = (
        "remoteprotocolerror",
        "server disconnected",
        "connection reset",
        "connection aborted",
        "temporarily unavailable",
        "timeout",
        "timed out",
        "eof",
        "network",
        "closed connection",
        "connection refused",
        "connection terminated",
        "httpcore",
        "httpx",
        "broken pipe",
        "connection pool",
        "stream closed",
        "try again",
    )
    return any(marker in text for marker in markers)


def _sleep_backoff(attempt: int) -> None:
    base = min(0.35 * (2 ** max(0, attempt - 1)), 3.0)
    jitter = random.uniform(0.05, 0.25)
    time.sleep(base + jitter)


def _execute_db_op(op_name: str, executor, max_attempts: int = _DB_MAX_ATTEMPTS):
    last_error = None
    for attempt in range(1, max_attempts + 1):
        try:
            return executor()
        except Exception as e:
            last_error = e
            if _is_retryable_db_error(e) and attempt < max_attempts:
                try:
                    reset_supabase()
                except Exception:
                    pass
                print(
                    f"⚠️ {op_name}: transient DB error on attempt "
                    f"{attempt}/{max_attempts}: {repr(e)}"
                )
                _sleep_backoff(attempt)
                continue
            raise
    raise last_error


async def _run_db_op(op_name: str, executor, max_attempts: int = _DB_MAX_ATTEMPTS):
    return await asyncio.to_thread(_execute_db_op, op_name, executor, max_attempts)


# ============================================================
# In-process concurrency guards
# ============================================================

def panel_creation_lock_key(*, guild_id: Any, owner_id: Any, panel_key: Any) -> str:
    return f"{_safe_str(guild_id)}:{_safe_str(owner_id)}:{_normalize_panel_key(panel_key)}"


def panel_mutation_lock_key(*, guild_id: Any, panel_key: Any) -> str:
    return f"{_safe_str(guild_id)}:{_normalize_panel_key(panel_key)}"


def get_panel_creation_lock(*, guild_id: Any, owner_id: Any, panel_key: Any) -> asyncio.Lock:
    key = panel_creation_lock_key(guild_id=guild_id, owner_id=owner_id, panel_key=panel_key)
    lock = _PANEL_CREATION_LOCKS.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _PANEL_CREATION_LOCKS[key] = lock
    return lock


def get_panel_mutation_lock(*, guild_id: Any, panel_key: Any) -> asyncio.Lock:
    key = panel_mutation_lock_key(guild_id=guild_id, panel_key=panel_key)
    lock = _PANEL_MUTATION_LOCKS.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _PANEL_MUTATION_LOCKS[key] = lock
    return lock


def get_guild_panel_semaphore(guild_id: Any, limit: int = 8) -> asyncio.Semaphore:
    key = _safe_str(guild_id)
    sem = _GUILD_PANEL_SEMAPHORES.get(key)
    if sem is None:
        sem = asyncio.Semaphore(max(1, int(limit)))
        _GUILD_PANEL_SEMAPHORES[key] = sem
    return sem


# ============================================================
# Normalizers
# ============================================================

def _normalize_panel_row(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": row.get("id"),
        "guild_id": _safe_str(row.get("guild_id")),
        "panel_key": _normalize_panel_key(row.get("panel_key") or row.get("slug") or row.get("panel_name")),
        "panel_name": _safe_str(row.get("panel_name") or row.get("name")),
        "panel_channel_id": _normalize_channel_id(row.get("panel_channel_id")),
        "panel_message_id": _normalize_channel_id(row.get("panel_message_id")),
        "panel_style": _normalize_panel_style(row.get("panel_style")),
        "prompt_title": _safe_str(row.get("prompt_title")),
        "prompt_description": _safe_str(row.get("prompt_description")),
        "embed_title": _safe_str(row.get("embed_title")),
        "embed_description": _safe_str(row.get("embed_description")),
        "button_label": _safe_str(row.get("button_label")),
        "menu_placeholder": _safe_str(row.get("menu_placeholder")),
        "preset_key": _normalize_preset_key(row.get("preset_key")),
        "is_enabled": _safe_bool(row.get("is_enabled"), True),
        "sort_order": _safe_int(row.get("sort_order"), 0),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
        "raw": dict(row),
    }


def _normalize_panel_rule_row(row: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    src = dict(row or {})
    return {
        "id": src.get("id"),
        "guild_id": _safe_str(src.get("guild_id")),
        "panel_key": _normalize_panel_key(src.get("panel_key")),
        "cooldown_seconds": max(0, _safe_int(src.get("cooldown_seconds"), DEFAULT_PANEL_RULES["cooldown_seconds"])),
        "max_tickets_per_window": max(0, _safe_int(src.get("max_tickets_per_window"), DEFAULT_PANEL_RULES["max_tickets_per_window"])),
        "window_minutes": max(0, _safe_int(src.get("window_minutes"), DEFAULT_PANEL_RULES["window_minutes"])),
        "auto_close_enabled": _normalize_bool_flag(src.get("auto_close_enabled"), DEFAULT_PANEL_RULES["auto_close_enabled"]),
        "auto_close_minutes": max(5, _safe_int(src.get("auto_close_minutes"), DEFAULT_PANEL_RULES["auto_close_minutes"])),
        "inactivity_reminders_enabled": _normalize_bool_flag(src.get("inactivity_reminders_enabled"), DEFAULT_PANEL_RULES["inactivity_reminders_enabled"]),
        "inactivity_reminder_minutes": max(1, _safe_int(src.get("inactivity_reminder_minutes"), DEFAULT_PANEL_RULES["inactivity_reminder_minutes"])),
        "staff_alert_channel_id": _normalize_channel_id(src.get("staff_alert_channel_id")),
        "allow_unverified": _normalize_bool_flag(src.get("allow_unverified"), DEFAULT_PANEL_RULES["allow_unverified"]),
        "allow_verified": _normalize_bool_flag(src.get("allow_verified"), DEFAULT_PANEL_RULES["allow_verified"]),
        "allow_resident": _normalize_bool_flag(src.get("allow_resident"), DEFAULT_PANEL_RULES["allow_resident"]),
        "allow_staff": _normalize_bool_flag(src.get("allow_staff"), DEFAULT_PANEL_RULES["allow_staff"]),
        "ghost_allowed": _normalize_bool_flag(src.get("ghost_allowed"), DEFAULT_PANEL_RULES["ghost_allowed"]),
        "transcript_mode": _normalize_transcript_mode(src.get("transcript_mode")),
        "close_confirmation_required": _normalize_bool_flag(src.get("close_confirmation_required"), DEFAULT_PANEL_RULES["close_confirmation_required"]),
        "per_owner_open_limit": max(1, _safe_int(src.get("per_owner_open_limit"), DEFAULT_PANEL_RULES["per_owner_open_limit"])),
        "created_at": src.get("created_at"),
        "updated_at": src.get("updated_at"),
        "raw": dict(src),
    }


def _normalize_panel_category_row(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": row.get("id"),
        "guild_id": _safe_str(row.get("guild_id")),
        "panel_key": _normalize_panel_key(row.get("panel_key")),
        "category_slug": _slugify(row.get("category_slug"), limit=120),
        "sort_order": _safe_int(row.get("sort_order"), 0),
        "is_enabled": _safe_bool(row.get("is_enabled"), True),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
        "raw": dict(row),
    }


def _normalize_panel_preset_row(row: Dict[str, Any]) -> Dict[str, Any]:
    defaults = _safe_json_dict(row.get("default_rules_json"))
    return {
        "id": row.get("id"),
        "guild_id": _safe_str(row.get("guild_id")),
        "preset_key": _normalize_preset_key(row.get("preset_key") or row.get("slug") or row.get("name")),
        "preset_name": _safe_str(row.get("preset_name") or row.get("name")),
        "panel_style": _normalize_panel_style(row.get("panel_style")),
        "default_prompt_title": _safe_str(row.get("default_prompt_title")),
        "default_prompt_description": _safe_str(row.get("default_prompt_description")),
        "default_embed_title": _safe_str(row.get("default_embed_title")),
        "default_embed_description": _safe_str(row.get("default_embed_description")),
        "default_button_label": _safe_str(row.get("default_button_label")),
        "default_menu_placeholder": _safe_str(row.get("default_menu_placeholder")),
        "default_rules_json": defaults,
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
        "raw": dict(row),
    }


def _normalize_panel_write_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "guild_id": _safe_str(payload.get("guild_id")),
        "panel_key": _normalize_panel_key(payload.get("panel_key")),
        "panel_name": _safe_str(payload.get("panel_name")),
        "panel_channel_id": _normalize_channel_id(payload.get("panel_channel_id")),
        "panel_message_id": _normalize_channel_id(payload.get("panel_message_id")),
        "panel_style": _normalize_panel_style(payload.get("panel_style")),
        "prompt_title": _clean_text(payload.get("prompt_title"), 250),
        "prompt_description": _clean_text(payload.get("prompt_description"), 3000),
        "embed_title": _clean_text(payload.get("embed_title"), 250),
        "embed_description": _clean_text(payload.get("embed_description"), 3000),
        "button_label": _clean_text(payload.get("button_label"), 80),
        "menu_placeholder": _clean_text(payload.get("menu_placeholder"), 120),
        "preset_key": _normalize_preset_key(payload.get("preset_key")),
        "is_enabled": _safe_bool(payload.get("is_enabled"), True),
        "sort_order": _safe_int(payload.get("sort_order"), 0),
        "updated_at": _now_iso(),
    }


def _normalize_panel_rule_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    rules = _normalize_panel_rule_row(payload)
    clean = {k: v for k, v in rules.items() if k not in {"id", "created_at", "raw"}}
    clean["updated_at"] = _now_iso()
    return clean


def _normalize_panel_category_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "guild_id": _safe_str(payload.get("guild_id")),
        "panel_key": _normalize_panel_key(payload.get("panel_key")),
        "category_slug": _slugify(payload.get("category_slug"), limit=120),
        "sort_order": _safe_int(payload.get("sort_order"), 0),
        "is_enabled": _safe_bool(payload.get("is_enabled"), True),
        "updated_at": _now_iso(),
    }


def _normalize_panel_preset_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "guild_id": _safe_str(payload.get("guild_id")),
        "preset_key": _normalize_preset_key(payload.get("preset_key")),
        "preset_name": _safe_str(payload.get("preset_name")),
        "panel_style": _normalize_panel_style(payload.get("panel_style")),
        "default_prompt_title": _clean_text(payload.get("default_prompt_title"), 250),
        "default_prompt_description": _clean_text(payload.get("default_prompt_description"), 3000),
        "default_embed_title": _clean_text(payload.get("default_embed_title"), 250),
        "default_embed_description": _clean_text(payload.get("default_embed_description"), 3000),
        "default_button_label": _clean_text(payload.get("default_button_label"), 80),
        "default_menu_placeholder": _clean_text(payload.get("default_menu_placeholder"), 120),
        "default_rules_json": _safe_json_dict(payload.get("default_rules_json")),
        "updated_at": _now_iso(),
    }


# ============================================================
# Sync DB operations
# ============================================================

def _list_panels_sync(guild_id: Any) -> List[Dict[str, Any]]:
    sb = _sb()
    if sb is None:
        return []

    def _read():
        return (
            sb.table(TICKET_PANELS_TABLE)
            .select("*")
            .eq("guild_id", _safe_str(guild_id))
            .order("sort_order", desc=False)
            .order("panel_name", desc=False)
            .execute()
        )

    rows = _result_rows(_execute_db_op(f"list panels ({guild_id})", _read))
    return [_normalize_panel_row(row) for row in rows]


def _get_panel_sync(guild_id: Any, panel_key: Any) -> Optional[Dict[str, Any]]:
    sb = _sb()
    if sb is None:
        return None

    key = _normalize_panel_key(panel_key)
    if not key:
        return None

    def _read():
        return (
            sb.table(TICKET_PANELS_TABLE)
            .select("*")
            .eq("guild_id", _safe_str(guild_id))
            .eq("panel_key", key)
            .limit(1)
            .execute()
        )

    rows = _result_rows(_execute_db_op(f"get panel ({guild_id}/{key})", _read))
    if not rows:
        return None
    return _normalize_panel_row(rows[0])


def _get_panel_by_message_sync(guild_id: Any, channel_id: Any, message_id: Any) -> Optional[Dict[str, Any]]:
    sb = _sb()
    if sb is None:
        return None

    ch = _normalize_channel_id(channel_id)
    msg = _normalize_channel_id(message_id)
    if not ch or not msg:
        return None

    def _read():
        return (
            sb.table(TICKET_PANELS_TABLE)
            .select("*")
            .eq("guild_id", _safe_str(guild_id))
            .eq("panel_channel_id", ch)
            .eq("panel_message_id", msg)
            .limit(1)
            .execute()
        )

    rows = _result_rows(_execute_db_op(f"get panel by message ({guild_id}/{ch}/{msg})", _read))
    if not rows:
        return None
    return _normalize_panel_row(rows[0])


def _upsert_panel_sync(payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    sb = _sb()
    if sb is None:
        return None

    clean = _normalize_panel_write_payload(payload)
    if not clean["guild_id"] or not clean["panel_key"] or not clean["panel_name"]:
        return None

    def _write():
        return (
            sb.table(TICKET_PANELS_TABLE)
            .upsert(clean, on_conflict="guild_id,panel_key")
            .execute()
        )

    _execute_db_op(f"upsert panel ({clean['guild_id']}/{clean['panel_key']})", _write)
    return _get_panel_sync(clean["guild_id"], clean["panel_key"])


def _delete_panel_sync(guild_id: Any, panel_key: Any) -> bool:
    sb = _sb()
    if sb is None:
        return False

    gid = _safe_str(guild_id)
    key = _normalize_panel_key(panel_key)
    if not gid or not key:
        return False

    def _delete_panel():
        sb.table(TICKET_PANELS_TABLE).delete().eq("guild_id", gid).eq("panel_key", key).execute()

    def _delete_categories():
        sb.table(TICKET_PANEL_CATEGORIES_TABLE).delete().eq("guild_id", gid).eq("panel_key", key).execute()

    def _delete_rules():
        sb.table(TICKET_PANEL_RULES_TABLE).delete().eq("guild_id", gid).eq("panel_key", key).execute()

    try:
        _execute_db_op(f"delete panel categories ({gid}/{key})", _delete_categories)
        _execute_db_op(f"delete panel rules ({gid}/{key})", _delete_rules)
        _execute_db_op(f"delete panel ({gid}/{key})", _delete_panel)
        return True
    except Exception as e:
        print("⚠️ Failed deleting panel:", repr(e))
        return False


def _list_panel_categories_sync(guild_id: Any, panel_key: Any) -> List[Dict[str, Any]]:
    sb = _sb()
    if sb is None:
        return []

    gid = _safe_str(guild_id)
    key = _normalize_panel_key(panel_key)
    if not gid or not key:
        return []

    def _read():
        return (
            sb.table(TICKET_PANEL_CATEGORIES_TABLE)
            .select("*")
            .eq("guild_id", gid)
            .eq("panel_key", key)
            .order("sort_order", desc=False)
            .order("category_slug", desc=False)
            .execute()
        )

    rows = _result_rows(_execute_db_op(f"list panel categories ({gid}/{key})", _read))
    return [_normalize_panel_category_row(row) for row in rows]


def _replace_panel_categories_sync(guild_id: Any, panel_key: Any, category_slugs: Sequence[Any]) -> List[Dict[str, Any]]:
    sb = _sb()
    if sb is None:
        return []

    gid = _safe_str(guild_id)
    key = _normalize_panel_key(panel_key)
    if not gid or not key:
        return []

    normalized: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for index, slug in enumerate(category_slugs or []):
        cleaned = _slugify(slug, limit=120)
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        normalized.append(
            _normalize_panel_category_payload(
                {
                    "guild_id": gid,
                    "panel_key": key,
                    "category_slug": cleaned,
                    "sort_order": index + 1,
                    "is_enabled": True,
                }
            )
        )

    def _delete_old():
        sb.table(TICKET_PANEL_CATEGORIES_TABLE).delete().eq("guild_id", gid).eq("panel_key", key).execute()

    def _insert_new():
        if normalized:
            sb.table(TICKET_PANEL_CATEGORIES_TABLE).insert(normalized).execute()

    try:
        _execute_db_op(f"replace panel categories delete ({gid}/{key})", _delete_old)
        _execute_db_op(f"replace panel categories insert ({gid}/{key})", _insert_new)
    except Exception as e:
        print("⚠️ Failed replacing panel categories:", repr(e))
        return []

    return _list_panel_categories_sync(gid, key)


def _get_panel_rules_sync(guild_id: Any, panel_key: Any) -> Dict[str, Any]:
    sb = _sb()
    if sb is None:
        return _normalize_panel_rule_row({"guild_id": guild_id, "panel_key": panel_key})

    gid = _safe_str(guild_id)
    key = _normalize_panel_key(panel_key)
    if not gid or not key:
        return _normalize_panel_rule_row({"guild_id": guild_id, "panel_key": panel_key})

    def _read():
        return (
            sb.table(TICKET_PANEL_RULES_TABLE)
            .select("*")
            .eq("guild_id", gid)
            .eq("panel_key", key)
            .limit(1)
            .execute()
        )

    rows = _result_rows(_execute_db_op(f"get panel rules ({gid}/{key})", _read))
    if not rows:
        return _normalize_panel_rule_row({"guild_id": gid, "panel_key": key})
    return _normalize_panel_rule_row(rows[0])


def _upsert_panel_rules_sync(payload: Dict[str, Any]) -> Dict[str, Any]:
    sb = _sb()
    if sb is None:
        return _normalize_panel_rule_row(payload)

    clean = _normalize_panel_rule_payload(payload)
    if not clean["guild_id"] or not clean["panel_key"]:
        return _normalize_panel_rule_row(payload)

    def _write():
        return (
            sb.table(TICKET_PANEL_RULES_TABLE)
            .upsert(clean, on_conflict="guild_id,panel_key")
            .execute()
        )

    _execute_db_op(f"upsert panel rules ({clean['guild_id']}/{clean['panel_key']})", _write)
    return _get_panel_rules_sync(clean["guild_id"], clean["panel_key"])


def _list_panel_presets_sync(guild_id: Any) -> List[Dict[str, Any]]:
    sb = _sb()
    if sb is None:
        return []

    gid = _safe_str(guild_id)
    if not gid:
        return []

    def _read():
        return (
            sb.table(TICKET_PANEL_PRESETS_TABLE)
            .select("*")
            .eq("guild_id", gid)
            .order("preset_name", desc=False)
            .execute()
        )

    rows = _result_rows(_execute_db_op(f"list panel presets ({gid})", _read))
    return [_normalize_panel_preset_row(row) for row in rows]


def _get_panel_preset_sync(guild_id: Any, preset_key: Any) -> Optional[Dict[str, Any]]:
    sb = _sb()
    if sb is None:
        return None

    gid = _safe_str(guild_id)
    key = _normalize_preset_key(preset_key)
    if not gid or not key:
        return None

    def _read():
        return (
            sb.table(TICKET_PANEL_PRESETS_TABLE)
            .select("*")
            .eq("guild_id", gid)
            .eq("preset_key", key)
            .limit(1)
            .execute()
        )

    rows = _result_rows(_execute_db_op(f"get panel preset ({gid}/{key})", _read))
    if not rows:
        return None
    return _normalize_panel_preset_row(rows[0])


def _upsert_panel_preset_sync(payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    sb = _sb()
    if sb is None:
        return None

    clean = _normalize_panel_preset_payload(payload)
    if not clean["guild_id"] or not clean["preset_key"] or not clean["preset_name"]:
        return None

    def _write():
        return (
            sb.table(TICKET_PANEL_PRESETS_TABLE)
            .upsert(clean, on_conflict="guild_id,preset_key")
            .execute()
        )

    _execute_db_op(f"upsert panel preset ({clean['guild_id']}/{clean['preset_key']})", _write)
    return _get_panel_preset_sync(clean["guild_id"], clean["preset_key"])


def _delete_panel_preset_sync(guild_id: Any, preset_key: Any) -> bool:
    sb = _sb()
    if sb is None:
        return False

    gid = _safe_str(guild_id)
    key = _normalize_preset_key(preset_key)
    if not gid or not key:
        return False

    def _delete():
        sb.table(TICKET_PANEL_PRESETS_TABLE).delete().eq("guild_id", gid).eq("preset_key", key).execute()

    try:
        _execute_db_op(f"delete panel preset ({gid}/{key})", _delete)
        return True
    except Exception as e:
        print("⚠️ Failed deleting panel preset:", repr(e))
        return False


# ============================================================
# Async API
# ============================================================

async def list_ticket_panels(guild_id: Any) -> List[Dict[str, Any]]:
    return await _run_db_op(f"list ticket panels async ({guild_id})", lambda: _list_panels_sync(guild_id))


async def get_ticket_panel(guild_id: Any, panel_key: Any) -> Optional[Dict[str, Any]]:
    return await _run_db_op(f"get ticket panel async ({guild_id}/{panel_key})", lambda: _get_panel_sync(guild_id, panel_key))


async def get_ticket_panel_by_message(
    guild_id: Any,
    channel_id: Any,
    message_id: Any,
) -> Optional[Dict[str, Any]]:
    return await _run_db_op(
        f"get ticket panel by message async ({guild_id}/{channel_id}/{message_id})",
        lambda: _get_panel_by_message_sync(guild_id, channel_id, message_id),
    )


async def upsert_ticket_panel(payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    gid = _safe_str(payload.get("guild_id"))
    key = _normalize_panel_key(payload.get("panel_key"))
    lock = get_panel_mutation_lock(guild_id=gid, panel_key=key or "unknown")
    async with lock:
        return await _run_db_op(
            f"upsert ticket panel async ({gid}/{key})",
            lambda: _upsert_panel_sync(payload),
        )


async def delete_ticket_panel(guild_id: Any, panel_key: Any) -> bool:
    lock = get_panel_mutation_lock(guild_id=guild_id, panel_key=panel_key)
    async with lock:
        return await _run_db_op(
            f"delete ticket panel async ({guild_id}/{panel_key})",
            lambda: _delete_panel_sync(guild_id, panel_key),
        )


async def list_ticket_panel_categories(guild_id: Any, panel_key: Any) -> List[Dict[str, Any]]:
    return await _run_db_op(
        f"list ticket panel categories async ({guild_id}/{panel_key})",
        lambda: _list_panel_categories_sync(guild_id, panel_key),
    )


async def replace_ticket_panel_categories(
    guild_id: Any,
    panel_key: Any,
    category_slugs: Sequence[Any],
) -> List[Dict[str, Any]]:
    lock = get_panel_mutation_lock(guild_id=guild_id, panel_key=panel_key)
    async with lock:
        return await _run_db_op(
            f"replace ticket panel categories async ({guild_id}/{panel_key})",
            lambda: _replace_panel_categories_sync(guild_id, panel_key, category_slugs),
        )


async def get_ticket_panel_rules(guild_id: Any, panel_key: Any) -> Dict[str, Any]:
    return await _run_db_op(
        f"get ticket panel rules async ({guild_id}/{panel_key})",
        lambda: _get_panel_rules_sync(guild_id, panel_key),
    )


async def upsert_ticket_panel_rules(payload: Dict[str, Any]) -> Dict[str, Any]:
    gid = _safe_str(payload.get("guild_id"))
    key = _normalize_panel_key(payload.get("panel_key"))
    lock = get_panel_mutation_lock(guild_id=gid, panel_key=key or "unknown")
    async with lock:
        return await _run_db_op(
            f"upsert ticket panel rules async ({gid}/{key})",
            lambda: _upsert_panel_rules_sync(payload),
        )


async def list_ticket_panel_presets(guild_id: Any) -> List[Dict[str, Any]]:
    return await _run_db_op(
        f"list ticket panel presets async ({guild_id})",
        lambda: _list_panel_presets_sync(guild_id),
    )


async def get_ticket_panel_preset(guild_id: Any, preset_key: Any) -> Optional[Dict[str, Any]]:
    return await _run_db_op(
        f"get ticket panel preset async ({guild_id}/{preset_key})",
        lambda: _get_panel_preset_sync(guild_id, preset_key),
    )


async def upsert_ticket_panel_preset(payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    gid = _safe_str(payload.get("guild_id"))
    key = _normalize_preset_key(payload.get("preset_key"))
    return await _run_db_op(
        f"upsert ticket panel preset async ({gid}/{key})",
        lambda: _upsert_panel_preset_sync(payload),
    )


async def delete_ticket_panel_preset(guild_id: Any, preset_key: Any) -> bool:
    return await _run_db_op(
        f"delete ticket panel preset async ({guild_id}/{preset_key})",
        lambda: _delete_panel_preset_sync(guild_id, preset_key),
    )


# ============================================================
# Composed helpers
# ============================================================

async def get_ticket_panel_bundle(guild_id: Any, panel_key: Any) -> Optional[Dict[str, Any]]:
    panel = await get_ticket_panel(guild_id, panel_key)
    if not panel:
        return None

    categories_task = asyncio.create_task(list_ticket_panel_categories(guild_id, panel_key))
    rules_task = asyncio.create_task(get_ticket_panel_rules(guild_id, panel_key))

    categories, rules = await asyncio.gather(categories_task, rules_task)

    preset = None
    preset_key = _normalize_preset_key(panel.get("preset_key"))
    if preset_key:
        try:
            preset = await get_ticket_panel_preset(guild_id, preset_key)
        except Exception:
            preset = None

    return {
        "panel": panel,
        "categories": categories,
        "rules": rules,
        "preset": preset,
    }


async def apply_panel_preset_to_panel(
    *,
    guild_id: Any,
    panel_key: Any,
    preset_key: Any,
    overwrite_rules: bool = True,
) -> Optional[Dict[str, Any]]:
    preset = await get_ticket_panel_preset(guild_id, preset_key)
    panel = await get_ticket_panel(guild_id, panel_key)

    if not preset or not panel:
        return None

    patch = {
        "guild_id": _safe_str(guild_id),
        "panel_key": _normalize_panel_key(panel_key),
        "preset_key": preset["preset_key"],
    }

    if preset.get("panel_style"):
        patch["panel_style"] = preset.get("panel_style")
    if preset.get("default_prompt_title"):
        patch["prompt_title"] = preset.get("default_prompt_title")
    if preset.get("default_prompt_description"):
        patch["prompt_description"] = preset.get("default_prompt_description")
    if preset.get("default_embed_title"):
        patch["embed_title"] = preset.get("default_embed_title")
    if preset.get("default_embed_description"):
        patch["embed_description"] = preset.get("default_embed_description")
    if preset.get("default_button_label"):
        patch["button_label"] = preset.get("default_button_label")
    if preset.get("default_menu_placeholder"):
        patch["menu_placeholder"] = preset.get("default_menu_placeholder")

    updated_panel = await upsert_ticket_panel({**panel, **patch})

    if overwrite_rules:
        default_rules = dict(DEFAULT_PANEL_RULES)
        default_rules.update(_safe_json_dict(preset.get("default_rules_json")))
        default_rules["guild_id"] = _safe_str(guild_id)
        default_rules["panel_key"] = _normalize_panel_key(panel_key)
        await upsert_ticket_panel_rules(default_rules)

    if updated_panel is None:
        return None
    return await get_ticket_panel_bundle(guild_id, panel_key)


async def ensure_ticket_panel_exists(
    *,
    guild_id: Any,
    panel_key: Any,
    panel_name: Any,
    panel_style: Any = DEFAULT_PANEL_STYLE,
    prompt_title: Any = None,
    prompt_description: Any = None,
    button_label: Any = None,
    menu_placeholder: Any = None,
    preset_key: Any = None,
    sort_order: int = 0,
) -> Dict[str, Any]:
    existing = await get_ticket_panel(guild_id, panel_key)
    if existing:
        return existing

    payload = {
        "guild_id": _safe_str(guild_id),
        "panel_key": _normalize_panel_key(panel_key),
        "panel_name": _safe_str(panel_name),
        "panel_style": _normalize_panel_style(panel_style),
        "prompt_title": prompt_title,
        "prompt_description": prompt_description,
        "button_label": button_label,
        "menu_placeholder": menu_placeholder,
        "preset_key": _normalize_preset_key(preset_key),
        "is_enabled": True,
        "sort_order": sort_order,
    }

    created = await upsert_ticket_panel(payload)
    if created is None:
        return _normalize_panel_row(payload)

    rules = await get_ticket_panel_rules(guild_id, panel_key)
    if not rules.get("guild_id") or not rules.get("panel_key"):
        await upsert_ticket_panel_rules(
            {
                "guild_id": _safe_str(guild_id),
                "panel_key": _normalize_panel_key(panel_key),
                **DEFAULT_PANEL_RULES,
            }
        )

    if payload["preset_key"]:
        try:
            await apply_panel_preset_to_panel(
                guild_id=guild_id,
                panel_key=panel_key,
                preset_key=payload["preset_key"],
                overwrite_rules=True,
            )
            created = await get_ticket_panel(guild_id, panel_key) or created
        except Exception as e:
            _repo_debug(
                f"preset apply failed guild={guild_id} panel={panel_key} "
                f"preset={payload['preset_key']} error={repr(e)}"
            )

    return created


async def bind_panel_message(
    *,
    guild_id: Any,
    panel_key: Any,
    channel_id: Any,
    message_id: Any,
) -> Optional[Dict[str, Any]]:
    panel = await get_ticket_panel(guild_id, panel_key)
    if panel is None:
        return None

    payload = dict(panel)
    payload["panel_channel_id"] = _normalize_channel_id(channel_id)
    payload["panel_message_id"] = _normalize_channel_id(message_id)
    return await upsert_ticket_panel(payload)


async def build_panel_runtime_config(guild_id: Any, panel_key: Any) -> Optional[Dict[str, Any]]:
    bundle = await get_ticket_panel_bundle(guild_id, panel_key)
    if bundle is None:
        return None

    panel = dict(bundle["panel"])
    rules = dict(DEFAULT_PANEL_RULES)
    rules.update(bundle.get("rules") or {})

    preset = bundle.get("preset") or {}
    preset_rules = _safe_json_dict(preset.get("default_rules_json"))
    merged_rules = dict(DEFAULT_PANEL_RULES)
    merged_rules.update(preset_rules)
    merged_rules.update(rules)

    categories = [
        row["category_slug"]
        for row in (bundle.get("categories") or [])
        if _safe_bool(row.get("is_enabled"), True) and _safe_str(row.get("category_slug"))
    ]

    return {
        "panel": panel,
        "rules": merged_rules,
        "categories": categories,
        "preset": preset or None,
    }


async def panel_creation_guard_scope(
    *,
    guild_id: Any,
    owner_id: Any,
    panel_key: Any,
    semaphore_limit: int = 8,
) -> Tuple[asyncio.Semaphore, asyncio.Lock]:
    sem = get_guild_panel_semaphore(guild_id, limit=semaphore_limit)
    lock = get_panel_creation_lock(guild_id=guild_id, owner_id=owner_id, panel_key=panel_key)
    return sem, lock
