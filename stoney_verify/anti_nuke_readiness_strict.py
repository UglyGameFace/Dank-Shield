from __future__ import annotations

"""Read-only strict readiness checks for AntiNuke contain mode."""

from typing import Any

from . import anti_nuke


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or isinstance(value, bool):
            return int(default)
        return int(str(value).strip())
    except Exception:
        return int(default)


def delegated_authority_blockers(guild: Any) -> list[str]:
    """Return guild authority that must be removed before strict contain mode."""

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
        if role_id in bot_role_ids or not anti_nuke.role_has_dangerous_permissions(role):
            continue
        holders = [
            member
            for member in list(getattr(role, "members", []) or [])
            if _safe_int(getattr(member, "id", 0), 0) not in {owner_id, bot_id}
        ]
        if anti_nuke._role_is_default(role) or holders:  # noqa: SLF001
            blockers.append(
                f"Strict containment: @{getattr(role, 'name', 'role')} still grants AntiNuke-risk authority"
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
            if anti_nuke._overwrite_grants_dangerous_permissions(overwrite):  # noqa: SLF001
                blockers.append(
                    f"Strict containment: #{getattr(channel, 'name', 'channel')} still grants AntiNuke-risk channel authority"
                )

    return list(dict.fromkeys(blockers))


__all__ = ["delegated_authority_blockers"]
