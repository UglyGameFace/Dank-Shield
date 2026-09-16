from __future__ import annotations

"""Canonical guild-scoped Invite Shield target settings.

The old protection UI stored invite target metadata through startup-guard
wrappers around Spam Guard. That made policy persistence depend on import order.
This module owns those target settings directly in guild config so the canonical
invite policy and Protection Center can share one durable source without
monkey-patching Spam Guard.
"""

import re
from typing import Any, Mapping

from .guild_config import get_guild_config, invalidate_guild_config, upsert_guild_config

ALL_BOTS_KEY = "invite_hard_block_target_all_bots"
BOT_IDS_KEY = "invite_hard_block_target_bot_ids"
CHANNEL_IDS_KEY = "invite_hard_block_target_channel_ids"
PROTECTED_RULE_KEY = "invite_protected_poster_rule_enabled"

_SCOPE_KEYS = (ALL_BOTS_KEY, BOT_IDS_KEY, CHANNEL_IDS_KEY, PROTECTED_RULE_KEY)


def _cfg_value(cfg: Any, key: str, default: Any = None) -> Any:
    try:
        value = getattr(cfg, key, None)
        if value is not None:
            return value
    except Exception:
        pass
    try:
        if hasattr(cfg, "get"):
            value = cfg.get(key)
            if value is not None:
                return value
    except Exception:
        pass
    for bucket in ("settings", "config", "metadata", "meta"):
        try:
            nested = getattr(cfg, bucket, None)
            if isinstance(nested, Mapping) and nested.get(key) is not None:
                return nested.get(key)
        except Exception:
            pass
        try:
            if hasattr(cfg, "get"):
                nested = cfg.get(bucket)
                if isinstance(nested, Mapping) and nested.get(key) is not None:
                    return nested.get(key)
        except Exception:
            pass
    return default


def parse_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return bool(default)
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y", "on", "enabled", "all"}:
        return True
    if text in {"0", "false", "no", "n", "off", "disabled", "none"}:
        return False
    return bool(default)


def parse_ids(value: Any, *, limit: int = 100) -> list[str]:
    if isinstance(value, (list, tuple, set)):
        raw_items = list(value)
    else:
        raw_items = re.split(r"[\s,;]+", str(value or ""))

    out: list[str] = []
    for raw in raw_items:
        text = str(raw or "").strip().strip("<@#!&>")
        if text.isdigit() and text not in out:
            out.append(text)
        if len(out) >= max(1, int(limit)):
            break
    return out


def _read_alias(cfg: Any, key: str, default: Any = None) -> Any:
    value = _cfg_value(cfg, key, None)
    if value is None:
        value = _cfg_value(cfg, f"spam_{key}", None)
    if value is None:
        legacy = {
            ALL_BOTS_KEY: "invite_target_all_bots",
            BOT_IDS_KEY: "invite_target_bot_ids",
            CHANNEL_IDS_KEY: "invite_target_channel_ids",
            PROTECTED_RULE_KEY: "protected_poster_invite_rule_enabled",
        }.get(key)
        if legacy:
            value = _cfg_value(cfg, legacy, None)
    return default if value is None else value


def normalize_scope(cfg: Any) -> dict[str, Any]:
    return {
        ALL_BOTS_KEY: parse_bool(_read_alias(cfg, ALL_BOTS_KEY, False), False),
        BOT_IDS_KEY: parse_ids(_read_alias(cfg, BOT_IDS_KEY, [])),
        CHANNEL_IDS_KEY: parse_ids(_read_alias(cfg, CHANNEL_IDS_KEY, [])),
        PROTECTED_RULE_KEY: parse_bool(_read_alias(cfg, PROTECTED_RULE_KEY, False), False),
    }


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


__all__ = [
    "ALL_BOTS_KEY",
    "BOT_IDS_KEY",
    "CHANNEL_IDS_KEY",
    "PROTECTED_RULE_KEY",
    "load_invite_scope_settings",
    "merge_scope_settings",
    "normalize_scope",
    "parse_bool",
    "parse_ids",
    "save_invite_scope_settings",
]
