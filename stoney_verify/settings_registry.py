from __future__ import annotations

"""Canonical setting definitions and compatibility reads.

This module owns setting *meaning*: canonical names, defaults, types, aliases,
and feature ownership. It does not own database I/O or caches. Persistence
stays with guild_config, Spam Guard, and feature-native services.

The first registry slice covers Protection / Automod / Invite Shield settings
whose meanings were previously duplicated across several modules.
"""

import re
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from .spam_guard_defaults import SPAM_GUARD_DEFAULT_ENABLED


@dataclass(frozen=True, slots=True)
class SettingSpec:
    key: str
    kind: str
    default: Any
    owner: str
    persistence: str
    aliases: tuple[str, ...] = ()
    choices: tuple[str, ...] = ()
    aliases_before_canonical: bool = False
    aliases_preserve_presence: bool = False
    minimum: int | None = None
    maximum: int | None = None


AUTOMOD_ENABLED_KEY = "automod_enabled"
AUTOMOD_BLOCK_INVITES_KEY = "automod_block_invites"
AUTOMOD_BLOCK_LINKS_KEY = "automod_block_links"
AUTOMOD_LINK_POLICY_KEY = "automod_link_policy"
AUTOMOD_BAD_WORDS_KEY = "automod_bad_words"

INVITE_SHIELD_ENABLED_KEY = "invite_shield_enabled"
INVITE_HARD_BLOCK_ENABLED_KEY = "invite_hard_block_enabled"
BLOCK_INVITES_KEY = "block_invites"
BLOCK_EXTERNAL_INVITES_ONLY_KEY = "block_external_invites_only"
ALLOW_SERVER_INVITES_KEY = "allow_server_invites"

INVITE_TARGET_ALL_BOTS_KEY = "invite_hard_block_target_all_bots"
INVITE_TARGET_BOT_IDS_KEY = "invite_hard_block_target_bot_ids"
INVITE_TARGET_CHANNEL_IDS_KEY = "invite_hard_block_target_channel_ids"
INVITE_PROTECTED_POSTER_RULE_KEY = "invite_protected_poster_rule_enabled"

SPAM_GUARD_ENABLED_KEY = "spam_guard_enabled"
SPAM_GUARD_MODE_KEY = "spam_guard_mode"
SPAM_GUARD_APPLY_VERIFIED_KEY = "spam_guard_apply_to_verified_users"
SPAM_GUARD_WINDOW_SECONDS_KEY = "spam_guard_window_seconds"
SPAM_GUARD_MESSAGE_THRESHOLD_KEY = "spam_guard_message_threshold"
SPAM_GUARD_DUPLICATE_THRESHOLD_KEY = "spam_guard_duplicate_threshold"
SPAM_GUARD_INVITE_THRESHOLD_KEY = "spam_guard_invite_threshold"
SPAM_GUARD_MULTI_INVITE_IMMEDIATE_KEY = "spam_guard_multi_invite_immediate"
SPAM_GUARD_DELETE_HISTORY_KEY = "spam_guard_delete_history"
SPAM_GUARD_TIMEOUT_MINUTES_KEY = "spam_guard_timeout_minutes"
SPAM_GUARD_COOLDOWN_SECONDS_KEY = "spam_guard_cooldown_seconds"
SPAM_GUARD_QUARANTINE_ROLE_KEY = "spam_guard_quarantine_role_id"
SPAM_GUARD_EXEMPT_ROLE_IDS_KEY = "spam_guard_exempt_role_ids"
SPAM_GUARD_INVITE_ALLOWED_ROLE_IDS_KEY = "spam_guard_invite_allowed_role_ids"
SPAM_GUARD_ALLOWED_CHANNEL_IDS_KEY = "spam_guard_allowed_channel_ids"
SPAM_GUARD_EXEMPT_USER_IDS_KEY = "spam_guard_exempt_user_ids"
SPAM_GUARD_ALLOWED_INVITE_CODES_KEY = "spam_guard_allowed_invite_codes"


PROTECTION_SETTING_SPECS: dict[str, SettingSpec] = {
    AUTOMOD_ENABLED_KEY: SettingSpec(
        AUTOMOD_ENABLED_KEY,
        "bool",
        False,
        "automod",
        "guild_config",
    ),
    AUTOMOD_BLOCK_INVITES_KEY: SettingSpec(
        AUTOMOD_BLOCK_INVITES_KEY,
        "bool",
        False,
        "invite_policy",
        "guild_config",
        aliases=("spam_automod_block_invites",),
    ),
    AUTOMOD_BLOCK_LINKS_KEY: SettingSpec(
        AUTOMOD_BLOCK_LINKS_KEY,
        "bool",
        False,
        "automod",
        "guild_config",
        aliases=("spam_automod_block_links",),
    ),
    AUTOMOD_LINK_POLICY_KEY: SettingSpec(
        AUTOMOD_LINK_POLICY_KEY,
        "choice",
        "allow_links",
        "automod",
        "guild_config",
        choices=("allow_links", "invite_shield", "link_lockdown"),
    ),
    AUTOMOD_BAD_WORDS_KEY: SettingSpec(
        AUTOMOD_BAD_WORDS_KEY,
        "string",
        "",
        "automod",
        "guild_config",
    ),
    INVITE_SHIELD_ENABLED_KEY: SettingSpec(
        INVITE_SHIELD_ENABLED_KEY,
        "bool",
        False,
        "invite_policy",
        "spam_guard",
        aliases=("spam_invite_shield_enabled",),
    ),
    INVITE_HARD_BLOCK_ENABLED_KEY: SettingSpec(
        INVITE_HARD_BLOCK_ENABLED_KEY,
        "bool",
        False,
        "invite_policy",
        "spam_guard",
        aliases=("spam_invite_hard_block_enabled",),
    ),
    BLOCK_INVITES_KEY: SettingSpec(
        BLOCK_INVITES_KEY,
        "bool",
        False,
        "invite_policy",
        "spam_guard",
        aliases=("spam_block_invites",),
    ),
    BLOCK_EXTERNAL_INVITES_ONLY_KEY: SettingSpec(
        BLOCK_EXTERNAL_INVITES_ONLY_KEY,
        "bool",
        True,
        "invite_policy",
        "spam_guard",
        aliases=("spam_block_external_invites_only",),
        aliases_before_canonical=True,
        aliases_preserve_presence=True,
    ),
    ALLOW_SERVER_INVITES_KEY: SettingSpec(
        ALLOW_SERVER_INVITES_KEY,
        "bool",
        True,
        "invite_policy",
        "spam_guard",
        aliases=("spam_allow_server_invites",),
        aliases_before_canonical=True,
        aliases_preserve_presence=True,
    ),
    INVITE_TARGET_ALL_BOTS_KEY: SettingSpec(
        INVITE_TARGET_ALL_BOTS_KEY,
        "bool",
        False,
        "invite_scope",
        "guild_config",
        aliases=(
            "spam_invite_hard_block_target_all_bots",
            "invite_target_all_bots",
        ),
    ),
    INVITE_TARGET_BOT_IDS_KEY: SettingSpec(
        INVITE_TARGET_BOT_IDS_KEY,
        "id_list",
        (),
        "invite_scope",
        "guild_config",
        aliases=(
            "spam_invite_hard_block_target_bot_ids",
            "invite_target_bot_ids",
        ),
    ),
    INVITE_TARGET_CHANNEL_IDS_KEY: SettingSpec(
        INVITE_TARGET_CHANNEL_IDS_KEY,
        "id_list",
        (),
        "invite_scope",
        "guild_config",
        aliases=(
            "spam_invite_hard_block_target_channel_ids",
            "invite_target_channel_ids",
        ),
    ),
    INVITE_PROTECTED_POSTER_RULE_KEY: SettingSpec(
        INVITE_PROTECTED_POSTER_RULE_KEY,
        "bool",
        False,
        "invite_scope",
        "guild_config",
        aliases=(
            "spam_invite_protected_poster_rule_enabled",
            "protected_poster_invite_rule_enabled",
            "invite_hard_block_protected_posters_enabled",
            "spam_invite_hard_block_protected_posters_enabled",
        ),
    ),
}

SPAM_GUARD_SETTING_SPECS: dict[str, SettingSpec] = {
    SPAM_GUARD_ENABLED_KEY: SettingSpec(
        SPAM_GUARD_ENABLED_KEY,
        "bool",
        SPAM_GUARD_DEFAULT_ENABLED,
        "spam_guard",
        "guild_security_settings",
        aliases=("spam_blocker_enabled", "enabled"),
        aliases_before_canonical=True,
        aliases_preserve_presence=True,
    ),
    SPAM_GUARD_MODE_KEY: SettingSpec(
        SPAM_GUARD_MODE_KEY,
        "choice",
        "timeout",
        "spam_guard",
        "guild_security_settings",
        aliases=("spam_mode", "mode"),
        choices=("log_only", "delete_only", "timeout", "quarantine", "kick", "ban"),
        aliases_before_canonical=True,
        aliases_preserve_presence=True,
    ),
    SPAM_GUARD_APPLY_VERIFIED_KEY: SettingSpec(
        SPAM_GUARD_APPLY_VERIFIED_KEY,
        "bool",
        True,
        "spam_guard",
        "guild_security_settings",
        aliases=("spam_apply_to_verified_users", "apply_to_verified_users"),
        aliases_before_canonical=True,
        aliases_preserve_presence=True,
    ),
    SPAM_GUARD_WINDOW_SECONDS_KEY: SettingSpec(
        SPAM_GUARD_WINDOW_SECONDS_KEY,
        "int",
        12,
        "spam_guard",
        "guild_security_settings",
        aliases=("spam_window_seconds", "window_seconds"),
        aliases_before_canonical=True,
        aliases_preserve_presence=True,
        minimum=5,
        maximum=60,
    ),
    SPAM_GUARD_MESSAGE_THRESHOLD_KEY: SettingSpec(
        SPAM_GUARD_MESSAGE_THRESHOLD_KEY,
        "int",
        5,
        "spam_guard",
        "guild_security_settings",
        aliases=("spam_message_threshold", "message_threshold"),
        aliases_before_canonical=True,
        aliases_preserve_presence=True,
        minimum=3,
        maximum=20,
    ),
    SPAM_GUARD_DUPLICATE_THRESHOLD_KEY: SettingSpec(
        SPAM_GUARD_DUPLICATE_THRESHOLD_KEY,
        "int",
        3,
        "spam_guard",
        "guild_security_settings",
        aliases=("spam_duplicate_threshold", "duplicate_threshold"),
        aliases_before_canonical=True,
        aliases_preserve_presence=True,
        minimum=2,
        maximum=12,
    ),
    SPAM_GUARD_INVITE_THRESHOLD_KEY: SettingSpec(
        SPAM_GUARD_INVITE_THRESHOLD_KEY,
        "int",
        2,
        "spam_guard",
        "guild_security_settings",
        aliases=("spam_invite_threshold", "invite_threshold"),
        aliases_before_canonical=True,
        aliases_preserve_presence=True,
        minimum=1,
        maximum=12,
    ),
    SPAM_GUARD_MULTI_INVITE_IMMEDIATE_KEY: SettingSpec(
        SPAM_GUARD_MULTI_INVITE_IMMEDIATE_KEY,
        "int",
        2,
        "spam_guard",
        "guild_security_settings",
        aliases=("spam_multi_invite_immediate", "multi_invite_immediate"),
        aliases_before_canonical=True,
        aliases_preserve_presence=True,
        minimum=2,
        maximum=8,
    ),
    SPAM_GUARD_DELETE_HISTORY_KEY: SettingSpec(
        SPAM_GUARD_DELETE_HISTORY_KEY,
        "int",
        8,
        "spam_guard",
        "guild_security_settings",
        aliases=("spam_delete_history", "delete_history"),
        aliases_before_canonical=True,
        aliases_preserve_presence=True,
        minimum=1,
        maximum=30,
    ),
    SPAM_GUARD_TIMEOUT_MINUTES_KEY: SettingSpec(
        SPAM_GUARD_TIMEOUT_MINUTES_KEY,
        "int",
        30,
        "spam_guard",
        "guild_security_settings",
        aliases=("spam_timeout_minutes", "timeout_minutes"),
        aliases_before_canonical=True,
        aliases_preserve_presence=True,
        minimum=1,
        maximum=1440,
    ),
    SPAM_GUARD_COOLDOWN_SECONDS_KEY: SettingSpec(
        SPAM_GUARD_COOLDOWN_SECONDS_KEY,
        "int",
        20,
        "spam_guard",
        "guild_security_settings",
        aliases=("spam_cooldown_seconds", "cooldown_seconds"),
        aliases_before_canonical=True,
        aliases_preserve_presence=True,
        minimum=5,
        maximum=300,
    ),
    SPAM_GUARD_QUARANTINE_ROLE_KEY: SettingSpec(
        SPAM_GUARD_QUARANTINE_ROLE_KEY,
        "string",
        "",
        "spam_guard",
        "guild_security_settings",
        aliases=("spam_quarantine_role_id", "quarantine_role_id"),
        aliases_before_canonical=True,
        aliases_preserve_presence=True,
    ),
    SPAM_GUARD_EXEMPT_ROLE_IDS_KEY: SettingSpec(
        SPAM_GUARD_EXEMPT_ROLE_IDS_KEY,
        "id_list",
        (),
        "spam_guard",
        "guild_security_settings",
        aliases=("spam_exempt_role_ids", "exempt_role_ids"),
        aliases_before_canonical=True,
        aliases_preserve_presence=True,
    ),
    SPAM_GUARD_INVITE_ALLOWED_ROLE_IDS_KEY: SettingSpec(
        SPAM_GUARD_INVITE_ALLOWED_ROLE_IDS_KEY,
        "id_list",
        (),
        "spam_guard",
        "guild_security_settings",
        aliases=("spam_invite_allowed_role_ids", "invite_allowed_role_ids"),
        aliases_before_canonical=True,
        aliases_preserve_presence=True,
    ),
    SPAM_GUARD_ALLOWED_CHANNEL_IDS_KEY: SettingSpec(
        SPAM_GUARD_ALLOWED_CHANNEL_IDS_KEY,
        "id_list",
        (),
        "spam_guard",
        "guild_security_settings",
        aliases=("spam_allowed_channel_ids", "allowed_channel_ids"),
        aliases_before_canonical=True,
        aliases_preserve_presence=True,
    ),
    SPAM_GUARD_EXEMPT_USER_IDS_KEY: SettingSpec(
        SPAM_GUARD_EXEMPT_USER_IDS_KEY,
        "id_list",
        (),
        "spam_guard",
        "guild_security_settings",
        aliases=("spam_exempt_user_ids", "exempt_user_ids"),
        aliases_before_canonical=True,
        aliases_preserve_presence=True,
    ),
    SPAM_GUARD_ALLOWED_INVITE_CODES_KEY: SettingSpec(
        SPAM_GUARD_ALLOWED_INVITE_CODES_KEY,
        "code_list",
        (),
        "spam_guard",
        "guild_security_settings",
        aliases=("spam_allowed_invite_codes", "allowed_invite_codes"),
        aliases_before_canonical=True,
        aliases_preserve_presence=True,
    ),
}

SETTING_SPECS: dict[str, SettingSpec] = {
    **PROTECTION_SETTING_SPECS,
    **SPAM_GUARD_SETTING_SPECS,
}

SPAM_GUARD_PRESETS: dict[str, dict[str, Any]] = {
    "off": {"enabled": False},
    "safe": {
        "enabled": True,
        "mode": "timeout",
        "apply_to_verified_users": True,
        "block_external_invites_only": True,
        "allow_server_invites": True,
        "window_seconds": 12,
        "message_threshold": 5,
        "duplicate_threshold": 3,
        "invite_threshold": 2,
        "multi_invite_immediate": 2,
        "delete_history": 8,
        "timeout_minutes": 30,
        "cooldown_seconds": 20,
    },
    "strict": {
        "enabled": True,
        "mode": "timeout",
        "apply_to_verified_users": True,
        "block_external_invites_only": True,
        "allow_server_invites": True,
        "window_seconds": 10,
        "message_threshold": 4,
        "duplicate_threshold": 2,
        "invite_threshold": 1,
        "multi_invite_immediate": 2,
        "delete_history": 12,
        "timeout_minutes": 60,
        "cooldown_seconds": 30,
    },
}

_NESTED_BUCKETS = ("settings", "config", "metadata", "meta")
_MISSING = object()


def setting_spec(key: str) -> SettingSpec:
    try:
        return SETTING_SPECS[str(key)]
    except KeyError as exc:
        raise KeyError(f"Unknown registered setting: {key}") from exc


def _direct_value(source: Any, key: str, *, include_none: bool = False) -> Any:
    if source is None:
        return _MISSING

    try:
        if isinstance(source, Mapping) and key in source:
            value = source.get(key)
            if include_none or value is not None:
                return value
    except Exception:
        pass

    try:
        value = getattr(source, key, _MISSING)
        if value is not _MISSING and (include_none or value is not None):
            return value
    except Exception:
        pass

    try:
        getter = getattr(source, "get", None)
        if callable(getter):
            value = getter(key, _MISSING)
            if value is not _MISSING and (include_none or value is not None):
                return value
    except Exception:
        pass

    for bucket in _NESTED_BUCKETS:
        nested = _MISSING
        try:
            if isinstance(source, Mapping):
                nested = source.get(bucket, _MISSING)
            else:
                nested = getattr(source, bucket, _MISSING)
                if nested is _MISSING:
                    getter = getattr(source, "get", None)
                    if callable(getter):
                        nested = getter(bucket, _MISSING)
        except Exception:
            nested = _MISSING

        if isinstance(nested, Mapping) and key in nested:
            value = nested.get(key)
            if include_none or value is not None:
                return value

    return _MISSING


def raw_setting(source: Any, key: str, default: Any = _MISSING) -> Any:
    spec = setting_spec(key)
    candidates = (
        (*spec.aliases, spec.key)
        if spec.aliases_before_canonical
        else (spec.key, *spec.aliases)
    )
    for candidate in candidates:
        value = _direct_value(
            source,
            candidate,
            include_none=bool(spec.aliases_preserve_presence),
        )
        if value is not _MISSING:
            return value

    if default is not _MISSING:
        return default
    return spec.default


def coerce_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return bool(default)
    try:
        text = str(value).strip().lower()
    except Exception:
        return bool(default)

    if text in {"1", "true", "yes", "y", "on", "enabled", "enable", "allow", "allowed", "all"}:
        return True
    if text in {"0", "false", "no", "n", "off", "disabled", "disable", "block", "blocked", "none"}:
        return False
    return bool(default)


def coerce_ids(value: Any, *, limit: int = 100) -> list[str]:
    if isinstance(value, (list, tuple, set)):
        items: Iterable[Any] = value
    else:
        items = re.split(r"[\s,;]+", str(value or ""))

    out: list[str] = []
    for raw in items:
        text = str(raw or "").strip().strip("<@#!&>")
        if text.isdigit() and text not in out:
            out.append(text)
        if len(out) >= max(1, int(limit)):
            break
    return out


def coerce_int(
    value: Any,
    default: int,
    *,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int:
    try:
        if value is None or isinstance(value, bool):
            parsed = int(default)
        else:
            parsed = int(str(value).strip())
    except Exception:
        parsed = int(default)

    if minimum is not None:
        parsed = max(int(minimum), parsed)
    if maximum is not None:
        parsed = min(int(maximum), parsed)
    return parsed


def coerce_codes(value: Any, *, limit: int = 100) -> list[str]:
    stored_collection = isinstance(value, (list, tuple, set))
    if stored_collection:
        items: Iterable[Any] = value
    else:
        items = re.split(r"[\s,;]+", str(value or ""))

    out: list[str] = []
    for raw in items:
        code = str(raw or "").strip().lower()
        if not stored_collection:
            code = code.strip("/")
            for prefix in (
                "https://discord.gg/",
                "http://discord.gg/",
                "https://discord.com/invite/",
                "http://discord.com/invite/",
                "https://discordapp.com/invite/",
                "http://discordapp.com/invite/",
            ):
                if code.startswith(prefix):
                    code = code[len(prefix):]
                    break
            code = code.strip().strip("/")

        if code and code not in out:
            out.append(code)
        if len(out) >= max(1, int(limit)):
            break
    return out


def setting_value(source: Any, key: str, default: Any = _MISSING) -> Any:
    spec = setting_spec(key)
    fallback = spec.default if default is _MISSING else default
    raw = raw_setting(source, key, fallback)

    if spec.kind == "bool":
        return coerce_bool(raw, bool(fallback))
    if spec.kind == "id_list":
        return coerce_ids(raw)
    if spec.kind == "int":
        return coerce_int(
            raw,
            int(fallback),
            minimum=spec.minimum,
            maximum=spec.maximum,
        )
    if spec.kind == "code_list":
        return coerce_codes(raw)
    if spec.kind == "choice":
        text = str(raw or "").strip().lower()
        if text in spec.choices:
            return text
        fallback_text = str(fallback or spec.default).strip().lower()
        return fallback_text if fallback_text in spec.choices else str(spec.default)
    if spec.kind == "string":
        try:
            return str(raw or "")
        except Exception:
            return str(fallback or "")
    return raw


def setting_bool(source: Any, key: str, default: bool | None = None) -> bool:
    spec = setting_spec(key)
    fallback = bool(spec.default) if default is None else bool(default)
    return coerce_bool(raw_setting(source, key, fallback), fallback)


def setting_ids(source: Any, key: str, *, limit: int = 100) -> list[str]:
    return coerce_ids(raw_setting(source, key, ()), limit=limit)


def invite_shield_enabled(cfg: Any, spam: Mapping[str, Any] | None = None) -> bool:
    """Return the legacy-compatible effective Invite Shield state."""

    spam_source: Mapping[str, Any] = spam or {}
    return bool(
        setting_bool(cfg, AUTOMOD_BLOCK_INVITES_KEY, False)
        or setting_bool(spam_source, INVITE_SHIELD_ENABLED_KEY, False)
        or setting_bool(spam_source, INVITE_HARD_BLOCK_ENABLED_KEY, False)
        or setting_bool(spam_source, AUTOMOD_BLOCK_INVITES_KEY, False)
        or setting_bool(spam_source, BLOCK_INVITES_KEY, False)
    )


def link_shield_enabled(cfg: Any, spam: Mapping[str, Any] | None = None) -> bool:
    spam_source: Mapping[str, Any] = spam or {}
    return bool(
        setting_bool(cfg, AUTOMOD_BLOCK_LINKS_KEY, False)
        or setting_bool(spam_source, AUTOMOD_BLOCK_LINKS_KEY, False)
    )


def invite_scope_values(source: Any) -> dict[str, Any]:
    return {
        INVITE_TARGET_ALL_BOTS_KEY: setting_bool(source, INVITE_TARGET_ALL_BOTS_KEY, False),
        INVITE_TARGET_BOT_IDS_KEY: setting_ids(source, INVITE_TARGET_BOT_IDS_KEY),
        INVITE_TARGET_CHANNEL_IDS_KEY: setting_ids(source, INVITE_TARGET_CHANNEL_IDS_KEY),
        INVITE_PROTECTED_POSTER_RULE_KEY: setting_bool(source, INVITE_PROTECTED_POSTER_RULE_KEY, False),
    }


def spam_guard_defaults(guild_id: int) -> dict[str, Any]:
    return {
        "guild_id": str(int(guild_id)),
        "enabled": bool(SPAM_GUARD_DEFAULT_ENABLED),
        "mode": "timeout",
        "apply_to_verified_users": True,
        "block_external_invites_only": True,
        "allow_server_invites": True,
        "window_seconds": 12,
        "message_threshold": 5,
        "duplicate_threshold": 3,
        "invite_threshold": 2,
        "multi_invite_immediate": 2,
        "delete_history": 8,
        "timeout_minutes": 30,
        "cooldown_seconds": 20,
        "quarantine_role_id": "",
        "exempt_role_ids": [],
        "invite_allowed_role_ids": [],
        "allowed_channel_ids": [],
        "exempt_user_ids": [],
        "allowed_invite_codes": [],
    }


def normalize_spam_guard_settings(
    guild_id: int,
    source: Mapping[str, Any] | None,
) -> dict[str, Any]:
    base = spam_guard_defaults(guild_id)
    if not isinstance(source, Mapping):
        return base

    base["enabled"] = setting_bool(source, SPAM_GUARD_ENABLED_KEY, base["enabled"])
    base["mode"] = setting_value(source, SPAM_GUARD_MODE_KEY, base["mode"])
    base["apply_to_verified_users"] = setting_bool(
        source,
        SPAM_GUARD_APPLY_VERIFIED_KEY,
        base["apply_to_verified_users"],
    )
    base["block_external_invites_only"] = setting_bool(
        source,
        BLOCK_EXTERNAL_INVITES_ONLY_KEY,
        base["block_external_invites_only"],
    )
    base["allow_server_invites"] = setting_bool(
        source,
        ALLOW_SERVER_INVITES_KEY,
        base["allow_server_invites"],
    )
    base["window_seconds"] = setting_value(source, SPAM_GUARD_WINDOW_SECONDS_KEY)
    base["message_threshold"] = setting_value(source, SPAM_GUARD_MESSAGE_THRESHOLD_KEY)
    base["duplicate_threshold"] = setting_value(source, SPAM_GUARD_DUPLICATE_THRESHOLD_KEY)
    base["invite_threshold"] = setting_value(source, SPAM_GUARD_INVITE_THRESHOLD_KEY)
    base["multi_invite_immediate"] = setting_value(source, SPAM_GUARD_MULTI_INVITE_IMMEDIATE_KEY)
    base["delete_history"] = setting_value(source, SPAM_GUARD_DELETE_HISTORY_KEY)
    base["timeout_minutes"] = setting_value(source, SPAM_GUARD_TIMEOUT_MINUTES_KEY)
    base["cooldown_seconds"] = setting_value(source, SPAM_GUARD_COOLDOWN_SECONDS_KEY)
    base["quarantine_role_id"] = str(setting_value(source, SPAM_GUARD_QUARANTINE_ROLE_KEY) or "").strip()
    base["exempt_role_ids"] = setting_ids(source, SPAM_GUARD_EXEMPT_ROLE_IDS_KEY)
    base["invite_allowed_role_ids"] = setting_ids(source, SPAM_GUARD_INVITE_ALLOWED_ROLE_IDS_KEY)
    base["allowed_channel_ids"] = setting_ids(source, SPAM_GUARD_ALLOWED_CHANNEL_IDS_KEY)
    base["exempt_user_ids"] = setting_ids(source, SPAM_GUARD_EXEMPT_USER_IDS_KEY)
    base["allowed_invite_codes"] = coerce_codes(
        raw_setting(source, SPAM_GUARD_ALLOWED_INVITE_CODES_KEY, ())
    )
    return base


def spam_guard_preset(name: str) -> dict[str, Any]:
    key = str(name or "safe").strip().lower()
    if key not in SPAM_GUARD_PRESETS:
        key = "safe"
    return dict(SPAM_GUARD_PRESETS[key])


def protection_registry_snapshot() -> dict[str, dict[str, Any]]:
    return {
        key: {
            "kind": spec.kind,
            "default": list(spec.default) if isinstance(spec.default, tuple) else spec.default,
            "owner": spec.owner,
            "persistence": spec.persistence,
            "aliases": list(spec.aliases),
            "choices": list(spec.choices),
            "aliases_before_canonical": bool(spec.aliases_before_canonical),
            "aliases_preserve_presence": bool(spec.aliases_preserve_presence),
            "minimum": spec.minimum,
            "maximum": spec.maximum,
        }
        for key, spec in sorted(PROTECTION_SETTING_SPECS.items())
    }


__all__ = [
    "SETTING_SPECS",
    "SPAM_GUARD_SETTING_SPECS",
    "SPAM_GUARD_PRESETS",
    "SPAM_GUARD_ENABLED_KEY",
    "SPAM_GUARD_MODE_KEY",
    "SPAM_GUARD_APPLY_VERIFIED_KEY",
    "SPAM_GUARD_WINDOW_SECONDS_KEY",
    "SPAM_GUARD_MESSAGE_THRESHOLD_KEY",
    "SPAM_GUARD_DUPLICATE_THRESHOLD_KEY",
    "SPAM_GUARD_INVITE_THRESHOLD_KEY",
    "SPAM_GUARD_MULTI_INVITE_IMMEDIATE_KEY",
    "SPAM_GUARD_DELETE_HISTORY_KEY",
    "SPAM_GUARD_TIMEOUT_MINUTES_KEY",
    "SPAM_GUARD_COOLDOWN_SECONDS_KEY",
    "SPAM_GUARD_QUARANTINE_ROLE_KEY",
    "SPAM_GUARD_EXEMPT_ROLE_IDS_KEY",
    "SPAM_GUARD_INVITE_ALLOWED_ROLE_IDS_KEY",
    "SPAM_GUARD_ALLOWED_CHANNEL_IDS_KEY",
    "SPAM_GUARD_EXEMPT_USER_IDS_KEY",
    "SPAM_GUARD_ALLOWED_INVITE_CODES_KEY",
    "coerce_int",
    "coerce_codes",
    "spam_guard_defaults",
    "normalize_spam_guard_settings",
    "spam_guard_preset",
    "ALLOW_SERVER_INVITES_KEY",
    "AUTOMOD_BAD_WORDS_KEY",
    "AUTOMOD_BLOCK_INVITES_KEY",
    "AUTOMOD_BLOCK_LINKS_KEY",
    "AUTOMOD_ENABLED_KEY",
    "AUTOMOD_LINK_POLICY_KEY",
    "BLOCK_EXTERNAL_INVITES_ONLY_KEY",
    "BLOCK_INVITES_KEY",
    "INVITE_HARD_BLOCK_ENABLED_KEY",
    "INVITE_PROTECTED_POSTER_RULE_KEY",
    "INVITE_SHIELD_ENABLED_KEY",
    "INVITE_TARGET_ALL_BOTS_KEY",
    "INVITE_TARGET_BOT_IDS_KEY",
    "INVITE_TARGET_CHANNEL_IDS_KEY",
    "PROTECTION_SETTING_SPECS",
    "SettingSpec",
    "coerce_bool",
    "coerce_ids",
    "invite_scope_values",
    "invite_shield_enabled",
    "link_shield_enabled",
    "protection_registry_snapshot",
    "raw_setting",
    "setting_bool",
    "setting_ids",
    "setting_spec",
    "setting_value",
]
