from __future__ import annotations

"""Retired compatibility shim for the former strict-layout runtime patch.

Strict matching and Gothic Clean compatibility defaults now live in
``server_design_plan_service``. This module remains importable for old callers,
but it never mutates the design service, theme catalog, protected names, or
semantic-match function.
"""

_VALIDATED = False


def apply() -> bool:
    global _VALIDATED
    if _VALIDATED:
        return True
    try:
        from stoney_verify.services import server_design_plan_service

        required = (
            callable(getattr(server_design_plan_service, "normalize_plan_options", None)),
            callable(getattr(server_design_plan_service, "build_saved_design_plan", None)),
        )
        _VALIDATED = all(required)
        if _VALIDATED:
            print("✅ server_design_strict_layout_guard retired; native plan service owns strict matching")
        return _VALIDATED
    except Exception as exc:
        try:
            print(f"⚠️ retired strict-layout compatibility check failed: {type(exc).__name__}: {exc}")
        except Exception:
            pass
        return False


__all__ = ["apply"]
