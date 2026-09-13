from __future__ import annotations

"""Read-only readiness checks for the optional Strict Lockdown tier."""

from typing import Any

from . import anti_nuke


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or isinstance(value, bool):
            return int(default)
        return int(str(value).strip())
    except Exception:
        return int(default)


def _label(name: str) -> str:
    clean = str(name or "").strip().lower()
    if clean == "manage_guild":
        return "Manage Server"
    if clean == "manage_emojis_and_stickers":
        return "Manage Expressions"
    return clean.replace("_", " ").title()


def _role_permissions(role: Any) -> list[str]:
    permissions = getattr(role, "permissions", None)
    if permissions is None:
        return []
    found: list[str] = []
    for name in tuple(getattr(anti_nuke, "DANGEROUS_PERMISSION_NAMES", ()) or ()):
        try:
            enabled = bool(getattr(permissions, name, False))
        except Exception:
            enabled = False
        if enabled:
            label = _label(name)
            if label not in found:
                found.append(label)
    return found


def _overwrite_permissions(overwrite: Any) -> list[str]:
    found: list[str] = []
    for name in tuple(getattr(anti_nuke, "DANGEROUS_PERMISSION_NAMES", ()) or ()):
        try:
            enabled = getattr(overwrite, name, None) is True
        except Exception:
            enabled = False
        if enabled:
            label = _label(name)
            if label not in found:
                found.append(label)
    return found


def _joined(values: list[str]) -> str:
    return ", ".join(values) if values else "restricted server authority"


def delegated_authority_blockers(guild: Any) -> list[str]:
    """Return delegated authority that prevents Strict Lockdown from arming."""

    owner_id = _safe_int(getattr(guild, "owner_id", 0), 0)
    me = getattr(guild, "me", None)
    bot_id = _safe_int(getattr(me, "id", 0), 0)
    bot_role_ids = {
        _safe_int(getattr(role, "id", 0), 0)
        for role in list(getattr(me, "roles", []) or [])
    }
    blockers: list[str] = []

    for role in list(getattr(guild, "roles", []) or []):
        role_id = _safe_int(getattr(role, "id", 0), 0)
        permissions = _role_permissions(role)
        if role_id in bot_role_ids or not permissions:
            continue
        holders = [
            member
            for member in list(getattr(role, "members", []) or [])
            if _safe_int(getattr(member, "id", 0), 0) not in {owner_id, bot_id}
        ]
        if anti_nuke._role_is_default(role) or holders:  # noqa: SLF001
            blockers.append(
                f"Strict Lockdown: @{getattr(role, 'name', 'role')} grants {_joined(permissions)}"
            )

    for channel in list(getattr(guild, "channels", []) or []):
        overwrites = getattr(channel, "overwrites", None)
        if not hasattr(overwrites, "items"):
            continue
        try:
            items = list(overwrites.items())
        except Exception:
            continue
        for target, overwrite in items:
            target_id = _safe_int(getattr(target, "id", 0), 0)
            if target_id in bot_role_ids or target_id in {owner_id, bot_id}:
                continue
            permissions = _overwrite_permissions(overwrite)
            if not permissions:
                continue
            target_name = str(getattr(target, "name", "member/role") or "member/role")
            blockers.append(
                f"Strict Lockdown: #{getattr(channel, 'name', 'channel')} grants "
                f"{_joined(permissions)} to {target_name}"
            )

    return list(dict.fromkeys(blockers))


__all__ = ["delegated_authority_blockers"]
