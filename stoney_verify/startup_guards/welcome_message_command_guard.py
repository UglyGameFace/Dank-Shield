from __future__ import annotations

"""Expose /dank welcome in the public command surface.

The welcome command group is part of the setup/onboarding product surface. This
startup hook attaches it before commands_ext registers the module-level /dank
group and also marks it as an allowed public child so the public pruning pass
does not remove it.
"""

_PATCHED = False


def apply() -> bool:
    global _PATCHED
    if _PATCHED:
        return True
    try:
        import stoney_verify.commands_ext as commands_ext

        allowed = set(getattr(commands_ext, "_ALLOWED_STONEY_CHILDREN", set()) or set())
        if "welcome" not in allowed:
            allowed.add("welcome")
            commands_ext._ALLOWED_STONEY_CHILDREN = allowed

        from stoney_verify.commands_ext import public_welcome_group

        register = getattr(public_welcome_group, "register_public_welcome_group_commands", None)
        if callable(register):
            register(None, None)
        _PATCHED = True
        print("✅ welcome_message_command_guard active; /dank welcome is allowed in public setup surface")
        return True
    except Exception as exc:
        try:
            print(f"⚠️ welcome_message_command_guard failed: {type(exc).__name__}: {exc}")
        except Exception:
            pass
        return False


apply()

__all__ = ["apply"]
