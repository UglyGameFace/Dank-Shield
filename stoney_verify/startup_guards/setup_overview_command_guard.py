from __future__ import annotations

_PATCHED = False


def apply() -> bool:
    global _PATCHED
    if _PATCHED:
        return True
    try:
        import stoney_verify.commands_ext as commands_ext

        allowed = set(getattr(commands_ext, "_ALLOWED_STONEY_CHILDREN", set()) or set())
        allowed.add("overview")
        commands_ext._ALLOWED_STONEY_CHILDREN = allowed

        from stoney_verify.commands_ext import public_setup_overview

        register = getattr(public_setup_overview, "register_public_setup_overview_commands", None)
        if callable(register):
            register(None, None)

        # This guard is loaded after the feature command guards, so it is the
        # safest place to do the final public /dank surface cleanup.
        try:
            from stoney_verify.startup_guards import production_command_surface_guard

            production_command_surface_guard.apply()
        except Exception as prune_exc:
            print(f"⚠️ setup_overview_command_guard final command prune failed: {type(prune_exc).__name__}: {prune_exc}")

        _PATCHED = True
        print("✅ setup_overview_command_guard active; /dank overview is allowed in public setup surface")
        return True
    except Exception as exc:
        try:
            print(f"⚠️ setup_overview_command_guard failed: {type(exc).__name__}: {exc}")
        except Exception:
            pass
        return False


apply()

__all__ = ["apply"]
