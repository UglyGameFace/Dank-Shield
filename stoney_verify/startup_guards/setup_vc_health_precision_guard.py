from __future__ import annotations

"""Startup compatibility shim for VC health precision.

The primary VC runtime health logic now lives in vc_request_setup_clarity.py.
This module remains importable so older startup paths do not crash during the
transition.
"""

_DONE = False


def apply() -> bool:
    global _DONE
    if _DONE:
        return True
    _DONE = True
    try:
        print("🩺 setup_vc_health_precision_guard compatibility shim active; source VC health owns central policy")
    except Exception:
        pass
    return True


apply()

__all__ = ["apply"]
