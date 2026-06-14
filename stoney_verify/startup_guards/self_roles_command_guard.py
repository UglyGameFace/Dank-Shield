from __future__ import annotations

_PATCHED = False


def apply() -> bool:
    global _PATCHED
    if _PATCHED:
        return True
    try:
        import stoney_verify.commands_ext as commands_ext

        allowed = set(getattr(commands_ext, "_ALLOWED_STONEY_CHILDREN", set()) or set())
        if "roles" not in allowed:
            allowed.add("roles")
            commands_ext._ALLOWED_STONEY_CHILDREN = allowed

        from stoney_verify.commands_ext import public_self_roles_group

        bot = None
        try:
            from stoney_verify.globals import bot as global_bot
            bot = global_bot
        except Exception:
            bot = None

        register = getattr(public_self_roles_group, "register_public_self_roles_group_commands", None)
        if callable(register):
            register(bot, getattr(bot, "tree", None) if bot is not None else None)
        _PATCHED = True
        print("✅ self_roles_command_guard active; /dank roles and self-role buttons are allowed in public setup surface")
        return True
    except Exception as exc:
        try:
            print(f"⚠️ self_roles_command_guard failed: {type(exc).__name__}: {exc}")
        except Exception:
            pass
        return False


apply()

__all__ = ["apply"]
