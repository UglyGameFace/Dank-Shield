from __future__ import annotations

"""Align VC health with staff-controlled VC repair baseline.

View-only access to the VC verification channel is not the dangerous part.
Free Connect access is. This filters the older blocker that complained about
@everyone being able to view after repair had already removed Connect.
"""

from typing import Any

_DONE = False


def _filter_blockers(blockers: list[str], warnings: list[str], ok: list[str]) -> list[str]:
    out: list[str] = []
    for line in blockers:
        text = str(line)
        low = text.lower()
        if (
            "vc verification channel" in low
            and "is not locked" in low
            and "@everyone" in low
            and "can view" in low
            and "connect" not in low
        ):
            warnings.append(
                text.replace("Lock it in setup before testing VC verify.", "View-only access is allowed for onboarding visibility; Connect must remain locked.")
            )
            ok.append("VC verification channel has @everyone view-only access; connect remains the safety-critical lock.")
            continue
        out.append(line)
    return out


def apply() -> bool:
    global _DONE
    if _DONE:
        return True
    try:
        from stoney_verify.commands_ext import public_setup_group as group
        original = getattr(group, "_build_setup_health", None)
        if not callable(original) or getattr(original, "_vc_health_precision_wrapped", False):
            return False

        def wrapped(guild: Any, cfg: Any):
            blockers, warnings, ok = original(guild, cfg)
            warnings = list(warnings or [])
            ok = list(ok or [])
            blockers = _filter_blockers(list(blockers or []), warnings, ok)
            return blockers, warnings, ok

        setattr(wrapped, "_vc_health_precision_wrapped", True)
        group._build_setup_health = wrapped
        try:
            from stoney_verify.commands_ext import public_setup_solid as solid
            solid._build_setup_health = wrapped
        except Exception:
            pass
        _DONE = True
        print("🩺 setup_vc_health_precision_guard active; VC view-only access is non-blocking")
        return True
    except Exception as exc:
        try:
            print(f"⚠️ setup_vc_health_precision_guard failed: {exc!r}")
        except Exception:
            pass
        return False


apply()

__all__ = ["apply"]
