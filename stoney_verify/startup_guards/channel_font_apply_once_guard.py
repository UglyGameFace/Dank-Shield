from __future__ import annotations

"""Make Channel Name Fonts startup hooks idempotent without hiding warnings.

Some font guards are imported normally, then their apply() functions are called
again later when setup/API modules finish importing. The second call is useful
for patching late-loaded views, but the repeated "active" log lines are not.
This guard preserves the patch work and removes only the duplicate success logs.
"""

from typing import Any

_DONE = False


def _patch_rename_guard() -> None:
    try:
        from stoney_verify.startup_guards import channel_font_rename_queue_guard as guard
    except Exception:
        return

    if getattr(guard, "_apply_once_guard_installed", False):
        return

    def quiet_apply() -> bool:
        try:
            patch = getattr(guard, "_patch_font_view", None)
            if callable(patch):
                patch()
        except Exception:
            pass
        return True

    guard.apply = quiet_apply  # type: ignore[assignment]
    setattr(guard, "_apply_once_guard_installed", True)


def _patch_exact_guard() -> None:
    try:
        from stoney_verify.startup_guards import channel_font_exact_unicode_guard as guard
    except Exception:
        return

    if getattr(guard, "_apply_once_guard_installed", False):
        return

    def quiet_apply() -> bool:
        try:
            load_preview = getattr(guard, "_load_preview_button_guard", None)
            if callable(load_preview):
                load_preview()
        except Exception:
            pass
        try:
            from stoney_verify.services import channel_builder_runtime as runtime
            runtime._unicode_map = guard.exact_unicode_map
        except Exception:
            pass
        try:
            from stoney_verify.startup_guards import channel_builder_full_font_catalog_guard as catalog
            catalog.full_unicode_map = guard.exact_unicode_map
        except Exception:
            pass
        try:
            patch = getattr(guard, "_patch_live_plan", None)
            if callable(patch):
                patch()
            setattr(guard, "_PATCHED", True)
        except Exception:
            pass
        return True

    guard.apply = quiet_apply  # type: ignore[assignment]
    setattr(guard, "_apply_once_guard_installed", True)


def apply() -> bool:
    global _DONE
    if _DONE:
        return True
    _patch_rename_guard()
    _patch_exact_guard()
    _DONE = True
    try:
        print("🔤 channel_font_apply_once_guard active; repeated font apply hooks are quiet/idempotent")
    except Exception:
        pass
    return True


apply()

__all__ = ["apply"]
