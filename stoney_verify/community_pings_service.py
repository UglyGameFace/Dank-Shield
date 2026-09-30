from __future__ import annotations

"""Durable per-guild Community & Pings configuration.

The service owns parsing, validation, legacy compatibility, revisions, and
member-selection rules. Discord UI modules own presentation only.
"""

import asyncio
from dataclasses import dataclass, replace
import re
import weakref
from typing import Any, Iterable, Mapping, Optional, Sequence


COMMUNITY_PINGS_KEY = "community_pings_v2"
COMMUNITY_PINGS_VERSION = 2
MAX_COMMUNITY_OPTIONS = 25
MAX_COMMUNITY_GROUPS = 10

LEGACY_STONER_ROLE_KEY = "stoner_role_id"
LEGACY_SESH_PING_ROLE_KEY = "sesh_ping_role_id"
LEGACY_TOKE_CHANNEL_KEY = "toke_channel_id"

OPTION_KINDS = frozenset({"community", "notification"})
CAP_TOKE_START = "toke_start"
CAP_TOKE_NOTIFY = "toke_notify"

_COMMUNITY_MEMBER_LOCKS: weakref.WeakValueDictionary[str, asyncio.Lock] = weakref.WeakValueDictionary()


def community_member_lock(guild_id: int, member_id: int) -> asyncio.Lock:
    """Serialize all Community & Pings self-service mutations for one member."""
    key = f"{int(guild_id)}:{int(member_id)}"
    lock = _COMMUNITY_MEMBER_LOCKS.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _COMMUNITY_MEMBER_LOCKS[key] = lock
    return lock


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or isinstance(value, bool):
            return int(default)
        return int(str(value).strip())
    except Exception:
        return int(default)


def _safe_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "no", "n", "off"}:
        return False
    return default


def _text(value: Any, limit: int) -> str:
    clean = re.sub(r"\s+", " ", str(value or "").strip())
    return clean[: max(0, int(limit))]


def _slug(value: Any, *, fallback: str) -> str:
    clean = re.sub(r"[^a-z0-9]+", "-", str(value or "").lower().strip()).strip("-")
    return (clean or fallback)[:48]


def _emoji(value: Any, fallback: str = "🏷️") -> str:
    clean = str(value or "").strip()
    return clean[:32] if clean else fallback


@dataclass(frozen=True)
class CommunityPingGroup:
    key: str
    label: str
    emoji: str = "🏷️"
    max_selections: int = 0
    order: int = 0

    def to_payload(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "emoji": self.emoji,
            "max_selections": int(self.max_selections),
            "order": int(self.order),
        }


@dataclass(frozen=True)
class CommunityPingOption:
    key: str
    role_id: int
    label: str
    emoji: str = "🏷️"
    description: str = ""
    kind: str = "community"
    group_key: str = "community"
    prerequisite_role_id: int = 0
    exclusive_key: str = ""
    removable: bool = True
    enabled: bool = True
    order: int = 0
    capabilities: tuple[str, ...] = ()

    def to_payload(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "role_id": str(int(self.role_id)),
            "label": self.label,
            "emoji": self.emoji,
            "description": self.description,
            "kind": self.kind,
            "group_key": self.group_key,
            "prerequisite_role_id": (
                str(int(self.prerequisite_role_id))
                if int(self.prerequisite_role_id) > 0
                else ""
            ),
            "exclusive_key": self.exclusive_key,
            "removable": bool(self.removable),
            "enabled": bool(self.enabled),
            "order": int(self.order),
            "capabilities": list(self.capabilities),
        }


@dataclass(frozen=True)
class CommunityPingsConfig:
    revision: int
    groups: tuple[CommunityPingGroup, ...]
    options: tuple[CommunityPingOption, ...]
    source: str = "v2"

    def to_payload(self) -> dict[str, Any]:
        return {
            "version": COMMUNITY_PINGS_VERSION,
            "revision": max(1, int(self.revision)),
            "groups": [group.to_payload() for group in self.groups],
            "options": [option.to_payload() for option in self.options],
        }


def _group_from_raw(raw: Mapping[str, Any], index: int) -> Optional[CommunityPingGroup]:
    key = _slug(raw.get("key") or raw.get("label"), fallback=f"group-{index + 1}")
    label = _text(raw.get("label") or key.replace("-", " ").title(), 80)
    if not label:
        return None
    max_selections = max(0, min(_safe_int(raw.get("max_selections"), 0), MAX_COMMUNITY_OPTIONS))
    return CommunityPingGroup(
        key=key,
        label=label,
        emoji=_emoji(raw.get("emoji"), "🏷️"),
        max_selections=max_selections,
        order=_safe_int(raw.get("order"), index),
    )


def _option_from_raw(raw: Mapping[str, Any], index: int) -> Optional[CommunityPingOption]:
    role_id = _safe_int(raw.get("role_id"), 0)
    if role_id <= 0:
        return None

    label = _text(raw.get("label") or f"Role {role_id}", 100)
    kind = str(raw.get("kind") or "community").strip().lower()
    if kind not in OPTION_KINDS:
        kind = "community"

    raw_caps = raw.get("capabilities")
    if isinstance(raw_caps, str):
        cap_values = [raw_caps]
    elif isinstance(raw_caps, Sequence):
        cap_values = list(raw_caps)
    else:
        cap_values = []
    caps = tuple(
        dict.fromkeys(
            _slug(item, fallback="")
            for item in cap_values
            if _slug(item, fallback="")
        )
    )

    return CommunityPingOption(
        key=_slug(raw.get("key") or label, fallback=f"role-{role_id}"),
        role_id=role_id,
        label=label,
        emoji=_emoji(raw.get("emoji"), "💬" if kind == "notification" else "🌿"),
        description=_text(raw.get("description"), 100),
        kind=kind,
        group_key=_slug(raw.get("group_key"), fallback="community"),
        prerequisite_role_id=_safe_int(raw.get("prerequisite_role_id"), 0),
        exclusive_key=_slug(raw.get("exclusive_key"), fallback=""),
        removable=_safe_bool(raw.get("removable"), True),
        enabled=_safe_bool(raw.get("enabled"), True),
        order=_safe_int(raw.get("order"), index),
        capabilities=caps,
    )


def _dedupe_groups(groups: Iterable[CommunityPingGroup]) -> tuple[CommunityPingGroup, ...]:
    out: list[CommunityPingGroup] = []
    seen: set[str] = set()
    for group in sorted(groups, key=lambda item: (item.order, item.label.casefold(), item.key)):
        if group.key in seen:
            continue
        seen.add(group.key)
        out.append(group)
        if len(out) >= MAX_COMMUNITY_GROUPS:
            break
    return tuple(out)


def _dedupe_options(options: Iterable[CommunityPingOption]) -> tuple[CommunityPingOption, ...]:
    out: list[CommunityPingOption] = []
    seen_keys: set[str] = set()
    seen_roles: set[int] = set()
    for option in sorted(options, key=lambda item: (item.order, item.label.casefold(), item.key)):
        if option.key in seen_keys or int(option.role_id) in seen_roles:
            continue
        seen_keys.add(option.key)
        seen_roles.add(int(option.role_id))
        out.append(option)
        if len(out) >= MAX_COMMUNITY_OPTIONS:
            break
    return tuple(out)


def _legacy_config(config: Mapping[str, Any]) -> CommunityPingsConfig:
    stoner_id = _safe_int(config.get(LEGACY_STONER_ROLE_KEY), 0)
    ping_id = _safe_int(config.get(LEGACY_SESH_PING_ROLE_KEY), 0)

    if stoner_id <= 0 and ping_id <= 0:
        return CommunityPingsConfig(revision=1, groups=(), options=(), source="empty")

    group = CommunityPingGroup(
        key="toke-community",
        label="Toke Community",
        emoji="🌿",
        max_selections=0,
        order=0,
    )

    options: list[CommunityPingOption] = []
    if stoner_id > 0 and ping_id > 0 and stoner_id == ping_id:
        options.append(
            CommunityPingOption(
                key=f"legacy-toke-{stoner_id}",
                role_id=stoner_id,
                label="Stoner",
                emoji="🌿",
                description="Community role and /toke notifications",
                kind="community",
                group_key=group.key,
                removable=True,
                enabled=True,
                order=0,
                capabilities=(CAP_TOKE_START, CAP_TOKE_NOTIFY),
            )
        )
    else:
        if stoner_id > 0:
            options.append(
                CommunityPingOption(
                    key=f"legacy-stoner-{stoner_id}",
                    role_id=stoner_id,
                    label="Stoner",
                    emoji="🌿",
                    description="Community role; may start /toke",
                    kind="community",
                    group_key=group.key,
                    removable=True,
                    enabled=True,
                    order=0,
                    capabilities=(CAP_TOKE_START,),
                )
            )
        if ping_id > 0:
            options.append(
                CommunityPingOption(
                    key=f"legacy-sesh-{ping_id}",
                    role_id=ping_id,
                    label="Sesh Pings",
                    emoji="💨",
                    description="Opt in to /toke notifications",
                    kind="notification",
                    group_key=group.key,
                    prerequisite_role_id=stoner_id if stoner_id != ping_id else 0,
                    removable=True,
                    enabled=True,
                    order=1,
                    capabilities=(CAP_TOKE_NOTIFY,),
                )
            )

    return CommunityPingsConfig(
        revision=1,
        groups=(group,),
        options=_dedupe_options(options),
        source="legacy",
    )


def parse_community_pings(config: Mapping[str, Any]) -> CommunityPingsConfig:
    if COMMUNITY_PINGS_KEY not in config:
        return _legacy_config(config)

    raw = config.get(COMMUNITY_PINGS_KEY)
    if not isinstance(raw, Mapping):
        # Presence establishes v2 authority. Corrupt v2 must fail closed instead
        # of silently reactivating legacy Stoner/Sesh capabilities.
        return CommunityPingsConfig(revision=1, groups=(), options=(), source="v2")

    groups_raw = raw.get("groups")
    groups = _dedupe_groups(
        group
        for index, item in enumerate(groups_raw if isinstance(groups_raw, Sequence) and not isinstance(groups_raw, (str, bytes)) else [])
        if isinstance(item, Mapping)
        for group in [_group_from_raw(item, index)]
        if group is not None
    )

    options_raw = raw.get("options")
    options = _dedupe_options(
        option
        for index, item in enumerate(options_raw if isinstance(options_raw, Sequence) and not isinstance(options_raw, (str, bytes)) else [])
        if isinstance(item, Mapping)
        for option in [_option_from_raw(item, index)]
        if option is not None
    )

    known_groups = {group.key for group in groups}
    missing_group_keys = [option.group_key for option in options if option.group_key not in known_groups]
    if missing_group_keys:
        generated: list[CommunityPingGroup] = list(groups)
        for key in dict.fromkeys(missing_group_keys):
            if len(generated) >= MAX_COMMUNITY_GROUPS:
                break
            generated.append(
                CommunityPingGroup(
                    key=key,
                    label=key.replace("-", " ").title(),
                    order=len(generated),
                )
            )
        groups = _dedupe_groups(generated)

    return CommunityPingsConfig(
        revision=max(1, _safe_int(raw.get("revision"), 1)),
        groups=groups,
        options=options,
        source="v2",
    )


def next_revision(config: CommunityPingsConfig) -> int:
    return max(1, int(config.revision) + 1)


def with_option(
    config: CommunityPingsConfig,
    option: CommunityPingOption,
) -> CommunityPingsConfig:
    key_match = next((item for item in config.options if item.key == option.key), None)
    role_match = next(
        (item for item in config.options if int(item.role_id) == int(option.role_id)),
        None,
    )
    if key_match is not None and int(key_match.role_id) != int(option.role_id):
        raise ValueError(
            f"Another Community & Pings option already uses the key {option.key!r}."
        )
    if role_match is not None and role_match.key != option.key:
        raise ValueError(
            "That Discord role is already mapped to another Community & Pings option."
        )

    replacing = key_match is not None or role_match is not None
    if not replacing and len(config.options) >= MAX_COMMUNITY_OPTIONS:
        raise ValueError(f"Community & Pings supports at most {MAX_COMMUNITY_OPTIONS} options.")

    group_keys = {group.key for group in config.groups}
    existing = key_match or role_match
    if existing == option and option.group_key in group_keys:
        return config

    options = [item for item in config.options if item.key != option.key and item.role_id != option.role_id]
    options.append(option)
    normalized = _dedupe_options(options)

    groups = list(config.groups)
    if option.group_key not in group_keys:
        if len(groups) >= MAX_COMMUNITY_GROUPS:
            raise ValueError(f"Community & Pings supports at most {MAX_COMMUNITY_GROUPS} groups.")
        groups.append(
            CommunityPingGroup(
                key=option.group_key,
                label=option.group_key.replace("-", " ").title(),
                order=len(groups),
            )
        )

    return CommunityPingsConfig(
        revision=next_revision(config),
        groups=_dedupe_groups(groups),
        options=normalized,
        source="v2",
    )


def without_option(config: CommunityPingsConfig, option_key: str) -> CommunityPingsConfig:
    wanted = str(option_key or "").strip()
    options = tuple(item for item in config.options if item.key != wanted)
    if len(options) == len(config.options):
        return config
    return CommunityPingsConfig(
        revision=next_revision(config),
        groups=config.groups,
        options=options,
        source="v2",
    )


def move_option(config: CommunityPingsConfig, option_key: str, delta: int) -> CommunityPingsConfig:
    ordered = list(config.options)
    index = next((i for i, item in enumerate(ordered) if item.key == option_key), -1)
    if index < 0:
        return config
    target = max(0, min(len(ordered) - 1, index + int(delta)))
    if target == index:
        return config
    item = ordered.pop(index)
    ordered.insert(target, item)
    ordered = [replace(option, order=i) for i, option in enumerate(ordered)]
    return CommunityPingsConfig(
        revision=next_revision(config),
        groups=config.groups,
        options=tuple(ordered),
        source="v2",
    )


def upsert_group(
    config: CommunityPingsConfig,
    group: CommunityPingGroup,
) -> CommunityPingsConfig:
    existing = next((item for item in config.groups if item.key == group.key), None)
    replacing = existing is not None
    if not replacing and len(config.groups) >= MAX_COMMUNITY_GROUPS:
        raise ValueError(f"Community & Pings supports at most {MAX_COMMUNITY_GROUPS} groups.")
    if existing == group:
        return config
    groups = [item for item in config.groups if item.key != group.key]
    groups.append(group)
    normalized = _dedupe_groups(groups)
    return CommunityPingsConfig(
        revision=next_revision(config),
        groups=normalized,
        options=config.options,
        source="v2",
    )


def without_group(config: CommunityPingsConfig, group_key: str) -> CommunityPingsConfig:
    wanted = str(group_key or "").strip()
    if not wanted:
        return config
    if any(option.group_key == wanted for option in config.options):
        raise ValueError(
            "Move or delete the options in this group before deleting the group."
        )
    groups = tuple(item for item in config.groups if item.key != wanted)
    if len(groups) == len(config.groups):
        return config
    groups = tuple(replace(item, order=index) for index, item in enumerate(groups))
    return CommunityPingsConfig(
        revision=next_revision(config),
        groups=groups,
        options=config.options,
        source="v2",
    )


def option_for_role(config: CommunityPingsConfig, role_id: int) -> Optional[CommunityPingOption]:
    rid = int(role_id or 0)
    return next((item for item in config.options if int(item.role_id) == rid), None)


def enabled_options(config: CommunityPingsConfig) -> tuple[CommunityPingOption, ...]:
    return tuple(item for item in config.options if item.enabled)


def role_dependency_labels(config: CommunityPingsConfig, role_id: int) -> list[str]:
    rid = int(role_id or 0)
    labels: list[str] = []
    for option in config.options:
        if int(option.role_id) == rid:
            labels.append(f"Community & Pings · {option.label}")
        if int(option.prerequisite_role_id) == rid:
            labels.append(f"Community & Pings prerequisite · {option.label}")
    return list(dict.fromkeys(labels))


def self_service_kind(config: CommunityPingsConfig, role_id: int) -> str:
    option = option_for_role(config, role_id)
    if option is None or not option.enabled:
        return ""
    return "Notification" if option.kind == "notification" else "Community"


def toke_role_ids(
    config: CommunityPingsConfig,
    legacy_config: Optional[Mapping[str, Any]] = None,
) -> tuple[int, int]:
    starter = next(
        (int(item.role_id) for item in config.options if item.enabled and CAP_TOKE_START in item.capabilities),
        0,
    )
    notify = next(
        (int(item.role_id) for item in config.options if item.enabled and CAP_TOKE_NOTIFY in item.capabilities),
        0,
    )
    if config.source == "v2":
        return starter, notify

    legacy = legacy_config or {}
    if starter <= 0:
        starter = _safe_int(legacy.get(LEGACY_STONER_ROLE_KEY), 0)
    if notify <= 0:
        notify = _safe_int(legacy.get(LEGACY_SESH_PING_ROLE_KEY), 0)
    return starter, notify


def validate_member_selection(
    config: CommunityPingsConfig,
    *,
    selected_role_ids: Iterable[int],
    current_role_ids: Iterable[int],
) -> str:
    selected = {int(value) for value in selected_role_ids if int(value) > 0}
    current = {int(value) for value in current_role_ids if int(value) > 0}
    active = {int(item.role_id): item for item in enabled_options(config)}

    selected &= set(active)

    for role_id, option in active.items():
        if role_id in current and role_id not in selected and not option.removable:
            return f"{option.label} cannot be removed by members."

    active_role_ids = set(active)
    for role_id in selected:
        option = active[role_id]
        prerequisite = int(option.prerequisite_role_id)
        if prerequisite <= 0:
            continue
        if prerequisite in active_role_ids:
            if prerequisite not in selected:
                return f"{option.label} requires its configured prerequisite option."
        elif prerequisite not in current:
            return f"{option.label} requires its configured prerequisite role first."

    exclusive: dict[str, list[CommunityPingOption]] = {}
    for role_id in selected:
        option = active[role_id]
        if option.exclusive_key:
            exclusive.setdefault(option.exclusive_key, []).append(option)
    for items in exclusive.values():
        if len(items) > 1:
            labels = ", ".join(item.label for item in items[:3])
            return f"Choose only one of these mutually exclusive options: {labels}."

    groups = {group.key: group for group in config.groups}
    group_counts: dict[str, int] = {}
    for role_id in selected:
        option = active[role_id]
        group_counts[option.group_key] = group_counts.get(option.group_key, 0) + 1
    for group_key, count in group_counts.items():
        group = groups.get(group_key)
        if group is not None and int(group.max_selections) > 0 and count > int(group.max_selections):
            return f"{group.label} allows at most {group.max_selections} selection(s)."

    return ""


def validate_config(config: CommunityPingsConfig) -> list[str]:
    errors: list[str] = []
    if len(config.groups) > MAX_COMMUNITY_GROUPS:
        errors.append("too many groups")
    if len(config.options) > MAX_COMMUNITY_OPTIONS:
        errors.append("too many options")

    group_keys = [item.key for item in config.groups]
    if len(group_keys) != len(set(group_keys)):
        errors.append("duplicate group key")

    option_keys = [item.key for item in config.options]
    role_ids = [int(item.role_id) for item in config.options]
    if len(option_keys) != len(set(option_keys)):
        errors.append("duplicate option key")
    if len(role_ids) != len(set(role_ids)):
        errors.append("duplicate option role")

    known_groups = set(group_keys)
    for option in config.options:
        if option.group_key not in known_groups:
            errors.append(f"{option.key}: unknown group {option.group_key}")
        if option.kind not in OPTION_KINDS:
            errors.append(f"{option.key}: invalid kind {option.kind}")
        if int(option.role_id) <= 0:
            errors.append(f"{option.key}: invalid role")
        if int(option.prerequisite_role_id) == int(option.role_id):
            errors.append(f"{option.key}: cannot require itself")

    return errors


__all__ = [
    "CAP_TOKE_NOTIFY",
    "CAP_TOKE_START",
    "COMMUNITY_PINGS_KEY",
    "COMMUNITY_PINGS_VERSION",
    "CommunityPingGroup",
    "CommunityPingOption",
    "CommunityPingsConfig",
    "LEGACY_SESH_PING_ROLE_KEY",
    "LEGACY_STONER_ROLE_KEY",
    "LEGACY_TOKE_CHANNEL_KEY",
    "MAX_COMMUNITY_GROUPS",
    "MAX_COMMUNITY_OPTIONS",
    "OPTION_KINDS",
    "community_member_lock",
    "enabled_options",
    "move_option",
    "next_revision",
    "option_for_role",
    "parse_community_pings",
    "role_dependency_labels",
    "self_service_kind",
    "toke_role_ids",
    "upsert_group",
    "without_group",
    "validate_config",
    "validate_member_selection",
    "with_option",
    "without_option",
]
