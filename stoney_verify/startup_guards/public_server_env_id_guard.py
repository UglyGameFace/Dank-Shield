from __future__ import annotations

"""Disable deployment-level Discord snowflake IDs in public mode.

Operational secrets and tuning flags still live in env. Server-specific Discord
IDs do not: roles, channels, categories, and a home guild ID must come from each
server's saved guild_configs row once the bot is public.

This guard runs before the app imports the rest of the runtime modules, so legacy
constants in stoney_verify.globals cannot leak one server's IDs into another.
"""

import os
from typing import Any

_SERVER_ID_NAMES: tuple[str, ...] = (
    "GUILD_ID",
    "VERIFY_CHANNEL_ID",
    "VC_VERIFY_CHANNEL_ID",
    "VC_VERIFY_QUEUE_CHANNEL_ID",
    "VC_VERIFY_VC_ID",
    "TICKET_CATEGORY_ID",
    "TRANSCRIPTS_CHANNEL_ID",
    "JOIN_LOG_CHANNEL_ID",
    "UNVERIFIED_ROLE_ID",
    "VERIFIED_ROLE_ID",
    "RESIDENT_ROLE_ID",
    "STONER_ROLE_ID",
    "DRUNKEN_ROLE_ID",
    "STAFF_ROLE_ID",
    "VC_STAFF_ROLE_ID",
    "MODLOG_CHANNEL_ID",
    "RAIDLOG_CHANNEL_ID",
    "FORCE_VERIFY_LOG_CHANNEL_ID",
)


def _safe_str(value: Any, default: str = "") -> str:
    try:
        text = str(value or "").strip()
        return text if text else default
    except Exception:
        return default


def _env_bool(name: str, default: bool = False) -> bool:
    try:
        raw = _safe_str(os.getenv(name), "").lower()
        if raw in {"1", "true", "yes", "y", "on"}:
            return True
        if raw in {"0", "false", "no", "n", "off"}:
            return False
    except Exception:
        pass
    return bool(default)


def _public_mode() -> bool:
    profile = _safe_str(os.getenv("DANK_COMMAND_PROFILE"), "public").lower()
    deployment = _safe_str(os.getenv("DANK_DEPLOYMENT_MODE"), "").lower()
    if not deployment:
        deployment = _safe_str(os.getenv("DEPLOYMENT_ENV"), "production").lower()
    return bool(
        profile in {"public", "minimal"}
        or deployment in {"public", "prod", "production"}
        or _env_bool("DANK_PUBLIC_MODE", True)
        or _env_bool("DANK_PRODUCTION_MODE", False)
    )


def _allow_server_env_ids() -> bool:
    explicit = os.getenv("DANK_ALLOW_SERVER_ENV_IDS")
    if explicit is not None and _safe_str(explicit):
        return _env_bool("DANK_ALLOW_SERVER_ENV_IDS", False)
    legacy = os.getenv("DANK_SERVER_ENV_IDS_ENABLED")
    if legacy is not None and _safe_str(legacy):
        return _env_bool("DANK_SERVER_ENV_IDS_ENABLED", False)
    return not _public_mode()


def _log(message: str) -> None:
    try:
        print(f"🧭 public_server_env_id_guard {message}")
    except Exception:
        pass


def _warn(message: str) -> None:
    try:
        print(f"⚠️ public_server_env_id_guard {message}")
    except Exception:
        pass


def apply() -> bool:
    if _allow_server_env_ids():
        _log("server-specific env IDs allowed for this non-public/beta runtime")
        return True

    try:
        from stoney_verify import globals as g
    except Exception as e:
        _warn(f"could not import globals: {e!r}")
        return False

    ignored: list[str] = []
    for name in _SERVER_ID_NAMES:
        try:
            value = int(str(getattr(g, name, 0) or 0) or 0)
        except Exception:
            value = 0
        if value:
            ignored.append(name)
        try:
            setattr(g, name, 0)
        except Exception:
            pass

    try:
        g.OPTIONAL_ROLE_IDS = []
    except Exception:
        pass

    if ignored:
        _warn(
            "ignored deployment-level Discord IDs in public mode: "
            + ", ".join(sorted(set(ignored)))
            + "; use /dank setup per server instead"
        )
    else:
        _log("public mode active; deployment-level Discord IDs are disabled")

    return True


apply()

__all__ = ["apply"]
