from __future__ import annotations

"""
Python host fallback for Dank Shield startup safety.

Some hosts auto-import ``sitecustomize`` before the normal app entrypoint. Keep
this file tiny: the real runtime safety logic lives in
``stoney_verify.startup_guards.runtime_safety``.
"""


def _force_verify_panel_command_module() -> None:
    try:
        import stoney_verify.commands_ext as commands_ext

        spec = (
            "public_verify_basic_panel",
            "register_public_verify_basic_panel_commands",
            "core: /verify panel basic button verification command",
        )
        modules = list(getattr(commands_ext, "COMMAND_MODULES", []) or [])
        names = {str(item[0]) for item in modules if item}
        if spec[0] not in names:
            insert_at = len(modules)
            for index, item in enumerate(modules):
                try:
                    if item[0] == "public_verify_group":
                        insert_at = index
                        break
                except Exception:
                    continue
            modules.insert(insert_at, spec)
            commands_ext.COMMAND_MODULES = modules

        core = tuple(getattr(commands_ext, "_PUBLIC_CORE_MODULES", ()) or ())
        if spec[0] not in core:
            updated_core = []
            inserted = False
            for item in core:
                if item == "public_verify_group" and not inserted:
                    updated_core.append(spec[0])
                    inserted = True
                updated_core.append(item)
            if not inserted:
                updated_core.append(spec[0])
            commands_ext._PUBLIC_CORE_MODULES = tuple(updated_core)

        profiles = dict(getattr(commands_ext, "COMMAND_PROFILES", {}) or {})
        for profile in ("public", "minimal", "public-admin", "dev"):
            values = tuple(profiles.get(profile, ()) or ())
            if values and spec[0] not in values:
                if "public_verify_group" in values:
                    out = []
                    inserted = False
                    for item in values:
                        if item == "public_verify_group" and not inserted:
                            out.append(spec[0])
                            inserted = True
                        out.append(item)
                    profiles[profile] = tuple(out)
                else:
                    profiles[profile] = values + (spec[0],)
        commands_ext.COMMAND_PROFILES = profiles
    except Exception:
        pass


try:
    import stoney_verify.startup_guards as startup_guards

    if not hasattr(startup_guards, "load_all_startup_guards") and hasattr(startup_guards, "load_startup_guards"):
        startup_guards.load_all_startup_guards = startup_guards.load_startup_guards
except Exception as e:
    try:
        print(f"⚠️ sitecustomize failed to install startup guard loader compatibility: {e!r}")
    except Exception:
        pass

try:
    from stoney_verify.startup_guards.runtime_safety import load_runtime_safety

    load_runtime_safety()
except Exception as e:
    try:
        print(f"⚠️ sitecustomize failed to load startup_guards.runtime_safety: {e!r}")
    except Exception:
        pass

try:
    _force_verify_panel_command_module()
    import stoney_verify.commands_ext.public_verify_basic_panel  # noqa: F401
except Exception:
    pass

try:
    from stoney_verify.startup_guards import basic_verification_mode_guard
    basic_verification_mode_guard.apply()
except Exception:
    pass


# TEMPORARY CI BRIDGE — removed and restored byte-for-byte before the permanent
# Smart Auto-Detect commit. It activates only for the dedicated patch applier.
try:
    import atexit
    import runpy
    import sys
    from pathlib import Path

    if str(sys.argv[0]).endswith("tools/apply_dank_design_category_aware_auto_detect.py"):
        _smart_root = Path(__file__).resolve().parent
        _smart_followup = _smart_root / "tools/apply_dank_design_category_aware_followup.py"

        def _run_smart_auto_detect_followup() -> None:
            if _smart_followup.exists():
                runpy.run_path(str(_smart_followup), run_name="__main__")

        atexit.register(_run_smart_auto_detect_followup)
except Exception:
    pass
