from __future__ import annotations

"""Bridge setup permission repair preview labels to central policy."""

from typing import Any

_DONE = False


def _label(target: Any) -> str:
    try:
        from stoney_verify.services.setup_permission_policy import role_label
        return f"`{role_label(target)}`"
    except Exception:
        pass
    try:
        mention = getattr(target, "mention", None)
        if mention:
            return str(mention)
    except Exception:
        pass
    return "`unknown`"


def apply() -> bool:
    global _DONE
    if _DONE:
        return True
    try:
        from stoney_verify.startup_guards import setup_permission_repair_guard as repair
        repair._target_label = _label
        _DONE = True
        print("🩺 setup_permission_label_precision_guard active; repair preview labels use central policy")
        return True
    except Exception as exc:
        try:
            print(f"⚠️ setup_permission_label_precision_guard failed: {exc!r}")
        except Exception:
            pass
        return False


apply()

__all__ = ["apply"]
