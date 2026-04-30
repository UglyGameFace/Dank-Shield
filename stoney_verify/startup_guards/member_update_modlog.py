from __future__ import annotations

"""Startup loader for public member-update modlog coverage.

This keeps role/nickname/timeout audit logging available even though the public
command profile intentionally hides older legacy moderation modules.
"""

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


def register_member_update_modlog() -> bool:
    global _REGISTERED
    if _REGISTERED:
        return True
    try:
        from stoney_verify.globals import bot
        from stoney_verify.commands_ext.public_member_update_modlog import register_public_member_update_modlog

        register_public_member_update_modlog(bot, getattr(bot, "tree", None))
        _REGISTERED = True
        _log("registered public role/nickname/timeout update listener")
        return True
    except Exception as e:
        _warn(f"failed registering public member update listener: {e!r}")
        return False


register_member_update_modlog()


__all__ = ["register_member_update_modlog"]
