from __future__ import annotations

"""Durable, guild-scoped member setup revision state.

This module owns setup versioning only. Discord role/channel mutation stays in the
member-setup runtime so persistence can be tested without a Discord connection.
"""

from datetime import datetime, timezone
from typing import Any, Mapping, Optional

from .guild_config import get_guild_config, invalidate_guild_config, upsert_guild_config
from .profile_card_service import (
    get_profile_guild_settings,
    list_profile_guild_settings,
    upsert_profile_guild_namespace,
)


GUILD_SETUP_KEY = "member_setup_state"
MEMBER_SETUP_KEY = "member_setup_state"
SETUP_SCHEMA_VERSION = 1
MAX_REVISION_HISTORY = 50

SETUP_SECTIONS: tuple[str, ...] = (
    "community",
    "notifications",
    "profile",
    "interests",
)

SECTION_LABELS: dict[str, str] = {
    "community": "Community",
    "notifications": "Notifications",
    "profile": "Profile & Cosmetics",
    "interests": "Interests",
}

SEVERITY_MINOR = "minor"
SEVERITY_RECOMMENDED = "recommended"
SEVERITY_REQUIRED = "required"
SEVERITY_ACCESS_GATED = "access_gated"

SEVERITY_ORDER: tuple[str, ...] = (
    SEVERITY_MINOR,
    SEVERITY_RECOMMENDED,
    SEVERITY_REQUIRED,
    SEVERITY_ACCESS_GATED,
)
_SEVERITY_RANK = {name: index for index, name in enumerate(SEVERITY_ORDER)}

ACCESS_MODE_NORMAL = "normal"
ACCESS_MODE_STRICT = "strict"
ACCESS_MODES = frozenset({ACCESS_MODE_NORMAL, ACCESS_MODE_STRICT})


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or isinstance(value, bool):
            return int(default)
        return int(str(value).strip())
    except Exception:
        return int(default)


def _clean_text(value: Any, limit: int = 300) -> str:
    text = " ".join(str(value or "").replace("\r", " ").replace("\n", " ").split()).strip()
    return text[:limit]


def normalize_sections(values: Any) -> list[str]:
    if isinstance(values, str):
        values = values.replace(";", ",").split(",")
    if not isinstance(values, (list, tuple, set, frozenset)):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for raw in values:
        key = str(raw or "").strip().lower().replace(" ", "_").replace("-", "_")
        aliases = {
            "pings": "notifications",
            "notification": "notifications",
            "cosmetics": "profile",
            "profiles": "profile",
            "interest": "interests",
        }
        key = aliases.get(key, key)
        if key in SETUP_SECTIONS and key not in seen:
            seen.add(key)
            out.append(key)
    return out


def normalize_severity(value: Any, default: str = SEVERITY_REQUIRED) -> str:
    clean = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    return clean if clean in _SEVERITY_RANK else default


def max_severity(values: Any) -> str:
    best = SEVERITY_MINOR
    if not isinstance(values, (list, tuple, set, frozenset)):
        values = [values]
    for value in values:
        clean = normalize_severity(value, SEVERITY_MINOR)
        if _SEVERITY_RANK[clean] > _SEVERITY_RANK[best]:
            best = clean
    return best


def default_guild_setup_state() -> dict[str, Any]:
    return {
        "schema_version": SETUP_SCHEMA_VERSION,
        "enabled": False,
        "setup_channel_id": "",
        "access_mode": ACCESS_MODE_NORMAL,
        "access_role_id": "",
        "prerequisite_role_id": "",
        "protected_category_ids": [],
        "gate_active": False,
        "gate_snapshot": {},
        "current_revision": 0,
        "history": [],
    }


def normalize_guild_setup_state(value: Any) -> dict[str, Any]:
    raw = dict(value or {}) if isinstance(value, Mapping) else {}
    state = default_guild_setup_state()
    state["enabled"] = bool(raw.get("enabled", False))
    state["setup_channel_id"] = str(_safe_int(raw.get("setup_channel_id"), 0) or "")
    state["access_mode"] = (
        str(raw.get("access_mode") or ACCESS_MODE_NORMAL)
        if str(raw.get("access_mode") or ACCESS_MODE_NORMAL) in ACCESS_MODES
        else ACCESS_MODE_NORMAL
    )
    state["access_role_id"] = str(_safe_int(raw.get("access_role_id"), 0) or "")
    state["prerequisite_role_id"] = str(_safe_int(raw.get("prerequisite_role_id"), 0) or "")
    category_ids: list[str] = []
    raw_categories = raw.get("protected_category_ids")
    if isinstance(raw_categories, (list, tuple, set, frozenset)):
        seen_categories: set[int] = set()
        for item in raw_categories:
            cid = _safe_int(item, 0)
            if cid > 0 and cid not in seen_categories:
                seen_categories.add(cid)
                category_ids.append(str(cid))
    state["protected_category_ids"] = category_ids[:100]
    state["gate_active"] = bool(raw.get("gate_active", False))
    snapshot = raw.get("gate_snapshot")
    state["gate_snapshot"] = dict(snapshot) if isinstance(snapshot, Mapping) else {}
    state["current_revision"] = max(0, _safe_int(raw.get("current_revision"), 0))

    history: list[dict[str, Any]] = []
    source_history = raw.get("history")
    if isinstance(source_history, list):
        for item in source_history[-MAX_REVISION_HISTORY:]:
            if not isinstance(item, Mapping):
                continue
            revision = max(0, _safe_int(item.get("revision"), 0))
            if revision <= 0:
                continue
            history.append(
                {
                    "revision": revision,
                    "severity": normalize_severity(item.get("severity"), SEVERITY_REQUIRED),
                    "changed_sections": normalize_sections(item.get("changed_sections")),
                    "summary": _clean_text(item.get("summary"), 300),
                    "published_at": _clean_text(item.get("published_at"), 80),
                    "published_by_id": str(_safe_int(item.get("published_by_id"), 0) or ""),
                }
            )
    history.sort(key=lambda item: int(item["revision"]))
    state["history"] = history[-MAX_REVISION_HISTORY:]

    if history:
        state["current_revision"] = max(
            int(state["current_revision"]),
            max(int(item["revision"]) for item in history),
        )
    return state


async def load_guild_setup_state(guild_id: int, *, refresh: bool = False) -> dict[str, Any]:
    cfg = await get_guild_config(int(guild_id), refresh=refresh)
    return normalize_guild_setup_state(cfg.get(GUILD_SETUP_KEY))


async def save_guild_setup_state(
    guild_id: int,
    state: Mapping[str, Any],
    *,
    actor_id: int = 0,
    source: str = "member_setup",
) -> dict[str, Any]:
    gid = int(guild_id)
    normalized = normalize_guild_setup_state(state)
    await upsert_guild_config(
        gid,
        {
            GUILD_SETUP_KEY: normalized,
            "__config_write_mode": "explicit_override",
            "__config_write_source": str(source or "member_setup")[:300],
            "__config_write_actor_id": str(int(actor_id)) if int(actor_id) > 0 else "",
            "__config_write_allow_keys": [GUILD_SETUP_KEY],
        },
    )
    invalidate_guild_config(gid)
    return await load_guild_setup_state(gid, refresh=True)


async def configure_guild_setup(
    guild_id: int,
    *,
    enabled: Optional[bool] = None,
    setup_channel_id: Optional[int] = None,
    access_mode: Optional[str] = None,
    access_role_id: Optional[int] = None,
    prerequisite_role_id: Optional[int] = None,
    protected_category_ids: Optional[list[int]] = None,
    gate_active: Optional[bool] = None,
    gate_snapshot: Optional[Mapping[str, Any]] = None,
    actor_id: int = 0,
) -> dict[str, Any]:
    state = await load_guild_setup_state(int(guild_id), refresh=True)
    if enabled is not None:
        state["enabled"] = bool(enabled)
    if setup_channel_id is not None:
        state["setup_channel_id"] = str(max(0, int(setup_channel_id)) or "")
    if access_mode is not None:
        clean_mode = str(access_mode or "").strip().lower()
        if clean_mode not in ACCESS_MODES:
            raise ValueError("Access mode must be normal or strict.")
        state["access_mode"] = clean_mode
    if access_role_id is not None:
        state["access_role_id"] = str(max(0, int(access_role_id)) or "")
    if prerequisite_role_id is not None:
        state["prerequisite_role_id"] = str(max(0, int(prerequisite_role_id)) or "")
    if protected_category_ids is not None:
        state["protected_category_ids"] = [
            str(int(value))
            for value in protected_category_ids
            if int(value) > 0
        ][:100]
    if gate_active is not None:
        state["gate_active"] = bool(gate_active)
    if gate_snapshot is not None:
        state["gate_snapshot"] = dict(gate_snapshot)
    return await save_guild_setup_state(
        int(guild_id),
        state,
        actor_id=actor_id,
        source="member_setup_configuration",
    )


async def publish_revision(
    guild_id: int,
    *,
    severity: str,
    changed_sections: Any,
    summary: str,
    actor_id: int = 0,
) -> dict[str, Any]:
    gid = int(guild_id)
    state = await load_guild_setup_state(gid, refresh=True)
    clean_severity = normalize_severity(severity)
    sections = normalize_sections(changed_sections)
    if not sections and clean_severity != SEVERITY_MINOR:
        raise ValueError("Choose at least one member-setup section for this revision.")

    revision = int(state.get("current_revision") or 0) + 1
    entry = {
        "revision": revision,
        "severity": clean_severity,
        "changed_sections": sections,
        "summary": _clean_text(summary, 300) or f"Member setup revision {revision}",
        "published_at": utc_now_iso(),
        "published_by_id": str(int(actor_id)) if int(actor_id) > 0 else "",
    }
    history = list(state.get("history") or [])
    history.append(entry)
    state["history"] = history[-MAX_REVISION_HISTORY:]
    state["current_revision"] = revision
    state["enabled"] = True
    return await save_guild_setup_state(
        gid,
        state,
        actor_id=actor_id,
        source="member_setup_publish_revision",
    )


def default_member_setup_state() -> dict[str, Any]:
    return {
        "completed_revision": 0,
        "section_revisions": {},
        "completed_at": "",
        "last_reviewed_at": "",
    }


def normalize_member_setup_state(value: Any) -> dict[str, Any]:
    raw = dict(value or {}) if isinstance(value, Mapping) else {}
    state = default_member_setup_state()
    state["completed_revision"] = max(0, _safe_int(raw.get("completed_revision"), 0))
    section_revisions: dict[str, int] = {}
    raw_sections = raw.get("section_revisions")
    if isinstance(raw_sections, Mapping):
        for key, revision in raw_sections.items():
            clean = normalize_sections([key])
            if clean:
                section_revisions[clean[0]] = max(0, _safe_int(revision, 0))
    state["section_revisions"] = section_revisions
    state["completed_at"] = _clean_text(raw.get("completed_at"), 80)
    state["last_reviewed_at"] = _clean_text(raw.get("last_reviewed_at"), 80)
    return state


async def load_member_setup_state(
    guild_id: int,
    user_id: int,
    *,
    refresh: bool = False,
) -> dict[str, Any]:
    row = await get_profile_guild_settings(int(guild_id), int(user_id), refresh=refresh)
    settings = dict(row.get("settings") or {}) if isinstance(row.get("settings"), Mapping) else {}
    return normalize_member_setup_state(settings.get(MEMBER_SETUP_KEY))


async def save_member_setup_state(
    guild_id: int,
    user_id: int,
    state: Mapping[str, Any],
) -> dict[str, Any]:
    normalized = normalize_member_setup_state(state)
    await upsert_profile_guild_namespace(
        int(guild_id),
        int(user_id),
        MEMBER_SETUP_KEY,
        normalized,
    )
    return await load_member_setup_state(int(guild_id), int(user_id), refresh=True)


async def load_guild_member_setup_states(guild_id: int) -> dict[int, dict[str, Any]]:
    """Return one durable setup-state snapshot for every stored member in a guild."""
    rows = await list_profile_guild_settings(int(guild_id))
    out: dict[int, dict[str, Any]] = {}
    for row in rows:
        uid = _safe_int(row.get("user_id"), 0)
        if uid <= 0:
            continue
        settings = dict(row.get("settings") or {}) if isinstance(row.get("settings"), Mapping) else {}
        out[uid] = normalize_member_setup_state(settings.get(MEMBER_SETUP_KEY))
    return out


def _revisions_after(
    guild_state: Mapping[str, Any],
    completed_revision: int,
) -> list[dict[str, Any]]:
    state = normalize_guild_setup_state(guild_state)
    floor = max(0, int(completed_revision))
    return [
        dict(item)
        for item in state.get("history", [])
        if int(item.get("revision") or 0) > floor
    ]


def member_review_status(
    guild_state: Mapping[str, Any],
    member_state: Mapping[str, Any],
) -> dict[str, Any]:
    guild = normalize_guild_setup_state(guild_state)
    member = normalize_member_setup_state(member_state)
    current_revision = int(guild.get("current_revision") or 0)
    completed_revision = min(
        max(0, int(member.get("completed_revision") or 0)),
        current_revision,
    )
    section_revisions = dict(member.get("section_revisions") or {})

    pending_latest: dict[str, int] = {}
    severities: list[str] = []
    relevant = _revisions_after(guild, completed_revision)
    for revision in relevant:
        revision_number = int(revision.get("revision") or 0)
        severity = normalize_severity(revision.get("severity"), SEVERITY_REQUIRED)
        sections = normalize_sections(revision.get("changed_sections"))
        if severity != SEVERITY_MINOR and sections:
            severities.append(severity)
        for section in sections:
            if severity == SEVERITY_MINOR:
                continue
            if int(section_revisions.get(section, 0) or 0) < revision_number:
                pending_latest[section] = max(
                    int(pending_latest.get(section, 0) or 0),
                    revision_number,
                )

    pending_sections = [
        section for section in SETUP_SECTIONS if section in pending_latest
    ]
    severity = max_severity(severities) if pending_sections else SEVERITY_MINOR
    return {
        "enabled": bool(guild.get("enabled", False)),
        "current_revision": current_revision,
        "completed_revision": completed_revision,
        "pending_sections": pending_sections,
        "pending_section_revisions": pending_latest,
        "severity": severity,
        "is_current": not pending_sections and completed_revision >= current_revision,
        "first_time": completed_revision <= 0 and current_revision > 0,
        "access_gated": (
            bool(guild.get("enabled", False))
            and bool(guild.get("gate_active", False))
            and str(guild.get("access_mode")) == ACCESS_MODE_STRICT
            and bool(pending_sections)
            and (
                severity == SEVERITY_ACCESS_GATED
                or (completed_revision <= 0 and current_revision > 0)
            )
        ),
    }


async def mark_section_reviewed(
    guild_id: int,
    user_id: int,
    section: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    sections = normalize_sections([section])
    if not sections:
        raise ValueError("Unknown member-setup section.")
    section_key = sections[0]

    guild = await load_guild_setup_state(int(guild_id), refresh=True)
    member = await load_member_setup_state(int(guild_id), int(user_id), refresh=True)
    current_revision = int(guild.get("current_revision") or 0)
    section_revisions = dict(member.get("section_revisions") or {})
    section_revisions[section_key] = current_revision
    member["section_revisions"] = section_revisions
    member["last_reviewed_at"] = utc_now_iso()

    preview = member_review_status(guild, member)
    if not preview["pending_sections"]:
        member["completed_revision"] = current_revision
        member["completed_at"] = utc_now_iso()

    saved = await save_member_setup_state(int(guild_id), int(user_id), member)
    return saved, member_review_status(guild, saved)


async def complete_current_revision(
    guild_id: int,
    user_id: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    guild = await load_guild_setup_state(int(guild_id), refresh=True)
    member = await load_member_setup_state(int(guild_id), int(user_id), refresh=True)
    status = member_review_status(guild, member)
    if status["pending_sections"]:
        raise ValueError("Required setup sections still need review.")
    current_revision = int(guild.get("current_revision") or 0)
    member["completed_revision"] = current_revision
    member["completed_at"] = utc_now_iso()
    member["last_reviewed_at"] = utc_now_iso()
    saved = await save_member_setup_state(int(guild_id), int(user_id), member)
    return saved, member_review_status(guild, saved)


def latest_revision(guild_state: Mapping[str, Any]) -> Optional[dict[str, Any]]:
    state = normalize_guild_setup_state(guild_state)
    history = list(state.get("history") or [])
    return dict(history[-1]) if history else None
