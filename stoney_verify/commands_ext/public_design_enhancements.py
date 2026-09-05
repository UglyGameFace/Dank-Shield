from __future__ import annotations

"""Compatibility hook for historical Dank Design enhancement activation.

Design behavior is now owned by the consolidated Studio and
``server_design_plan_service``. This function intentionally performs no imports
from startup_guards and does not replace live functions/classes.
"""

_ACTIVATED = False


def activate_public_design_enhancements() -> bool:
    global _ACTIVATED
    if _ACTIVATED:
        return True
    _ACTIVATED = True
    print("✅ public_design_enhancements compatibility hook active; native design services own behavior")
    return True


__all__ = ["activate_public_design_enhancements"]
