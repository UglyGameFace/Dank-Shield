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

        register = getattr(public_self_roles_group, "register_public_self_roles_group_commands", None)
        if callable(register):
            register(None, None)
        _PATCHED = True
        print("✅ self_roles_command_guard active; /dank roles is allowed in public setup surface")
        return True
    except Exception as exc:
        try:
            print(f"⚠️ self_roles_command_guard failed: {type(exc).__name__}: {exc}")
        except Exception:
            pass
        return False


apply()

__all__ = ["apply"]
