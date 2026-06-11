from __future__ import annotations

"""Per-guild verification role truth for legacy member/event helpers.

Some older runtime helpers still read deployment/global role IDs such as
VERIFIED_ROLE_ID or STONER_ROLE_ID. That is unsafe for a public multi-server bot:
one server's env IDs must never decide another server's verification state.

This guard keeps the old helpers but makes their role truth come from the current
guild's saved config. If no per-guild config exists, the helpers fail safe by not
claiming a member is pending verification rather than kicking/removing them from
a neutral state.
"""

import os
import time
from typing import Any, Mapping

import discord

_PATCHED = False
_CACHE_TTL_SECONDS = 45.0
_CFG_CACHE: dict[int, tuple[float, dict[str, Any]]] = {}

_PENDING_KEYS = ("unverified_role_id",)
_SAFE_KEYS = (
    "verified_role_id",
    "resident_role_id",
    "member_role_id",
    "staff_role_id",
    "vc_staff_role_id",
    "stoner_role_id",
    "drunken_role_id",
)
_SECONDARY_KEYS = ("resident_role_id", "member_role_id", "stoner_role_id", "drunken_role_id")


def _log(message: str) -> None:
    try:
        print(f"🧭 per_guild_role_truth_guard {message}")
    except Exception:
        pass


def _warn(message: str) -> None:
    try:
        print(f"⚠️ per_guild_role_truth_guard {message}")
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


def _mapping_dict(value: Any) -> dict[str, Any]:
    try:
        if isinstance(value, Mapping):
            return dict(value)
    except Exception:
        pass
    return {}


def _merge_row(row: Any) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    raw = _mapping_dict(row)
    for key in ("settings", "config", "metadata", "meta"):
        nested = _mapping_dict(raw.get(key))
        if nested:
            merged.update(nested)
    for key, value in raw.items():
        if key in {"settings", "config", "metadata", "meta"}:
            continue
        if value is not None:
            merged[str(key)] = value
    return merged


def _cached_guild_config(guild_id: int) -> dict[str, Any]:
    try:
        from stoney_verify import guild_config

        cache = getattr(guild_config, "_CONFIG_CACHE", None)
        if isinstance(cache, dict):
            row = cache.get(str(int(guild_id)))
            if isinstance(row, Mapping):
                return _merge_row(row)
    except Exception:
        pass
    return {}


def _db_guild_config(guild_id: int) -> dict[str, Any]:
    now = time.monotonic()
    cached = _CFG_CACHE.get(int(guild_id))
    if cached and now - cached[0] <= _CACHE_TTL_SECONDS:
        return dict(cached[1])

    cfg = _cached_guild_config(guild_id)
    if cfg:
        _CFG_CACHE[int(guild_id)] = (now, dict(cfg))
        return cfg

    try:
        from stoney_verify.globals import get_supabase

        sb = get_supabase()
        if sb is None:
            return {}
        table = (os.getenv("STONEY_GUILD_CONFIG_TABLE") or "guild_configs").strip() or "guild_configs"
        res = sb.table(table).select("*").eq("guild_id", str(int(guild_id))).limit(1).execute()
        rows = getattr(res, "data", None) or []
        if rows and isinstance(rows[0], Mapping):
            cfg = _merge_row(rows[0])
            _CFG_CACHE[int(guild_id)] = (now, dict(cfg))
            return cfg
    except Exception:
        pass

    _CFG_CACHE[int(guild_id)] = (now, {})
    return {}


def _cfg_id(cfg: Mapping[str, Any], key: str) -> int:
    return _safe_int(cfg.get(key), 0)


def _ids_for(guild_id: int, keys: tuple[str, ...]) -> set[int]:
    cfg = _db_guild_config(int(guild_id))
    out: set[int] = set()
    for key in keys:
        value = _cfg_id(cfg, key)
        if value > 0:
            out.add(value)
    return out


def _member_role_ids(member: discord.Member) -> set[int]:
    out: set[int] = set()
    try:
        for role in getattr(member, "roles", []) or []:
            try:
                if getattr(role, "is_default", lambda: False)():
                    continue
            except Exception:
                pass
            rid = _safe_int(getattr(role, "id", 0), 0)
            if rid > 0:
                out.add(rid)
    except Exception:
        pass
    return out


def _role_truth(member: discord.Member) -> dict[str, Any]:
    guild_id = _safe_int(getattr(getattr(member, "guild", None), "id", 0), 0)
    role_ids = _member_role_ids(member)
    pending_ids = _ids_for(guild_id, _PENDING_KEYS) if guild_id else set()
    safe_ids = _ids_for(guild_id, _SAFE_KEYS) if guild_id else set()
    secondary_ids = _ids_for(guild_id, _SECONDARY_KEYS) if guild_id else set()

    has_unverified = bool(role_ids & pending_ids)
    has_staff = bool(role_ids & _ids_for(guild_id, ("staff_role_id", "vc_staff_role_id"))) if guild_id else False
    has_verified = bool(role_ids & (safe_ids - _ids_for(guild_id, ("staff_role_id", "vc_staff_role_id")))) if guild_id else False
    has_secondary = bool(role_ids & secondary_ids)
    has_any_real = bool(role_ids - pending_ids)
    has_cosmetic_only = bool(has_any_real and not has_verified and not has_staff and not has_unverified)
    pending = bool(has_unverified and not has_verified and not has_staff)

    return {
        "configured": bool(pending_ids or safe_ids),
        "pending_ids": pending_ids,
        "safe_ids": safe_ids,
        "secondary_ids": secondary_ids,
        "role_ids": role_ids,
        "has_unverified": has_unverified,
        "has_verified_role": has_verified,
        "has_staff_role": has_staff,
        "has_secondary_verified_role": has_secondary,
        "has_any_role": has_any_real,
        "has_cosmetic_only": has_cosmetic_only,
        "is_pending_verification": pending,
    }


def _role_state_from_truth(member: discord.Member, truth: Mapping[str, Any]) -> tuple[str, str]:
    try:
        if bool(getattr(member, "bot", False)):
            return "bot_ok", "Member is a bot/app and should not be treated as unverified."
        if not truth.get("role_ids"):
            return "unknown", "No tracked roles found."
        if truth.get("has_staff_role") and truth.get("has_unverified"):
            return "staff_conflict", "Member has both Staff and Unverified."
        if truth.get("has_staff_role"):
            return "staff_ok", "Member has staff role."
        if truth.get("has_verified_role") and truth.get("has_unverified"):
            return "verified_conflict", "Member has both verified role and Unverified."
        if truth.get("has_verified_role"):
            return "verified_ok", "Member has a configured safe access role and no Unverified role."
        if truth.get("has_unverified"):
            return "unverified_only", "Member has Unverified and is pending verification."
        if truth.get("has_cosmetic_only"):
            return "cosmetic_only", "Member has only cosmetic/non-verification roles."
        return "missing_unverified", "Member has no configured safe access role and no Unverified role."
    except Exception:
        return "unknown", "Role state evaluation failed."


def _patch_events() -> bool:
    try:
        from stoney_verify import events
    except Exception as e:
        _warn(f"events import failed: {e!r}")
        return False

    original_snapshot = getattr(events, "_member_role_snapshot", None)

    def member_has_any_safe_access_role(member: discord.Member, *, include_unverified: bool = True) -> bool:
        truth = _role_truth(member)
        if not truth.get("configured"):
            return False
        role_ids = truth["role_ids"]
        safe_ids = set(truth["safe_ids"])
        if include_unverified:
            safe_ids |= set(truth["pending_ids"])
        return bool(role_ids & safe_ids)

    def member_is_pending_verification(member: discord.Member) -> bool:
        truth = _role_truth(member)
        if not truth.get("configured"):
            return False
        return bool(truth.get("is_pending_verification"))

    def member_role_snapshot(member: discord.Member) -> dict[str, Any]:
        base: dict[str, Any] = {}
        if callable(original_snapshot):
            try:
                base = dict(original_snapshot(member) or {})
            except Exception:
                base = {}
        truth = _role_truth(member)
        role_state, role_state_reason = _role_state_from_truth(member, truth)
        base.update(
            {
                "has_any_role": bool(truth.get("has_any_role")),
                "has_unverified": bool(truth.get("has_unverified")),
                "has_verified_role": bool(truth.get("has_verified_role")),
                "has_staff_role": bool(truth.get("has_staff_role")),
                "has_secondary_verified_role": bool(truth.get("has_secondary_verified_role")),
                "has_cosmetic_only": bool(truth.get("has_cosmetic_only")),
                "role_state": role_state,
                "role_state_reason": role_state_reason,
            }
        )
        return base

    events._member_has_any_safe_access_role = member_has_any_safe_access_role  # type: ignore[attr-defined]
    events._member_is_pending_verification = member_is_pending_verification  # type: ignore[attr-defined]
    events._member_role_snapshot = member_role_snapshot  # type: ignore[attr-defined]
    return True


def _patch_sync_service() -> bool:
    try:
        from stoney_verify.members_new import sync_service
    except Exception as e:
        _warn(f"members_new.sync_service import failed: {e!r}")
        return False

    original_snapshot = getattr(sync_service, "_member_role_snapshot", None)

    def member_role_snapshot(member: discord.Member) -> dict[str, Any]:
        base: dict[str, Any] = {}
        if callable(original_snapshot):
            try:
                base = dict(original_snapshot(member) or {})
            except Exception:
                base = {}
        truth = _role_truth(member)
        role_state, role_state_reason = _role_state_from_truth(member, truth)
        base.update(
            {
                "has_any_role": bool(truth.get("has_any_role")),
                "has_unverified": bool(truth.get("has_unverified")),
                "has_verified_role": bool(truth.get("has_verified_role")),
                "has_staff_role": bool(truth.get("has_staff_role")),
                "has_secondary_verified_role": bool(truth.get("has_secondary_verified_role")),
                "has_cosmetic_only": bool(truth.get("has_cosmetic_only")),
                "role_state": role_state,
                "role_state_reason": role_state_reason,
            }
        )
        return base

    sync_service._member_role_snapshot = member_role_snapshot  # type: ignore[attr-defined]
    return True


def _patch_legacy_service() -> bool:
    try:
        from stoney_verify.members_new import service
    except Exception as e:
        _warn(f"members_new.service import failed: {e!r}")
        return False

    def member_role_flags(member: discord.Member) -> dict[str, bool]:
        truth = _role_truth(member)
        return {
            "has_any_role": bool(truth.get("has_any_role")),
            "has_unverified": bool(truth.get("has_unverified")),
            "has_verified_role": bool(truth.get("has_verified_role")),
            "has_staff_role": bool(truth.get("has_staff_role")),
            "has_secondary_verified_role": bool(truth.get("has_secondary_verified_role")),
            "has_cosmetic_only": bool(truth.get("has_cosmetic_only")),
        }

    def role_state(member: discord.Member) -> dict[str, str]:
        state, reason = _role_state_from_truth(member, _role_truth(member))
        return {"role_state": state, "role_state_reason": reason}

    def has_verified_role(member: discord.Member) -> bool:
        truth = _role_truth(member)
        return bool(truth.get("has_verified_role") and not truth.get("has_unverified"))

    service._member_role_flags = member_role_flags  # type: ignore[attr-defined]
    service._role_state = role_state  # type: ignore[attr-defined]
    service._has_verified_role = has_verified_role  # type: ignore[attr-defined]
    return True


def apply() -> bool:
    global _PATCHED
    if _PATCHED:
        return True
    events_ok = _patch_events()
    sync_ok = _patch_sync_service()
    legacy_ok = _patch_legacy_service()
    _PATCHED = True
    _log(f"active events={events_ok} sync_service={sync_ok} legacy_service={legacy_ok}")
    return bool(events_ok or sync_ok or legacy_ok)


apply()

__all__ = ["apply"]
