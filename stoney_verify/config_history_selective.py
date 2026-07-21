from __future__ import annotations

"""Selective configuration backup and restore planning.

This module extends the canonical :mod:`stoney_verify.config_history` service.
It does not create a second configuration source: live values remain in the
existing guild configuration and ticket-category tables, while historical
snapshots remain in ``guild_config_versions``.
"""

import asyncio
from typing import Any, Iterable, Mapping, Optional

from . import config_history as history
from .guild_config import clear_guild_config_cache

CORE_DOMAIN = "core"
TICKET_CHOICES_DOMAIN = "ticket_choices"
VALID_BACKUP_DOMAINS = {CORE_DOMAIN, TICKET_CHOICES_DOMAIN}

RESTORE_ALL = "all"
RESTORE_MISSING = "missing"
RESTORE_SELECTED = "selected"
VALID_RESTORE_MODES = {RESTORE_ALL, RESTORE_MISSING, RESTORE_SELECTED}

_CONTAINER_KEYS = ("settings", "config", "metadata", "meta")

_SECTION_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "Protection & Moderation",
        (
            "antinuke",
            "anti_nuke",
            "automod",
            "spam",
            "raid",
            "quarantine",
            "moderation",
            "modlog",
            "security",
            "bad_word",
            "invite_shield",
            "link_shield",
        ),
    ),
    (
        "Tickets & Verification",
        (
            "ticket",
            "verify",
            "verification",
            "verified",
            "unverified",
            "voice_verify",
            "vc_verify",
            "id_verify",
            "web_verify",
            "transcript",
        ),
    ),
    (
        "Roles",
        ("role", "permission", "access_control"),
    ),
    (
        "Channels & Categories",
        ("channel", "category", "folder"),
    ),
    (
        "Timers & Rules",
        (
            "timer",
            "timeout",
            "cooldown",
            "threshold",
            "window",
            "duration",
            "interval",
            "hours",
            "minutes",
            "seconds",
            "days",
            "ttl",
            "limit",
            "prefix",
            "rule",
        ),
    ),
    (
        "Welcome & Member Experience",
        (
            "welcome",
            "goodbye",
            "join_",
            "leave_",
            "member_",
            "self_role",
            "profile",
            "pronoun",
            "identity",
        ),
    ),
    (
        "Server Design",
        (
            "design",
            "theme",
            "appearance",
            "color",
            "emoji",
            "font",
            "frame",
            "layout",
        ),
    ),
    (
        "Feature Choices",
        ("enabled", "service", "setup_choice", "setup_type", "panel_style"),
    ),
)


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or isinstance(value, bool):
            return int(default)
        return int(str(value).strip())
    except Exception:
        return int(default)


def _safe_str(value: Any, default: str = "") -> str:
    try:
        text = str(value or "").strip()
        return text if text else default
    except Exception:
        return default


def _clean_items(values: Optional[Iterable[str]]) -> list[str]:
    clean: list[str] = []
    seen: set[str] = set()
    for value in values or ():
        item = _safe_str(value).lower()
        if not item or item in seen:
            continue
        seen.add(item)
        clean.append(item)
    return clean


def humanize_config_key(key: str) -> str:
    text = _safe_str(key).replace("_id", "").replace("_", " ").strip()
    if not text:
        return "Unknown setting"
    replacements = {
        "vc": "Voice",
        "id": "ID",
        "url": "URL",
        "dm": "DM",
        "sla": "SLA",
        "ttl": "TTL",
    }
    words: list[str] = []
    for word in text.split():
        words.append(replacements.get(word.lower(), word.capitalize()))
    return " ".join(words)


def core_key_section(key: str) -> str:
    lowered = _safe_str(key).lower()
    for section, markers in _SECTION_RULES:
        if any(marker in lowered for marker in markers):
            return section
    return "Other Saved Settings"


def summarize_core_keys(keys: Iterable[str]) -> dict[str, list[str]]:
    grouped: dict[str, list[str]] = {}
    for key in sorted({_safe_str(item) for item in keys if _safe_str(item)}):
        grouped.setdefault(core_key_section(key), []).append(key)
    return grouped


def _flat_core(row: Mapping[str, Any]) -> dict[str, Any]:
    return history._flatten_functional_config(row)


def _ticket_rows(snapshot: Mapping[str, Any]) -> list[dict[str, Any]]:
    return history._category_snapshot_rows(snapshot)


def _missing_core_keys(
    snapshot: Mapping[str, Any],
    current: Mapping[str, Any],
) -> list[str]:
    before = _flat_core(snapshot)
    after = _flat_core(current)
    return sorted(
        key
        for key, value in before.items()
        if key not in after or after.get(key) in (None, "", [], {})
        if value not in (None, "", [], {})
    )


def _ticket_maps(
    snapshot: Mapping[str, Any],
    current_rows: list[dict[str, Any]],
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    saved = {
        _safe_str(row.get("slug")).lower(): dict(row)
        for row in _ticket_rows(snapshot)
        if _safe_str(row.get("slug"))
    }
    current = {
        _safe_str(row.get("slug")).lower(): dict(row)
        for row in current_rows
        if _safe_str(row.get("slug"))
    }
    return saved, current


def _missing_ticket_slugs(
    snapshot: Mapping[str, Any],
    current_rows: list[dict[str, Any]],
) -> list[str]:
    saved, current = _ticket_maps(snapshot, current_rows)
    return sorted(slug for slug in saved if slug not in current)


def create_scoped_manual_backup_sync(
    guild_id: int,
    *,
    domains: Iterable[str],
    actor_id: Optional[int] = None,
    reason: str = "Manual selected backup",
) -> dict[str, Any]:
    gid = int(guild_id)
    selected = [domain for domain in _clean_items(domains) if domain in VALID_BACKUP_DOMAINS]
    if not selected:
        raise ValueError("Choose Core Settings, Ticket Choices, or both before creating a backup.")

    versions: list[dict[str, Any]] = []
    core_version: Optional[dict[str, Any]] = None
    ticket_version: Optional[dict[str, Any]] = None

    if CORE_DOMAIN in selected:
        table_name, current = history._fetch_current_config_row_sync(gid)
        core_version = history._insert_snapshot_sync(
            gid,
            current,
            config_table=table_name,
            source="manual_backup",
            mode="selected_domains",
            actor_id=actor_id,
            reason=reason,
            is_manual=True,
        )
        versions.append(core_version)

    if TICKET_CHOICES_DOMAIN in selected:
        available, rows = history._fetch_ticket_categories_state_sync(gid)
        if not available:
            raise RuntimeError("Ticket Choices are not available in this deployment.")
        ticket_version = history._insert_snapshot_sync(
            gid,
            history._ticket_category_snapshot(gid, rows),
            config_table=history.TICKET_CATEGORIES_TABLE,
            source="manual_backup",
            mode="selected_domains",
            actor_id=actor_id,
            reason=reason,
            is_manual=True,
        )
        versions.append(ticket_version)

    return {
        "guild_id": str(gid),
        "selected_domains": selected,
        "core_version": core_version,
        "ticket_categories_version": ticket_version,
        "backup_versions": versions,
    }


def plan_selective_restore_sync(
    guild_id: int,
    version_id: int,
) -> dict[str, Any]:
    gid = int(guild_id)
    vid = int(version_id)
    table_name, current = history._fetch_current_config_row_sync(gid)
    version = history._fetch_version_sync(gid, vid)
    version_table = _safe_str(version.get("config_table"), table_name)
    snapshot = history._row_dict(version.get("snapshot"))

    if not snapshot:
        raise RuntimeError(f"Configuration version {vid} has no usable snapshot.")
    if _safe_int(snapshot.get("guild_id"), gid) != gid:
        raise RuntimeError("Configuration version belongs to a different guild.")

    if version_table == history.TICKET_CATEGORIES_TABLE:
        available, current_rows = history._fetch_ticket_categories_state_sync(gid)
        if not available:
            raise RuntimeError("Ticket Choices are not available in this deployment.")
        saved, current_map = _ticket_maps(snapshot, current_rows)
        changed = history.changed_ticket_category_slugs(snapshot, current_rows)
        missing = _missing_ticket_slugs(snapshot, current_rows)
        labels = {
            slug: _safe_str((saved.get(slug) or current_map.get(slug) or {}).get("name"), slug)
            for slug in changed
        }
        return {
            "guild_id": str(gid),
            "version_id": vid,
            "config_table": history.TICKET_CATEGORIES_TABLE,
            "domain": TICKET_CHOICES_DOMAIN,
            "domain_label": "Ticket Choices",
            "changed_items": changed,
            "missing_items": missing,
            "item_labels": labels,
            "saved_count": len(saved),
            "current_count": len(current_map),
            "core_sections": {},
        }

    if version_table != table_name:
        raise RuntimeError("Configuration version belongs to a different configuration table.")

    changed = history.changed_config_keys(snapshot, current)
    missing = _missing_core_keys(snapshot, current)
    return {
        "guild_id": str(gid),
        "version_id": vid,
        "config_table": table_name,
        "domain": CORE_DOMAIN,
        "domain_label": "Core Settings",
        "changed_items": changed,
        "missing_items": missing,
        "item_labels": {key: humanize_config_key(key) for key in changed},
        "saved_count": len(_flat_core(snapshot)),
        "current_count": len(_flat_core(current)),
        "core_sections": summarize_core_keys(_flat_core(snapshot).keys()),
    }


def _selected_items_from_plan(
    plan: Mapping[str, Any],
    *,
    mode: str,
    selected_items: Optional[Iterable[str]],
) -> list[str]:
    restore_mode = _safe_str(mode, RESTORE_ALL).lower()
    if restore_mode not in VALID_RESTORE_MODES:
        raise ValueError("Unknown restore mode.")

    changed = {_safe_str(item).lower() for item in plan.get("changed_items", []) if _safe_str(item)}
    missing = {_safe_str(item).lower() for item in plan.get("missing_items", []) if _safe_str(item)}

    if restore_mode == RESTORE_ALL:
        chosen = sorted(changed)
    elif restore_mode == RESTORE_MISSING:
        chosen = sorted(changed & missing)
    else:
        requested = set(_clean_items(selected_items))
        invalid = sorted(requested - changed)
        if invalid:
            raise ValueError("One or more selected restore items are not different from the current configuration.")
        chosen = sorted(requested)

    if not chosen:
        raise ValueError("There are no matching configuration changes to restore.")
    return chosen


def _restore_core_selected_sync(
    guild_id: int,
    version_id: int,
    *,
    version: Mapping[str, Any],
    table_name: str,
    current: Mapping[str, Any],
    selected: list[str],
    actor_id: Optional[int],
    reason: str,
    mode: str,
) -> dict[str, Any]:
    gid = int(guild_id)
    vid = int(version_id)
    snapshot = history._row_dict(version.get("snapshot"))
    selected_set = set(selected)

    pre_restore = history._insert_snapshot_sync(
        gid,
        current,
        config_table=table_name,
        source="pre_restore_backup",
        mode="selective_restore_guard",
        actor_id=actor_id,
        reason=f"Automatic backup before selective restore of version {vid}",
        is_manual=True,
    )

    allowed_columns = {str(key) for key in current.keys()}
    restore_payload: dict[str, Any] = {}

    for key in selected:
        if key in allowed_columns and key not in history._RESTORE_EXCLUDED_KEYS:
            restore_payload[key] = snapshot.get(key) if key in snapshot else None

    touched_containers: set[str] = set()
    for container in _CONTAINER_KEYS:
        if container not in allowed_columns:
            continue
        current_nested = dict(current.get(container)) if isinstance(current.get(container), Mapping) else {}
        saved_nested = dict(snapshot.get(container)) if isinstance(snapshot.get(container), Mapping) else {}
        changed_container = False
        for key in selected:
            if key in saved_nested:
                current_nested[key] = saved_nested[key]
                changed_container = True
            elif key in current_nested:
                current_nested.pop(key, None)
                changed_container = True
        if changed_container:
            restore_payload[container] = current_nested
            touched_containers.add(container)

    audit_container = next(
        (name for name in ("settings", "config", "metadata") if name in allowed_columns),
        None,
    )
    if audit_container:
        raw = restore_payload.get(audit_container, current.get(audit_container))
        restore_payload[audit_container] = history._restore_audit_payload(
            raw,
            actor_id=actor_id,
            reason=reason,
            version_id=vid,
        )
        touched_containers.add(audit_container)

    if not restore_payload:
        raise RuntimeError("The selected Core Settings cannot be restored in the current schema.")

    response = (
        history._require_supabase()
        .table(table_name)
        .update(restore_payload)
        .eq("guild_id", str(gid))
        .execute()
    )
    rows = getattr(response, "data", None) or []
    restored = (
        dict(rows[0])
        if rows and isinstance(rows[0], Mapping)
        else history._fetch_current_config_row_sync(gid)[1]
    )
    clear_guild_config_cache(gid)

    return {
        "guild_id": str(gid),
        "config_table": table_name,
        "restored_from_version_id": vid,
        "restored": restored,
        "pre_restore_backup": pre_restore,
        "restore_mode": mode,
        "restored_items": selected,
        "restored_item_count": len(selected),
    }


def _restore_ticket_choices_selected_sync(
    guild_id: int,
    version_id: int,
    *,
    version: Mapping[str, Any],
    selected: list[str],
    actor_id: Optional[int],
    reason: str,
    mode: str,
) -> dict[str, Any]:
    gid = int(guild_id)
    vid = int(version_id)
    snapshot = history._row_dict(version.get("snapshot"))
    available, current_rows = history._fetch_ticket_categories_state_sync(gid)
    if not available:
        raise RuntimeError("Ticket Choices are not available in this deployment.")

    saved, current = _ticket_maps(snapshot, current_rows)
    target = {slug: dict(row) for slug, row in current.items()}
    for slug in selected:
        if slug in saved:
            target[slug] = dict(saved[slug])
        else:
            target.pop(slug, None)

    target_rows = list(target.values())
    target_rows.sort(
        key=lambda row: (
            _safe_int(row.get("sort_order"), 999),
            _safe_str(row.get("slug")).lower(),
        )
    )

    pre_restore = history._insert_snapshot_sync(
        gid,
        history._ticket_category_snapshot(gid, current_rows),
        config_table=history.TICKET_CATEGORIES_TABLE,
        source="pre_restore_backup",
        mode="selective_restore_guard",
        actor_id=actor_id,
        reason=f"Automatic backup before selective Ticket Choices restore of version {vid}",
        is_manual=True,
    )

    try:
        history._require_supabase().rpc(
            history.TICKET_CATEGORY_RESTORE_RPC,
            {"p_guild_id": str(gid), "p_rows": target_rows},
        ).execute()
    except Exception as exc:
        if history._is_missing_rpc_error(exc):
            raise RuntimeError(
                "Atomic ticket-choice restore is not installed yet. Apply the guild config version-history migration first."
            ) from exc
        raise

    _available_after, restored_rows = history._fetch_ticket_categories_state_sync(gid)
    restored_snapshot = history._ticket_category_snapshot(gid, restored_rows)
    final_version = history._insert_snapshot_sync(
        gid,
        restored_snapshot,
        config_table=history.TICKET_CATEGORIES_TABLE,
        source="config_history_restore",
        mode="selective_restore",
        actor_id=actor_id,
        reason=reason,
        is_manual=False,
    )

    return {
        "guild_id": str(gid),
        "config_table": history.TICKET_CATEGORIES_TABLE,
        "restored_from_version_id": vid,
        "restored": restored_snapshot,
        "pre_restore_backup": pre_restore,
        "restore_version": final_version,
        "restore_mode": mode,
        "restored_items": selected,
        "restored_item_count": len(selected),
    }


def restore_config_version_selective_sync(
    guild_id: int,
    version_id: int,
    *,
    mode: str = RESTORE_ALL,
    selected_items: Optional[Iterable[str]] = None,
    actor_id: Optional[int] = None,
    reason: str = "Restore selected configuration changes",
) -> dict[str, Any]:
    gid = int(guild_id)
    vid = int(version_id)
    plan = plan_selective_restore_sync(gid, vid)
    selected = _selected_items_from_plan(
        plan,
        mode=mode,
        selected_items=selected_items,
    )
    table_name, current = history._fetch_current_config_row_sync(gid)
    version = history._fetch_version_sync(gid, vid)

    if plan.get("domain") == TICKET_CHOICES_DOMAIN:
        return _restore_ticket_choices_selected_sync(
            gid,
            vid,
            version=version,
            selected=selected,
            actor_id=actor_id,
            reason=reason,
            mode=mode,
        )

    return _restore_core_selected_sync(
        gid,
        vid,
        version=version,
        table_name=table_name,
        current=current,
        selected=selected,
        actor_id=actor_id,
        reason=reason,
        mode=mode,
    )


async def create_scoped_manual_backup(
    guild_id: int,
    *,
    domains: Iterable[str],
    actor_id: Optional[int] = None,
    reason: str = "Manual selected backup",
) -> dict[str, Any]:
    return await asyncio.to_thread(
        create_scoped_manual_backup_sync,
        int(guild_id),
        domains=list(domains),
        actor_id=actor_id,
        reason=reason,
    )


async def plan_selective_restore(guild_id: int, version_id: int) -> dict[str, Any]:
    return await asyncio.to_thread(
        plan_selective_restore_sync,
        int(guild_id),
        int(version_id),
    )


async def restore_config_version_selective(
    guild_id: int,
    version_id: int,
    *,
    mode: str = RESTORE_ALL,
    selected_items: Optional[Iterable[str]] = None,
    actor_id: Optional[int] = None,
    reason: str = "Restore selected configuration changes",
) -> dict[str, Any]:
    return await asyncio.to_thread(
        restore_config_version_selective_sync,
        int(guild_id),
        int(version_id),
        mode=mode,
        selected_items=list(selected_items or ()),
        actor_id=actor_id,
        reason=reason,
    )


__all__ = [
    "CORE_DOMAIN",
    "RESTORE_ALL",
    "RESTORE_MISSING",
    "RESTORE_SELECTED",
    "TICKET_CHOICES_DOMAIN",
    "VALID_BACKUP_DOMAINS",
    "core_key_section",
    "create_scoped_manual_backup",
    "create_scoped_manual_backup_sync",
    "humanize_config_key",
    "plan_selective_restore",
    "plan_selective_restore_sync",
    "restore_config_version_selective",
    "restore_config_version_selective_sync",
    "summarize_core_keys",
]
