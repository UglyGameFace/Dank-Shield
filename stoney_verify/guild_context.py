from __future__ import annotations

"""Central per-guild runtime context for Dank Shield.

This module is the safe migration target away from direct deployment/global ID
reads inside ticketing, setup, protection, verification, and future premium
features.

Design rules:
- DB-backed guild config stays authoritative.
- Environment IDs are treated as legacy fallback only through guild_config.
- Public isolated/unconfigured guilds resolve as unsafe-to-act instead of
  inheriting another server's roles/channels/categories.
- This module does not mutate Discord, database rows, or existing setup state.
"""

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

from .guild_config import GuildRuntimeConfig, get_guild_config, public_config_isolation_enabled

_CHANNEL_KEYS: tuple[str, ...] = (
    "verify_channel_id",
    "vc_verify_channel_id",
    "vc_verify_queue_channel_id",
    "ticket_category_id",
    "ticket_archive_category_id",
    "transcripts_channel_id",
    "status_channel_id",
    "bot_status_channel_id",
    "uptime_channel_id",
    "health_channel_id",
    "modlog_channel_id",
    "raidlog_channel_id",
    "join_log_channel_id",
    "force_verify_log_channel_id",
)

_ROLE_KEYS: tuple[str, ...] = (
    "unverified_role_id",
    "verified_role_id",
    "resident_role_id",
    "staff_role_id",
    "vc_staff_role_id",
    "server_control_role_id",
)

_REQUIRED_TICKET_KEYS: tuple[str, ...] = (
    "ticket_category_id",
    "staff_role_id",
)

_REQUIRED_VERIFY_KEYS: tuple[str, ...] = (
    "verify_channel_id",
    "unverified_role_id",
    "verified_role_id",
)

_REQUIRED_LOG_KEYS: tuple[str, ...] = (
    "modlog_channel_id",
)


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or isinstance(value, bool):
            return int(default)
        text = str(value).strip()
        return int(text) if text else int(default)
    except Exception:
        return int(default)


def _safe_str(value: Any, default: str = "") -> str:
    try:
        text = str(value or "").strip()
        return text if text else default
    except Exception:
        return default


def _cfg_get(cfg: Mapping[str, Any], key: str, default: Any = None) -> Any:
    try:
        value = cfg.get(key, default)
        return default if value is None else value
    except Exception:
        return default


def _snowflake(cfg: Mapping[str, Any], key: str) -> int:
    return _safe_int(_cfg_get(cfg, key, 0), 0)


def _missing_keys(cfg: Mapping[str, Any], keys: Iterable[str]) -> tuple[str, ...]:
    missing: list[str] = []
    for key in keys:
        if _snowflake(cfg, key) <= 0:
            missing.append(str(key))
    return tuple(missing)


@dataclass(frozen=True)
class GuildContext:
    """Typed, immutable snapshot of one guild's runtime config."""

    guild_id: int
    source: str
    config: GuildRuntimeConfig
    public_config_isolation: bool
    allow_runtime_discovery: bool
    use_env_fallbacks: bool
    is_unconfigured: bool
    missing_ticket_keys: tuple[str, ...] = field(default_factory=tuple)
    missing_verify_keys: tuple[str, ...] = field(default_factory=tuple)
    missing_log_keys: tuple[str, ...] = field(default_factory=tuple)

    @property
    def unsafe_to_act(self) -> bool:
        """Return true when runtime actions should refuse instead of guessing."""

        return bool(self.is_unconfigured)

    @property
    def ticket_ready(self) -> bool:
        return not self.missing_ticket_keys and not self.unsafe_to_act

    @property
    def verify_ready(self) -> bool:
        return not self.missing_verify_keys and not self.unsafe_to_act

    @property
    def logging_ready(self) -> bool:
        return not self.missing_log_keys and not self.unsafe_to_act

    def get_id(self, key: str, default: int = 0) -> int:
        return _snowflake(self.config, key) or int(default)

    def get_text(self, key: str, default: str = "") -> str:
        return _safe_str(_cfg_get(self.config, key, default), default)

    def channel_id(self, key: str, default: int = 0) -> int:
        if key not in _CHANNEL_KEYS:
            return int(default)
        return self.get_id(key, default)

    def role_id(self, key: str, default: int = 0) -> int:
        if key not in _ROLE_KEYS:
            return int(default)
        return self.get_id(key, default)

    def summary(self) -> dict[str, Any]:
        return {
            "guild_id": self.guild_id,
            "source": self.source,
            "public_config_isolation": self.public_config_isolation,
            "use_env_fallbacks": self.use_env_fallbacks,
            "allow_runtime_discovery": self.allow_runtime_discovery,
            "is_unconfigured": self.is_unconfigured,
            "unsafe_to_act": self.unsafe_to_act,
            "ticket_ready": self.ticket_ready,
            "verify_ready": self.verify_ready,
            "logging_ready": self.logging_ready,
            "missing_ticket_keys": list(self.missing_ticket_keys),
            "missing_verify_keys": list(self.missing_verify_keys),
            "missing_log_keys": list(self.missing_log_keys),
        }


def build_guild_context(guild_id: Any, cfg: Mapping[str, Any]) -> GuildContext:
    gid = _safe_int(_cfg_get(cfg, "guild_id", guild_id), _safe_int(guild_id, 0))
    source = _safe_str(_cfg_get(cfg, "source", "unknown"), "unknown")

    is_unconfigured = False
    try:
        if isinstance(cfg, GuildRuntimeConfig):
            is_unconfigured = bool(cfg.is_unconfigured)
        else:
            source_lower = source.lower()
            is_unconfigured = source_lower.startswith("unconfigured:") or source_lower.startswith("env_fallback")
    except Exception:
        is_unconfigured = True

    return GuildContext(
        guild_id=gid,
        source=source,
        config=GuildRuntimeConfig(dict(cfg)),
        public_config_isolation=public_config_isolation_enabled(),
        allow_runtime_discovery=bool(_cfg_get(cfg, "allow_runtime_discovery", True)),
        use_env_fallbacks=bool(_cfg_get(cfg, "use_env_fallbacks", False)),
        is_unconfigured=is_unconfigured,
        missing_ticket_keys=_missing_keys(cfg, _REQUIRED_TICKET_KEYS),
        missing_verify_keys=_missing_keys(cfg, _REQUIRED_VERIFY_KEYS),
        missing_log_keys=_missing_keys(cfg, _REQUIRED_LOG_KEYS),
    )


async def get_guild_context(guild_id: Any, *, refresh: bool = False) -> GuildContext:
    """Resolve the authoritative runtime context for one guild."""

    cfg = await get_guild_config(guild_id, refresh=refresh)
    return build_guild_context(guild_id, cfg)


__all__ = ["GuildContext", "build_guild_context", "get_guild_context"]
