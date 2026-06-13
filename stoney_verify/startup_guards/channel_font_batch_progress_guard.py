from __future__ import annotations

from typing import Any

_PATCHED = False


def _to_int(value: Any) -> int:
    try:
        return int(str(value).strip())
    except Exception:
        return 0


def _ids(rows: list[dict[str, Any]]) -> set[str]:
    return {str(row.get("channel_id")) for row in rows if row.get("channel_id")}


def apply() -> bool:
    global _PATCHED
    if _PATCHED:
        return True
    try:
        from stoney_verify.startup_guards import channel_font_rename_queue_guard as base

        original = getattr(base, "_apply_batch", None)
        if not callable(original) or getattr(original, "_progress_wrapped", False):
            return False

        async def wrapped(interaction: Any, plan: list[dict[str, Any]]) -> dict[str, Any]:
            result = await original(interaction, plan)
            guild = getattr(interaction, "guild", None)
            user = getattr(interaction, "user", None)
            if guild is None or user is None:
                return result
            count = _to_int(result.get("attempted"))
            if count <= 0:
                return result
            touched = _ids(list(plan[:count]))
            remaining = list(result.get("remaining_plan") or base._remaining_plan(int(guild.id), int(user.id)))
            next_plan = [row for row in remaining if str(row.get("channel_id")) not in touched]
            if len(next_plan) != len(remaining):
                base._set_remaining_plan(int(guild.id), int(user.id), next_plan)
                result["remaining_plan"] = next_plan
                result["remaining"] = len(next_plan)
            return result

        setattr(wrapped, "_progress_wrapped", True)
        base._apply_batch = wrapped
        _PATCHED = True
        print("🔤 channel_font_batch_progress_guard active; font batches advance after attempted rows")
        return True
    except Exception as exc:
        try:
            print(f"⚠️ channel_font_batch_progress_guard failed: {exc!r}")
        except Exception:
            pass
        return False


apply()

__all__ = ["apply"]
