from __future__ import annotations

"""Bridge legacy member/event helpers to native per-guild role truth.

The real logic lives in ``stoney_verify.role_truth``. This guard exists only so
older modules that still expose sync helper names use the native resolver until
those call sites are fully refactored.
"""

from typing import Any

import discord

_PATCHED = False


def _log(message: str) -> None:
    try:
        print(f"🧭 per_guild_role_truth_guard {message}")
    except Exception:
        pass


def _warn(message: str) -> None:
    try:
        print(f"⚠️ per_guild_role_truth_guard {message}")
    except Exception:
        pass


def _patch_events() -> bool:
    try:
        from stoney_verify import events
        from stoney_verify import role_truth
    except Exception as e:
        _warn(f"events/role_truth import failed: {e!r}")
        return False

    original_snapshot = getattr(events, "_member_role_snapshot", None)

    def member_has_any_safe_access_role(member: discord.Member, *, include_unverified: bool = True) -> bool:
        return bool(role_truth.member_has_any_safe_access_role(member, include_unverified=include_unverified))

    def member_is_pending_verification(member: discord.Member) -> bool:
        return bool(role_truth.member_is_pending_verification(member))

    def member_role_snapshot(member: discord.Member) -> dict[str, Any]:
        base: dict[str, Any] = {}
        if callable(original_snapshot):
            try:
                base = dict(original_snapshot(member) or {})
            except Exception:
                base = {}
        return role_truth.apply_truth_to_snapshot(member, base)

    events._member_has_any_safe_access_role = member_has_any_safe_access_role  # type: ignore[attr-defined]
    events._member_is_pending_verification = member_is_pending_verification  # type: ignore[attr-defined]
    events._member_role_snapshot = member_role_snapshot  # type: ignore[attr-defined]
    return True


def _patch_sync_service() -> bool:
    try:
        from stoney_verify.members_new import sync_service
        from stoney_verify import role_truth
    except Exception as e:
        _warn(f"members_new.sync_service/role_truth import failed: {e!r}")
        return False

    original_snapshot = getattr(sync_service, "_member_role_snapshot", None)

    def member_role_snapshot(member: discord.Member) -> dict[str, Any]:
        base: dict[str, Any] = {}
        if callable(original_snapshot):
            try:
                base = dict(original_snapshot(member) or {})
            except Exception:
                base = {}
        return role_truth.apply_truth_to_snapshot(member, base)

    sync_service._member_role_snapshot = member_role_snapshot  # type: ignore[attr-defined]
    return True


def _patch_legacy_service() -> bool:
    try:
        from stoney_verify.members_new import service
        from stoney_verify import role_truth
    except Exception as e:
        _warn(f"members_new.service/role_truth import failed: {e!r}")
        return False

    def member_role_flags(member: discord.Member) -> dict[str, bool]:
        truth = role_truth.member_role_truth(member)
        return {
            "has_any_role": bool(truth.get("has_any_role")),
            "has_unverified": bool(truth.get("has_unverified")),
            "has_verified_role": bool(truth.get("has_verified_role")),
            "has_staff_role": bool(truth.get("has_staff_role")),
            "has_secondary_verified_role": bool(truth.get("has_secondary_verified_role")),
            "has_cosmetic_only": bool(truth.get("has_cosmetic_only")),
        }

    def role_state(member: discord.Member) -> dict[str, str]:
        state, reason = role_truth.role_state_from_truth(member)
        return {"role_state": state, "role_state_reason": reason}

    def has_verified_role(member: discord.Member) -> bool:
        truth = role_truth.member_role_truth(member)
        return bool(truth.get("has_verified_role") and not truth.get("has_unverified"))

    service._member_role_flags = member_role_flags  # type: ignore[attr-defined]
    service._role_state = role_state  # type: ignore[attr-defined]
    service._has_verified_role = has_verified_role  # type: ignore[attr-defined]
    return True


def apply() -> bool:
    global _PATCHED
    if _PATCHED:
        return True
    events_ok = _patch_events()
    sync_ok = _patch_sync_service()
    legacy_ok = _patch_legacy_service()
    _PATCHED = True
    _log(f"active events={events_ok} sync_service={sync_ok} legacy_service={legacy_ok}")
    return bool(events_ok or sync_ok or legacy_ok)


apply()

__all__ = ["apply"]
