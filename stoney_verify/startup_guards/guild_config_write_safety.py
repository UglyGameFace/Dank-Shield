from __future__ import annotations

"""Central safety guard for guild configuration writes.

This guard protects public-server setup state from accidental overwrites.

Why this exists:
Many commands can write guild_configs while repairing setup, discovering roles,
auto-building defaults, bootstrapping panels, or saving setup choices. In public
servers, a careless helper command must never silently replace a role/channel/
category that the owner already picked in /dank setup.

Write contract:
- Saved config wins.
- Runtime discovery may fill blanks only.
- Auto-create may fill blanks only.
- Setup builder may write/update setup fields because it is the owner-facing
  setup surface.
- Explicit overrides may write/update only when a command deliberately marks the
  patch with __config_write_mode='explicit_override'.
- Unknown/default direct writes are treated as fill_missing.

This module patches stoney_verify.guild_config.upsert_guild_config at startup so
existing modules are protected even before each command is migrated to the new
safe writer API.
"""

import inspect
from datetime import datetime, timezone
from typing import Any, Mapping


_PATCHED = False
_ORIGINAL_UPSERT = None

PROTECTED_CONFIG_KEYS: frozenset[str] = frozenset(
    {
        "unverified_role_id",
        "verified_role_id",
        "resident_role_id",
        "member_role_id",
        "staff_role_id",
        "vc_staff_role_id",
        "server_control_role_id",
        "control_role_id",
        "perm_role_id",
        "bot_manager_role_id",
        "verify_channel_id",
        "vc_verify_channel_id",
        "vc_verify_queue_channel_id",
        "welcome_channel_id",
        "ticket_category_id",
        "ticket_archive_category_id",
        "ticket_closed_category_id",
        "ticket_panel_channel_id",
        "support_channel_id",
        "start_category_id",
        "welcome_category_id",
        "management_category_id",
        "staff_tools_category_id",
        "transcripts_channel_id",
        "modlog_channel_id",
        "raidlog_channel_id",
        "raid_log_channel_id",
        "join_log_channel_id",
        "join_exit_log_channel_id",
        "force_verify_log_channel_id",
        "status_channel_id",
        "bot_status_channel_id",
        "uptime_channel_id",
        "health_channel_id",
        "ticket_prefix",
        "verify_kick_hours",
        "use_env_fallbacks",
        "allow_runtime_discovery",
    }
)

CONTROL_KEYS: frozenset[str] = frozenset(
    {
        "__config_write_mode",
        "__config_write_source",
        "__config_write_reason",
        "__config_write_actor_id",
        "__config_write_allow_keys",
        "__config_write_dry_run",
    }
)

ALLOWED_MODES: frozenset[str] = frozenset(
    {
        "fill_missing",
        "runtime_discovery",
        "auto_discover",
        "auto_create",
        "setup_builder",
        "explicit_override",
        "force",
    }
)

OVERWRITE_MODES: frozenset[str] = frozenset({"setup_builder", "explicit_override", "force"})
FILL_ONLY_MODES: frozenset[str] = frozenset({"fill_missing", "runtime_discovery", "auto_discover", "auto_create"})


def _log(message: str) -> None:
    try:
        print(f"🧷 guild_config_write_safety {message}")
    except Exception:
        pass


def _warn(message: str) -> None:
    try:
        print(f"⚠️ guild_config_write_safety {message}")
    except Exception:
        pass


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _setup_safe_defer_modal(interaction: Any) -> None:
    try:
        response = getattr(interaction, "response", None)
        if response is not None and not response.is_done():
            await response.defer(ephemeral=True, thinking=True)
    except Exception:
        pass


def _patch_setup_modal_defer_helper() -> None:
    try:
        from stoney_verify.commands_ext import public_setup_solid as solid

        if not callable(getattr(solid, "_safe_defer_modal", None)):
            setattr(solid, "_safe_defer_modal", _setup_safe_defer_modal)
            _log("attached setup modal defer helper")
    except Exception as exc:
        _warn(f"could not attach setup modal defer helper: {type(exc).__name__}: {exc}")


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
    if value is None:
        return None
    if key.endswith("_id"):
        try:
            num = int(str(value).strip())
            return str(num) if num > 0 else None
        except Exception:
            return None
    if isinstance(value, bool):
        return value
    return str(value).strip() if isinstance(value, str) else value


def _same_value(left: Any, right: Any) -> bool:
    if _is_empty(left) and _is_empty(right):
        return True
    return str(left).strip() == str(right).strip()


def _patch_dict(patch: Any) -> dict[str, Any]:
    try:
        if isinstance(patch, Mapping):
            return dict(patch)
    except Exception:
        pass
    return {}


def _get_cfg_value(cfg: Any, key: str) -> Any:
    try:
        if hasattr(cfg, "get"):
            return cfg.get(key)
    except Exception:
        pass
    try:
        return getattr(cfg, key, None)
    except Exception:
        return None


def _infer_mode(patch: Mapping[str, Any], source: str) -> str:
    explicit = _safe_str(patch.get("__config_write_mode"), "").lower()
    if explicit in ALLOWED_MODES:
        return explicit

    source_l = source.lower()
    if "setup" in source_l or "builder" in source_l:
        return "setup_builder"

    if any(k in patch for k in ("configured_by_id", "configured_by_name", "configured_at", "setup_version", "default_setup_version")):
        return "setup_builder"

    if "auto_create" in source_l or "auto-build" in source_l or "autobuild" in source_l:
        return "auto_create"

    if "discover" in source_l or "discovery" in source_l:
        return "runtime_discovery"

    return "fill_missing"


def _source_from_stack() -> str:
    try:
        frames = inspect.stack(context=0)
        parts: list[str] = []
        for frame in frames[2:9]:
            filename = str(getattr(frame, "filename", "") or "")
            func = str(getattr(frame, "function", "") or "")
            if "guild_config_write_safety" in filename or "guild_config.py" in filename:
                continue
            short = filename.replace("\\", "/").split("/stoney_verify/")[-1]
            parts.append(f"{short}:{func}")
            if len(parts) >= 3:
                break
        return " <- ".join(parts) if parts else "unknown"
    except Exception:
        return "unknown"


def _allowed_key_override(patch: Mapping[str, Any]) -> set[str]:
    raw = patch.get("__config_write_allow_keys")
    out: set[str] = set()
    try:
        if isinstance(raw, str):
            items = [x.strip() for x in raw.split(",")]
        elif isinstance(raw, (list, tuple, set)):
            items = [str(x).strip() for x in raw]
        else:
            items = []
        out = {x for x in items if x}
    except Exception:
        out = set()
    return out


def _filter_patch(guild_id: Any, patch: Mapping[str, Any], current: Any, *, source: str, mode: str) -> tuple[dict[str, Any], list[str], list[str]]:
    allow_keys = _allowed_key_override(patch)
    clean: dict[str, Any] = {}
    blocked: list[str] = []
    changed: list[str] = []

    for key, raw_value in patch.items():
        key = str(key)
        if key in CONTROL_KEYS:
            continue

        value = _normalize_value(key, raw_value)
        if value is None:
            continue

        protected = key in PROTECTED_CONFIG_KEYS or key.endswith("_role_id") or key.endswith("_channel_id") or key.endswith("_category_id")
        if not protected:
            clean[key] = value
            continue

        old_value = _get_cfg_value(current, key)
        if _is_empty(old_value):
            clean[key] = value
            changed.append(f"{key}=set")
            continue

        if _same_value(old_value, value):
            clean[key] = value
            continue

        key_override_allowed = key in allow_keys
        if mode in OVERWRITE_MODES or key_override_allowed:
            clean[key] = value
            changed.append(f"{key}: {old_value} -> {value}")
            continue

        blocked.append(f"{key}: kept existing {old_value}, blocked attempted {value}")

    if blocked:
        _warn(
            f"blocked config overwrite guild={guild_id} mode={mode} source={source} "
            f"blocked={blocked[:8]}"
        )
    if changed:
        _log(f"allowed config changes guild={guild_id} mode={mode} source={source} changes={changed[:8]}")

    return clean, blocked, changed


async def _safe_upsert_guild_config(guild_id: Any, patch: Mapping[str, Any], *args: Any, **kwargs: Any) -> Any:
    from stoney_verify import guild_config as gc

    global _ORIGINAL_UPSERT
    if _ORIGINAL_UPSERT is None:
        _ORIGINAL_UPSERT = getattr(gc, "_unsafe_original_upsert_guild_config", None)
    original = _ORIGINAL_UPSERT or getattr(gc, "upsert_guild_config")

    raw_patch = _patch_dict(patch)
    source = _safe_str(raw_patch.get("__config_write_source"), "") or _source_from_stack()
    mode = _infer_mode(raw_patch, source)
    dry_run = _safe_bool(raw_patch.get("__config_write_dry_run"), False)

    try:
        current = await gc.get_guild_config(guild_id, refresh=True)
    except Exception:
        current = {}

    clean_patch, blocked, changed = _filter_patch(guild_id, raw_patch, current, source=source, mode=mode)

    if clean_patch:
        clean_patch.setdefault("config_last_write_source", source[:300])
        clean_patch.setdefault("config_last_write_mode", mode)
        clean_patch.setdefault("config_last_write_at", _now_iso())
        if blocked:
            clean_patch.setdefault("config_last_blocked_overwrite", " | ".join(blocked)[:1000])

    if dry_run:
        preview = dict(current)
        preview.update(clean_patch)
        preview["_dry_run"] = True
        preview["_blocked_overwrites"] = blocked
        preview["_allowed_changes"] = changed
        return gc.GuildRuntimeConfig(preview)

    if not clean_patch:
        _warn(f"ignored empty/blocked config write guild={guild_id} mode={mode} source={source}")
        try:
            return await gc.get_guild_config(guild_id, refresh=True)
        except Exception:
            return current

    return await original(guild_id, clean_patch, *args, **kwargs)


def patch_guild_config_writer() -> bool:
    global _PATCHED, _ORIGINAL_UPSERT
    if _PATCHED:
        _patch_setup_modal_defer_helper()
        return True

    try:
        from stoney_verify import guild_config as gc

        current = getattr(gc, "upsert_guild_config", None)
        if not callable(current):
            _warn("guild_config.upsert_guild_config not found")
            return False

        if getattr(current, "_config_write_safety_wrapped", False):
            _PATCHED = True
            _patch_setup_modal_defer_helper()
            return True

        _ORIGINAL_UPSERT = current
        setattr(gc, "_unsafe_original_upsert_guild_config", current)
        setattr(_safe_upsert_guild_config, "_config_write_safety_wrapped", True)
        setattr(gc, "upsert_guild_config", _safe_upsert_guild_config)
        _PATCHED = True
        _log("loaded; central guild_config write protection active")
        _patch_setup_modal_defer_helper()
        return True
    except Exception as e:
        _warn(f"failed to patch guild config writer: {e!r}")
        return False


patch_guild_config_writer()


__all__ = [
    "FILL_ONLY_MODES",
    "OVERWRITE_MODES",
    "PROTECTED_CONFIG_KEYS",
    "patch_guild_config_writer",
]
