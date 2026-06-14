from __future__ import annotations

"""Clean labels in setup permission repair previews.

Discord's default role can render as @@everyone when treated like a mention.
Show plain labels for global roles so owners understand the exact target.
"""

from typing import Any

_DONE = False


def _label(target: Any) -> str:
    try:
        if getattr(target, "is_default", lambda: False)():
            return "`@everyone`"
    except Exception:
        pass
    try:
        name = str(getattr(target, "name", "") or "").strip()
        if name == "@everyone":
            return "`@everyone`"
        if name:
            return f"`@{name}`" if getattr(target, "__class__", object).__name__.endswith("Role") else f"`{name}`"
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
        print("🩺 setup_permission_label_precision_guard active; repair preview labels are unambiguous")
        return True
    except Exception as exc:
        try:
            print(f"⚠️ setup_permission_label_precision_guard failed: {exc!r}")
        except Exception:
            pass
        return False


apply()

__all__ = ["apply"]
