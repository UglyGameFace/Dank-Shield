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


@dataclass(frozen=True, slots=True)
class SettingSpec:
    key: str
    kind: str
    default: Any
    owner: str
    persistence: str
    aliases: tuple[str, ...] = ()
    choices: tuple[str, ...] = ()


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
    ),
    ALLOW_SERVER_INVITES_KEY: SettingSpec(
        ALLOW_SERVER_INVITES_KEY,
        "bool",
        True,
        "invite_policy",
        "spam_guard",
        aliases=("spam_allow_server_invites",),
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

_NESTED_BUCKETS = ("settings", "config", "metadata", "meta")
_MISSING = object()


def setting_spec(key: str) -> SettingSpec:
    try:
        return PROTECTION_SETTING_SPECS[str(key)]
    except KeyError as exc:
        raise KeyError(f"Unknown registered setting: {key}") from exc


def _direct_value(source: Any, key: str) -> Any:
    if source is None:
        return _MISSING

    try:
        if isinstance(source, Mapping) and key in source and source.get(key) is not None:
            return source.get(key)
    except Exception:
        pass

    try:
        value = getattr(source, key, _MISSING)
        if value is not _MISSING and value is not None:
            return value
    except Exception:
        pass

    try:
        getter = getattr(source, "get", None)
        if callable(getter):
            value = getter(key, _MISSING)
            if value is not _MISSING and value is not None:
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

        if isinstance(nested, Mapping) and key in nested and nested.get(key) is not None:
            return nested.get(key)

    return _MISSING


def raw_setting(source: Any, key: str, default: Any = _MISSING) -> Any:
    spec = setting_spec(key)
    for candidate in (spec.key, *spec.aliases):
        value = _direct_value(source, candidate)
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


def setting_value(source: Any, key: str, default: Any = _MISSING) -> Any:
    spec = setting_spec(key)
    fallback = spec.default if default is _MISSING else default
    raw = raw_setting(source, key, fallback)

    if spec.kind == "bool":
        return coerce_bool(raw, bool(fallback))
    if spec.kind == "id_list":
        return coerce_ids(raw)
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


def protection_registry_snapshot() -> dict[str, dict[str, Any]]:
    return {
        key: {
            "kind": spec.kind,
            "default": list(spec.default) if isinstance(spec.default, tuple) else spec.default,
            "owner": spec.owner,
            "persistence": spec.persistence,
            "aliases": list(spec.aliases),
            "choices": list(spec.choices),
        }
        for key, spec in sorted(PROTECTION_SETTING_SPECS.items())
    }


__all__ = [
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
