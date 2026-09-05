from __future__ import annotations

"""Retired compatibility shim for the former live-majority runtime bridge.

Category-aware Smart Auto-Detect, annotation, saved-rule precedence, repair
confidence, and public Review / Repair UI are now explicit native services. This
module intentionally does not replace ``build_design_plan``, embeds, or views.
"""

_VALIDATED = False


def apply() -> bool:
    global _VALIDATED
    if _VALIDATED:
        return True
    try:
        from stoney_verify.services import server_design_majority_layout as majority
        from stoney_verify.services import server_design_plan_service as plan_service

        required = (
            callable(getattr(majority, "build_category_aware_options", None)),
            callable(getattr(majority, "annotate_category_aware_plan_items", None)),
            callable(getattr(plan_service, "build_drift_repair_plan", None)),
        )
        _VALIDATED = all(required)
        if _VALIDATED:
            print("✅ server_design_majority_layout_guard retired; native Review / Repair owns Smart Auto-Detect")
        return _VALIDATED
    except Exception as exc:
        try:
            print(f"⚠️ retired majority-layout compatibility check failed: {type(exc).__name__}: {exc}")
        except Exception:
            pass
        return False


__all__ = ["apply"]
