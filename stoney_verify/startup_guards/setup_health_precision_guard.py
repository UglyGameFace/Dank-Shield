from __future__ import annotations

"""Reduce setup health false warnings.

Names are not security. Dank Shield stores Discord IDs, so custom category names
should not produce warnings unless they also cause a real placement or
permission problem.
"""

from typing import Any

_DONE = False

_NAME_ONLY_WARNING_PARTS = (
    "category name looks unusual",
)


def _filter(lines: list[str]) -> list[str]:
    out: list[str] = []
    for line in lines:
        text = str(line)
        lowered = text.lower()
        if any(part in lowered for part in _NAME_ONLY_WARNING_PARTS):
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
        if not callable(original) or getattr(original, "_precision_filtered", False):
            return False

        def wrapped(guild: Any, cfg: Any):
            blockers, warnings, ok = original(guild, cfg)
            return blockers, _filter(list(warnings or [])), ok

        setattr(wrapped, "_precision_filtered", True)
        group._build_setup_health = wrapped
        _DONE = True
        print("🩺 setup_health_precision_guard active; name-only health warnings filtered")
        return True
    except Exception as exc:
        try:
            print(f"⚠️ setup_health_precision_guard failed: {exc!r}")
        except Exception:
            pass
        return False


apply()

__all__ = ["apply"]
