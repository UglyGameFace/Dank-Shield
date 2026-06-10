from __future__ import annotations

"""Startup loader for optional public member-update modlog coverage.

The main events.py listener already calls modlog.maybe_log_member_update_diff.
Registering the older public listener at the same time creates duplicate
Member Updated embeds for every role change.

Default behavior keeps the extra listener off. It can still be enabled for
special deployments that intentionally do not load events.py by setting:

    DANK_ENABLE_EXTRA_MEMBER_UPDATE_MODLOG=true

Important: this startup guard must not monkey-patch modlog internals. Member
update noise shaping belongs in stoney_verify.modlog or the verification
approval service so ban/kick logs and future imports stay predictable.
"""

import os

_REGISTERED = False


def _log(message: str) -> None:
    try:
        print(f"🧍 member_update_modlog {message}")
    except Exception:
        pass


def _warn(message: str) -> None:
    try:
        print(f"⚠️ member_update_modlog {message}")
    except Exception:
        pass


def _env_bool(name: str, default: bool = False) -> bool:
    try:
        raw = os.getenv(name)
        if raw is None or not str(raw).strip():
            return bool(default)
        return str(raw).strip().lower() in {"1", "true", "yes", "y", "on"}
    except Exception:
        return bool(default)


def register_member_update_modlog() -> bool:
    """Register the legacy public listener only when explicitly requested."""

    global _REGISTERED
    if _REGISTERED:
        return True

    _REGISTERED = True

    if not _env_bool("DANK_ENABLE_EXTRA_MEMBER_UPDATE_MODLOG", False):
        _log("extra public listener disabled; core events.py member-update logger is active")
        return True

    try:
        from stoney_verify.globals import bot
        from stoney_verify.commands_ext.public_member_update_modlog import register_public_member_update_modlog

        register_public_member_update_modlog(bot, getattr(bot, "tree", None))
        _log("registered public role/nickname/timeout update listener")
        return True
    except Exception as e:
        _warn(f"failed registering public member update listener: {e!r}")
        return False


register_member_update_modlog()


__all__ = ["register_member_update_modlog"]
