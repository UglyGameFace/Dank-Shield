from __future__ import annotations

_PATCHED = False


def apply() -> bool:
    global _PATCHED
    if _PATCHED:
        return True
    try:
        import stoney_verify.commands_ext as commands_ext

        allowed = set(getattr(commands_ext, "_ALLOWED_DANK_CHILDREN", set()) or set())
        if "roles" not in allowed:
            allowed.add("roles")
            commands_ext._ALLOWED_DANK_CHILDREN = allowed

        from stoney_verify.commands_ext import public_self_roles_group
        from stoney_verify.startup_guards import profile_role_editor_guard
        from stoney_verify.startup_guards import profile_terms_newline_guard

        profile_terms_newline_guard.apply()
        # Must run before register_public_self_roles_group_commands(), because that
        # function registers the persistent ProfilePanelView with Discord. If this
        # patch runs after registration, old buttons like "Server Cosmetics" remain
        # attached to the runtime view until the next full restart/redeploy.
        profile_role_editor_guard.apply()

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
        print("✅ self_roles_command_guard active; /dank roles and profile role/cosmetic buttons are allowed in public setup surface")
        return True
    except Exception as exc:
        try:
            print(f"⚠️ self_roles_command_guard failed: {type(exc).__name__}: {exc}")
        except Exception:
            pass
        return False


apply()

__all__ = ["apply"]
