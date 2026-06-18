from __future__ import annotations

"""Make /dank design part of the normal slash-command registration pass.

The previous guard registered the command too late for some deployments: the
startup guard could attach it after the Discord command tree had already been
selected/synced. This guard injects the public_design_group module into
commands_ext before app.py runs command registration, so the command is visible
on the next sync/restart.
"""

from typing import Any

_PATCHED = False
_SPEC = ("public_design_group", "register_public_design_group_commands", "core: /dank design Server Design Studio")


def _append_unique_tuple(value: Any, item: str) -> tuple[str, ...]:
    existing = tuple(str(x) for x in (value or tuple()))
    return existing if item in existing else existing + (item,)


def apply() -> bool:
    global _PATCHED
    if _PATCHED:
        return True
    try:
        import stoney_verify.commands_ext as commands_ext

        modules = list(getattr(commands_ext, "COMMAND_MODULES", []) or [])
        if not any(str(spec[0]) == _SPEC[0] for spec in modules):
            insert_at = len(modules)
            for index, spec in enumerate(modules):
                if str(spec[0]) == "public_setup_group":
                    insert_at = index + 1
                    break
            modules.insert(insert_at, _SPEC)
            commands_ext.COMMAND_MODULES = modules

        allowed = set(getattr(commands_ext, "_ALLOWED_STONEY_CHILDREN", set()) or set())
        allowed.add("design")
        commands_ext._ALLOWED_STONEY_CHILDREN = allowed

        core = _append_unique_tuple(getattr(commands_ext, "_PUBLIC_CORE_MODULES", tuple()), _SPEC[0])
        commands_ext._PUBLIC_CORE_MODULES = core
        profiles = dict(getattr(commands_ext, "COMMAND_PROFILES", {}) or {})
        for profile in ("public", "minimal", "public-admin"):
            profiles[profile] = _append_unique_tuple(profiles.get(profile, tuple()), _SPEC[0])
        commands_ext.COMMAND_PROFILES = profiles

        _PATCHED = True
        print("✅ server_design_command_module_guard active; public_design_group will register before slash sync")
        return True
    except Exception as exc:
        try:
            print(f"⚠️ server_design_command_module_guard failed: {type(exc).__name__}: {exc}")
        except Exception:
            pass
        return False


apply()

__all__ = ["apply"]
