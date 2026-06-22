from __future__ import annotations

"""Native activation point for Dank Design layout enhancements.

This is not a startup guard. It is called by the normal /dank design command
registration path after the native design module is loaded.
"""

_PATCHED = False


def activate_public_design_enhancements() -> bool:
    global _PATCHED

    if _PATCHED:
        return True

    # Ensure the native design command module exists before enhancement modules
    # look for it in sys.modules.
    from stoney_verify.commands_ext import public_design_studio  # noqa: F401
    from stoney_verify.startup_guards import server_design_strict_layout_guard as strict_layout
    from stoney_verify.startup_guards import server_design_majority_layout_guard as majority_layout

    strict_ok = bool(strict_layout.apply())
    majority_ok = bool(majority_layout.apply())

    _PATCHED = bool(strict_ok and majority_ok)
    if _PATCHED:
        print("✅ public_design_enhancements active; strict/majority layout owned by native /dank design path")
    else:
        print(f"⚠️ public_design_enhancements partial strict={strict_ok} majority={majority_ok}")
    return _PATCHED


__all__ = ["activate_public_design_enhancements"]
