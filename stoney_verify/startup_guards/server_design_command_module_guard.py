from __future__ import annotations

"""Legacy validation shim for the native /dank design command registry.

Older deployments needed this module to mutate COMMAND_MODULES, profiles, and
_selected_command_modules at import time. The canonical commands_ext registry now
contains public_design_group in every public profile, so runtime mutation is more
likely to create duplicate/ordering bugs than to help.
"""

_VALIDATED = False


def apply() -> bool:
    global _VALIDATED
    if _VALIDATED:
        return True
    try:
        import stoney_verify.commands_ext as commands_ext

        modules = tuple(getattr(commands_ext, "COMMAND_MODULES", tuple()) or tuple())
        profiles = dict(getattr(commands_ext, "COMMAND_PROFILES", {}) or {})
        allowed = set(getattr(commands_ext, "_ALLOWED_DANK_CHILDREN", set()) or set())

        has_module = any(str(spec[0]) == "public_design_group" for spec in modules)
        profiles_ok = all("public_design_group" in tuple(profiles.get(name, tuple()) or tuple()) for name in ("public", "minimal", "public-admin"))
        allowed_ok = "design" in allowed
        if not (has_module and profiles_ok and allowed_ok):
            print(
                "⚠️ server_design_command_module_guard validation failed; "
                f"module={has_module} profiles={profiles_ok} allowed={allowed_ok}"
            )
            return False

        _VALIDATED = True
        print("✅ server_design_command_module_guard validation passed; no registry mutation required")
        return True
    except Exception as exc:
        try:
            print(f"⚠️ server_design_command_module_guard validation failed: {type(exc).__name__}: {exc}")
        except Exception:
            pass
        return False


apply()

__all__ = ["apply"]
