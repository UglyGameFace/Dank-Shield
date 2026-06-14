from __future__ import annotations

"""Canonical per-guild verification-mode policy.

Public Dank Shield servers default to simple Discord button verification:
Unverified clicks Verify, Dank Shield grants the configured Verified/full-access
role and removes Unverified.

ID / website upload verification is intentionally special-case. It is only
available in allowlisted guild IDs so old Stoney Balonney / Stoners Paradise ID
panels never leak into unrelated public servers such as The 420 Lobby.
"""

import os
from typing import Any, Mapping

DEFAULT_ID_VERIFY_ALLOWED_GUILD_IDS: frozenset[int] = frozenset({1357215261001912320})
DEFAULT_ID_VERIFY_ALLOWED_GUILD_NAMES: frozenset[str] = frozenset()
BASIC_VERIFY_CUSTOM_ID = "dank:basic_verify:v1"
BASIC_VERIFY_FOOTER = "dank_shield:basic_verify:v1"


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or isinstance(value, bool):
            return int(default)
        text = str(value).strip()
        if not text or text.lower() in {"none", "null"}:
            return int(default)
        return int(text)
    except Exception:
        return int(default)


def _safe_str(value: Any, default: str = "") -> str:
    try:
        text = str(value or "").strip()
        return text if text else str(default or "")
    except Exception:
        return str(default or "")


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


def _env_id_set(name: str) -> set[int]:
    raw = os.getenv(name, "")
    out: set[int] = set()
    for part in str(raw or "").replace(";", ",").split(","):
        value = _safe_int(part, 0)
        if value > 0:
            out.add(value)
    return out


def _env_name_set(name: str) -> set[str]:
    raw = os.getenv(name, "")
    out: set[str] = set()
    for part in str(raw or "").replace(";", ",").split(","):
        text = _safe_str(part).lower()
        if text:
            out.add(text)
    return out


def id_verify_allowed_guild_ids() -> set[int]:
    configured = _env_id_set("DANK_ID_VERIFY_ALLOWED_GUILD_IDS") | _env_id_set("STONEY_ID_VERIFY_ALLOWED_GUILD_IDS")
    return set(DEFAULT_ID_VERIFY_ALLOWED_GUILD_IDS) | configured


def id_verify_allowed_guild_names() -> set[str]:
    # Name matching is opt-in only through env. The built-in safety policy is ID-only.
    return _env_name_set("DANK_ID_VERIFY_ALLOWED_GUILD_NAMES") | _env_name_set("STONEY_ID_VERIFY_ALLOWED_GUILD_NAMES")


def guild_id(guild: Any) -> int:
    return _safe_int(getattr(guild, "id", 0), 0)


def guild_name(guild: Any) -> str:
    return _safe_str(getattr(guild, "name", "")).lower()


def id_verify_allowed_for_guild(guild: Any, cfg: Any = None) -> bool:
    gid = guild_id(guild)
    if gid > 0 and gid in id_verify_allowed_guild_ids():
        return True
    name = guild_name(guild)
    if name and name in id_verify_allowed_guild_names():
        return True

    # Do not allow a random guild_config field to enable ID verification outside
    # the allowlist. That prevents public servers from accidentally inheriting an
    # old Stoney Balonney web panel.
    _ = cfg
    return False


def config_requests_id_verify(cfg: Any) -> bool:
    mode = _safe_str(
        _cfg_value(cfg, "verification_mode")
        or _cfg_value(cfg, "verify_mode")
        or _cfg_value(cfg, "verification_flow")
        or _cfg_value(cfg, "setup_type")
    ).lower().replace("-", "_").replace(" ", "_")
    if mode in {"id", "id_verify", "identity", "identity_verify", "website", "web_verify", "upload_id"}:
        return True
    for key in ("id_verify_enabled", "identity_verify_enabled", "website_verify_enabled", "require_id_verify"):
        raw = _cfg_value(cfg, key, None)
        if str(raw).strip().lower() in {"1", "true", "yes", "on"}:
            return True
    return False


def effective_verification_mode(guild: Any, cfg: Any = None) -> str:
    if config_requests_id_verify(cfg) and id_verify_allowed_for_guild(guild, cfg):
        return "id_verify"
    return "basic_button"


def id_verify_disabled_reason(guild: Any, cfg: Any = None) -> str:
    if id_verify_allowed_for_guild(guild, cfg):
        return ""
    gid = guild_id(guild)
    name = getattr(guild, "name", "this server")
    return (
        f"ID verification is not enabled for {name} (`{gid}`). "
        "This server uses Basic Button Verification. ID/web upload verification is restricted to allowlisted guild IDs."
    )


__all__ = [
    "BASIC_VERIFY_CUSTOM_ID",
    "BASIC_VERIFY_FOOTER",
    "DEFAULT_ID_VERIFY_ALLOWED_GUILD_IDS",
    "effective_verification_mode",
    "config_requests_id_verify",
    "id_verify_allowed_for_guild",
    "id_verify_disabled_reason",
]
