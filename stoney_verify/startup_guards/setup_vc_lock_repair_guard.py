from __future__ import annotations

"""Bridge existing permission repair to the central setup permission policy."""

from typing import Optional

import discord

_DONE = False


def _locked_voice_verify_overwrites(
    guild: discord.Guild,
    *,
    staff_role: Optional[discord.Role],
    control_role: Optional[discord.Role],
    unverified_role: Optional[discord.Role],
    verified_role: Optional[discord.Role],
    resident_role: Optional[discord.Role],
) -> dict[object, discord.PermissionOverwrite]:
    from stoney_verify.services.setup_permission_policy import vc_verification_overwrites

    return vc_verification_overwrites(
        guild,
        staff_role=staff_role,
        control_role=control_role,
        unverified_role=unverified_role,
        verified_role=verified_role,
        resident_role=resident_role,
    )


def _load_label_precision() -> None:
    try:
        from stoney_verify.startup_guards import setup_permission_label_precision_guard
        setup_permission_label_precision_guard.apply()
    except Exception as exc:
        try:
            print(f"⚠️ setup_vc_lock_repair_guard label precision failed: {exc!r}")
        except Exception:
            pass


def _load_health_precision() -> None:
    try:
        from stoney_verify.startup_guards import setup_vc_health_precision_guard
        setup_vc_health_precision_guard.apply()
    except Exception as exc:
        try:
            print(f"⚠️ setup_vc_lock_repair_guard health precision failed: {exc!r}")
        except Exception:
            pass


def _load_queue_bridge() -> None:
    try:
        from stoney_verify.startup_guards import setup_permission_repair_queue_guard
        setup_permission_repair_queue_guard.apply()
    except Exception as exc:
        try:
            print(f"⚠️ setup_vc_lock_repair_guard queue bridge failed: {exc!r}")
        except Exception:
            pass


def apply() -> bool:
    global _DONE
    if _DONE:
        return True
    try:
        from stoney_verify.startup_guards import setup_permission_repair_guard as repair
        repair._voice_verify_overwrites = _locked_voice_verify_overwrites
        _load_label_precision()
        _load_health_precision()
        _load_queue_bridge()
        _DONE = True
        print("🛠️ setup_vc_lock_repair_guard active; permission repair uses central VC policy")
        return True
    except Exception as exc:
        try:
            print(f"⚠️ setup_vc_lock_repair_guard failed: {exc!r}")
        except Exception:
            pass
        return False


apply()

__all__ = ["apply"]
