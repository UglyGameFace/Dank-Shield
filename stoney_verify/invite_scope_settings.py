from __future__ import annotations

"""Canonical guild-scoped Invite Shield target settings.

The old protection UI stored invite target metadata through startup-guard
wrappers around Spam Guard. That made policy persistence depend on import order.
This module owns those target settings directly in guild config so the canonical
invite policy and Protection Center can share one durable source without
monkey-patching Spam Guard.
"""

from typing import Any, Mapping

from .guild_config import get_guild_config, invalidate_guild_config, upsert_guild_config
from .settings_registry import (
    INVITE_PROTECTED_POSTER_RULE_KEY,
    INVITE_TARGET_ALL_BOTS_KEY,
    INVITE_TARGET_BOT_IDS_KEY,
    INVITE_TARGET_CHANNEL_IDS_KEY,
    coerce_bool,
    coerce_ids,
    invite_scope_values,
)

ALL_BOTS_KEY = INVITE_TARGET_ALL_BOTS_KEY
BOT_IDS_KEY = INVITE_TARGET_BOT_IDS_KEY
CHANNEL_IDS_KEY = INVITE_TARGET_CHANNEL_IDS_KEY
PROTECTED_RULE_KEY = INVITE_PROTECTED_POSTER_RULE_KEY

_SCOPE_KEYS = (ALL_BOTS_KEY, BOT_IDS_KEY, CHANNEL_IDS_KEY, PROTECTED_RULE_KEY)
_POLICY_BOUND = False
_ORIGINAL_POLICY_LOAD: Any = None


def parse_bool(value: Any, default: bool = False) -> bool:
    return coerce_bool(value, default)


def parse_ids(value: Any, *, limit: int = 100) -> list[str]:
    return coerce_ids(value, limit=limit)


def normalize_scope(cfg: Any) -> dict[str, Any]:
    return invite_scope_values(cfg)


async def load_invite_scope_settings(guild_id: int, *, refresh: bool = False) -> dict[str, Any]:
    cfg = await get_guild_config(int(guild_id), refresh=refresh)
    return normalize_scope(cfg)


def merge_scope_settings(settings: Mapping[str, Any] | None, scope: Mapping[str, Any] | None) -> dict[str, Any]:
    merged = dict(settings or {})
    clean = normalize_scope(dict(scope or {}))
    merged.update(clean)
    for key, value in clean.items():
        merged[f"spam_{key}"] = value
    return merged


async def save_invite_scope_settings(guild_id: int, patch: Mapping[str, Any]) -> dict[str, Any]:
    gid = int(guild_id)
    current = await load_invite_scope_settings(gid, refresh=True)
    incoming = dict(patch or {})

    if ALL_BOTS_KEY in incoming:
        current[ALL_BOTS_KEY] = parse_bool(incoming[ALL_BOTS_KEY], False)
    if BOT_IDS_KEY in incoming:
        current[BOT_IDS_KEY] = parse_ids(incoming[BOT_IDS_KEY])
    if CHANNEL_IDS_KEY in incoming:
        current[CHANNEL_IDS_KEY] = parse_ids(incoming[CHANNEL_IDS_KEY])
    if PROTECTED_RULE_KEY in incoming:
        current[PROTECTED_RULE_KEY] = parse_bool(incoming[PROTECTED_RULE_KEY], False)

    payload: dict[str, Any] = {}
    for key in _SCOPE_KEYS:
        value = current[key]
        payload[key] = value
        payload[f"spam_{key}"] = value

    await upsert_guild_config(gid, payload)
    invalidate_guild_config(gid)
    try:
        from .invite_policy_engine import invalidate_invite_policy

        invalidate_invite_policy(gid)
    except Exception:
        pass
    return dict(current)


def install_invite_policy_scope_binding() -> bool:
    """Make the canonical policy consume target metadata from guild config.

    ``invite_policy_engine`` already owns all delete decisions but its historical
    target matcher receives the Spam Guard settings dictionary. The old startup
    guards solved that by monkey-patching Spam Guard globally. This explicit
    boot binding leaves Spam Guard untouched: it only augments the policy's own
    loader with scope values from the guild config object that loader already
    fetched.
    """

    global _POLICY_BOUND, _ORIGINAL_POLICY_LOAD
    if _POLICY_BOUND:
        return True
    try:
        from . import invite_policy_engine as policy

        original = getattr(policy, "load_invite_policy", None)
        if not callable(original):
            return False
        if getattr(original, "_dank_scope_bound", False):
            _POLICY_BOUND = True
            return True

        async def load_with_scope(guild: Any, *, refresh: bool = False):
            cfg, settings = await original(guild, refresh=refresh)
            return cfg, merge_scope_settings(settings, normalize_scope(cfg))

        load_with_scope._dank_scope_bound = True  # type: ignore[attr-defined]
        load_with_scope._dank_scope_original = original  # type: ignore[attr-defined]
        _ORIGINAL_POLICY_LOAD = original
        policy.load_invite_policy = load_with_scope
        _POLICY_BOUND = True
        return True
    except Exception:
        return False


__all__ = [
    "ALL_BOTS_KEY",
    "BOT_IDS_KEY",
    "CHANNEL_IDS_KEY",
    "PROTECTED_RULE_KEY",
    "install_invite_policy_scope_binding",
    "load_invite_scope_settings",
    "merge_scope_settings",
    "normalize_scope",
    "parse_bool",
    "parse_ids",
    "save_invite_scope_settings",
]
