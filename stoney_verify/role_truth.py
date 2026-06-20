from __future__ import annotations

"""Per-guild member role truth for Dank Shield.

This module is the native source of truth for verification/member role state.
Public multi-server production must never decide a member's verification state
from deployment/global role IDs. Every check here resolves the current guild's
saved setup config first and fails safe when the guild is not configured.
"""

import os
import time
from typing import Any, Mapping

import discord

_CACHE_TTL_SECONDS = 45.0
_CFG_CACHE: dict[int, tuple[float, dict[str, Any]]] = {}

PENDING_ROLE_KEYS: tuple[str, ...] = ("unverified_role_id",)
STAFF_ROLE_KEYS: tuple[str, ...] = ("staff_role_id", "vc_staff_role_id")
SECONDARY_SAFE_ROLE_KEYS: tuple[str, ...] = (
    "resident_role_id",
    "member_role_id",
    "stoner_role_id",
    "drunken_role_id",
)
PRIMARY_SAFE_ROLE_KEYS: tuple[str, ...] = ("verified_role_id",)
SAFE_ROLE_KEYS: tuple[str, ...] = PRIMARY_SAFE_ROLE_KEYS + SECONDARY_SAFE_ROLE_KEYS + STAFF_ROLE_KEYS


def safe_int(value: Any, default: int = 0) -> int:
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


def merge_config_row(row: Any) -> dict[str, Any]:
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


def clear_cache(guild_id: int | None = None) -> None:
    if guild_id is None:
        _CFG_CACHE.clear()
        return
    _CFG_CACHE.pop(int(guild_id), None)


def cached_guild_config(guild_id: int) -> dict[str, Any]:
    try:
        from stoney_verify import guild_config

        cache = getattr(guild_config, "_CONFIG_CACHE", None)
        if isinstance(cache, dict):
            row = cache.get(str(int(guild_id)))
            if isinstance(row, Mapping):
                return merge_config_row(row)
    except Exception:
        pass
    return {}


def get_guild_role_config(guild_id: int) -> dict[str, Any]:
    """Return best-effort saved config for the current guild only.

    This is sync on purpose because many member/event snapshot helpers are sync.
    It uses the existing guild_config cache first, then does a small Supabase
    lookup with a short TTL. It does not fall back to deployment role IDs.
    """

    gid = safe_int(guild_id, 0)
    if gid <= 0:
        return {}

    now = time.monotonic()
    cached = _CFG_CACHE.get(gid)
    if cached and now - cached[0] <= _CACHE_TTL_SECONDS:
        return dict(cached[1])

    cfg = cached_guild_config(gid)
    if cfg:
        _CFG_CACHE[gid] = (now, dict(cfg))
        return cfg

    try:
        from stoney_verify.globals import get_supabase

        sb = get_supabase()
        if sb is None:
            _CFG_CACHE[gid] = (now, {})
            return {}
        table = (os.getenv("DANK_GUILD_CONFIG_TABLE") or "guild_configs").strip() or "guild_configs"
        res = sb.table(table).select("*").eq("guild_id", str(gid)).limit(1).execute()
        rows = getattr(res, "data", None) or []
        if rows and isinstance(rows[0], Mapping):
            cfg = merge_config_row(rows[0])
            _CFG_CACHE[gid] = (now, dict(cfg))
            return cfg
    except Exception:
        pass

    _CFG_CACHE[gid] = (now, {})
    return {}


def cfg_id(cfg: Mapping[str, Any], key: str) -> int:
    return safe_int(cfg.get(key), 0)


def configured_role_ids(guild_id: int, keys: tuple[str, ...]) -> set[int]:
    cfg = get_guild_role_config(int(guild_id))
    out: set[int] = set()
    for key in keys:
        value = cfg_id(cfg, key)
        if value > 0:
            out.add(value)
    return out


def _iter_real_roles(member: discord.Member) -> list[Any]:
    try:
        roles = []
        for role in getattr(member, "roles", []) or []:
            try:
                if getattr(role, "is_default", lambda: False)():
                    continue
            except Exception:
                pass
            try:
                if str(getattr(role, "name", "")) == "@everyone":
                    continue
            except Exception:
                pass
            roles.append(role)
        return roles
    except Exception:
        return []


def member_role_ids(member: discord.Member) -> set[int]:
    out: set[int] = set()
    for role in _iter_real_roles(member):
        rid = safe_int(getattr(role, "id", 0), 0)
        if rid > 0:
            out.add(rid)
    return out


def member_has_role_id(member: discord.Member, role_id: int) -> bool:
    return safe_int(role_id, 0) in member_role_ids(member)


def base_member_role_snapshot(member: discord.Member) -> dict[str, Any]:
    role_ids: list[str] = []
    role_names: list[str] = []
    roles_json: list[dict[str, Any]] = []

    try:
        sorted_roles = sorted(
            _iter_real_roles(member),
            key=lambda role: int(getattr(role, "position", 0)),
            reverse=True,
        )
    except Exception:
        sorted_roles = []

    for role in sorted_roles:
        try:
            rid = str(getattr(role, "id", "") or "")
            rname = str(getattr(role, "name", "") or "")
            rpos = int(getattr(role, "position", 0) or 0)
            if not rid:
                continue
            role_ids.append(rid)
            role_names.append(rname)
            roles_json.append({"id": rid, "name": rname, "position": rpos})
        except Exception:
            continue

    return {
        "role_ids": role_ids,
        "role_names": role_names,
        "roles": roles_json,
        "top_role": role_names[0] if role_names else None,
        "highest_role_id": role_ids[0] if role_ids else None,
        "highest_role_name": role_names[0] if role_names else None,
        "data_health": "ok",
    }


def member_role_truth(member: discord.Member) -> dict[str, Any]:
    guild_id = safe_int(getattr(getattr(member, "guild", None), "id", 0), 0)
    roles = member_role_ids(member)
    pending = configured_role_ids(guild_id, PENDING_ROLE_KEYS) if guild_id else set()
    safe = configured_role_ids(guild_id, SAFE_ROLE_KEYS) if guild_id else set()
    staff = configured_role_ids(guild_id, STAFF_ROLE_KEYS) if guild_id else set()
    secondary = configured_role_ids(guild_id, SECONDARY_SAFE_ROLE_KEYS) if guild_id else set()
    verified_safe = safe - staff

    has_unverified = bool(roles & pending)
    has_staff = bool(roles & staff)
    has_verified = bool(roles & verified_safe)
    has_secondary = bool(roles & secondary)
    has_any_real = bool(roles - pending)
    has_cosmetic_only = bool(has_any_real and not has_verified and not has_staff and not has_unverified)

    return {
        "guild_id": guild_id,
        "configured": bool(pending or safe),
        "pending_role_ids": pending,
        "safe_role_ids": safe,
        "staff_role_ids": staff,
        "secondary_safe_role_ids": secondary,
        "member_role_ids": roles,
        "has_any_role": has_any_real,
        "has_unverified": has_unverified,
        "has_verified_role": has_verified,
        "has_staff_role": has_staff,
        "has_secondary_verified_role": has_secondary,
        "has_cosmetic_only": has_cosmetic_only,
        "is_pending_verification": bool(has_unverified and not has_verified and not has_staff),
        "has_safe_access_role": bool(roles & safe),
    }


def role_state_from_truth(member: discord.Member, truth: Mapping[str, Any] | None = None) -> tuple[str, str]:
    data = truth or member_role_truth(member)
    try:
        if bool(getattr(member, "bot", False)):
            return "bot_ok", "Member is a bot/app and should not be treated as unverified."
        if not data.get("member_role_ids"):
            return "unknown", "No tracked roles found."
        if data.get("has_staff_role") and data.get("has_unverified"):
            return "staff_conflict", "Member has both Staff and Unverified."
        if data.get("has_staff_role"):
            return "staff_ok", "Member has staff role."
        if data.get("has_verified_role") and data.get("has_unverified"):
            return "verified_conflict", "Member has both verified role and Unverified."
        if data.get("has_verified_role"):
            return "verified_ok", "Member has a configured safe access role and no Unverified role."
        if data.get("has_unverified"):
            return "unverified_only", "Member has Unverified and is pending verification."
        if data.get("has_cosmetic_only"):
            return "cosmetic_only", "Member has only cosmetic/non-verification roles."
        return "missing_unverified", "Member has no configured safe access role and no Unverified role."
    except Exception:
        return "unknown", "Role state evaluation failed."


def member_has_any_safe_access_role(member: discord.Member, *, include_unverified: bool = True) -> bool:
    truth = member_role_truth(member)
    if not truth.get("configured"):
        return False
    roles = set(truth.get("member_role_ids") or set())
    safe = set(truth.get("safe_role_ids") or set())
    if include_unverified:
        safe |= set(truth.get("pending_role_ids") or set())
    return bool(roles & safe)


def member_is_pending_verification(member: discord.Member) -> bool:
    truth = member_role_truth(member)
    if not truth.get("configured"):
        return False
    return bool(truth.get("is_pending_verification"))


def apply_truth_to_snapshot(member: discord.Member, snapshot: Mapping[str, Any] | None = None) -> dict[str, Any]:
    out = dict(snapshot or {})
    truth = member_role_truth(member)
    role_state, role_state_reason = role_state_from_truth(member, truth)
    out.update(
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
    return out


def build_member_role_snapshot(member: discord.Member) -> dict[str, Any]:
    return apply_truth_to_snapshot(member, base_member_role_snapshot(member))


__all__ = [
    "PENDING_ROLE_KEYS",
    "SAFE_ROLE_KEYS",
    "STAFF_ROLE_KEYS",
    "SECONDARY_SAFE_ROLE_KEYS",
    "apply_truth_to_snapshot",
    "base_member_role_snapshot",
    "build_member_role_snapshot",
    "clear_cache",
    "configured_role_ids",
    "get_guild_role_config",
    "member_has_any_safe_access_role",
    "member_has_role_id",
    "member_is_pending_verification",
    "member_role_ids",
    "member_role_truth",
    "role_state_from_truth",
    "safe_int",
]
