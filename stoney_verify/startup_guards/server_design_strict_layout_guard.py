from __future__ import annotations

"""Strict layout drift detection for Dank Design.

The core name builder has a smart semantic skip so it does not churn channels
that already have the selected font/base text. That skip was too loose for the
Design Studio consistency repair flow: a channel with the right fraktur text but
no separator, a thin separator, or a different separator could be counted as
"unchanged" and then skipped by Fix Mismatches.

This guard keeps the useful font/base skip, but only after the visible layout
pieces also match. Separator, leading emoji, and known category frame drift now
show up as changed items so the normal preview/apply/rollback path can repair
it.
"""

from collections.abc import Callable, Mapping
from dataclasses import replace
from typing import Any

_PATCHED = False
_ORIGINAL: Callable[..., bool] | None = None

_GOTHIC_CLEAN_SEPARATOR_ID = "pipe_spaced"

_FRAME_SIGNATURES: tuple[tuple[str, str, str], ...] = (
    ("premium_line", "✦────", "────✦"),
    ("heavy_line", "━━━", "━━━"),
    ("line", "───", "───"),
    ("top_box", "╭──", "──╮"),
    ("bottom_box", "╰──", "──╯"),
    ("box", "╔══", "══╗"),
    ("lenticular", "【", "】"),
    ("corner", "「", "」"),
    ("dreamy", "⋆｡°✩", "✩°｡⋆"),
)

# These are normal Discord channels from a visual-design standpoint. They are
# safe to rename because Dank Design only changes names and still creates a
# rollback snapshot. Keep destructive/operational ticket/archive channels
# protected unless the user explicitly overrides them later.
_RENAME_SAFE_DEFAULT_PROTECTED_NAMES = {
    "audit-log",
    "bot-commands",
    "logs",
    "mod-log",
    "setup",
    "staff",
    "staff-chat",
}

# Existing guild configs may already have locks saved with the old Gothic Clean
# separator. Normalize those at load time so Fix Mismatches reflects the new
# clear spaced-pipe Gothic layout immediately instead of reusing stale vertical
# bars that look doubled on Discord mobile.
_LEGACY_GOTHIC_SEPARATOR_IDS = {"bar_full", "bar_heavy", "bar_block", "bar_medium", "bar_bold"}
_GOTHIC_FONTS = {"fraktur", "bold_fraktur"}


def _clean_text(value: Any) -> str:
    try:
        return str(value or "").strip()
    except Exception:
        return ""


def _clean_key(value: Any) -> str:
    return _clean_text(value).lower().replace("-", "_")


def _frame_signature(studio: Any, value: Any) -> str:
    text = _clean_text(value)
    if not text:
        return ""
    try:
        text = studio.strip_known_unicode_fonts(text).strip()
    except Exception:
        pass
    for frame_id, prefix, suffix in _FRAME_SIGNATURES:
        if text.startswith(prefix) and text.endswith(suffix):
            return frame_id
    return ""


def _relax_visual_name_defaults(studio: Any) -> None:
    try:
        protected = getattr(studio, "DEFAULT_PROTECTED_NAMES", None)
        if isinstance(protected, set):
            protected.difference_update(_RENAME_SAFE_DEFAULT_PROTECTED_NAMES)
    except Exception:
        pass


def _ensure_spaced_pipe_separator(studio: Any) -> None:
    """Install the plain visible separator used by Gothic Clean.

    This deliberately uses ordinary ASCII with spaces because it renders clearly
    on Discord mobile and cannot be mistaken for a doubled unicode bar.
    """

    try:
        if _GOTHIC_CLEAN_SEPARATOR_ID in getattr(studio, "SEPARATORS_BY_ID", {}):
            return
        spec = studio.SeparatorSpec(
            _GOTHIC_CLEAN_SEPARATOR_ID,
            "Spaced Pipe",
            "Clean Vertical",
            " | ",
        )
        studio.SEPARATOR_LIBRARY = (spec, *tuple(getattr(studio, "SEPARATOR_LIBRARY", tuple()) or tuple()))
        studio.SEPARATORS_BY_ID = {separator.id: separator for separator in studio.SEPARATOR_LIBRARY}
    except Exception:
        pass


def _normalize_theme_defaults(studio: Any) -> None:
    """Use a plain spaced pipe for Gothic Clean.

    Discord mobile can make fullwidth/heavy unicode bars look like `||` beside
    fraktur letters. Gothic Clean should be clear first, decorative second.
    """

    if getattr(studio, "_DANK_GOTHIC_SPACED_PIPE_ACTIVE", False):
        return

    try:
        _ensure_spaced_pipe_separator(studio)
        themes = []
        changed = False
        for theme in tuple(getattr(studio, "THEMES", tuple()) or tuple()):
            if getattr(theme, "id", "") == "gothic_clean" and getattr(theme, "channel_separator", "") != _GOTHIC_CLEAN_SEPARATOR_ID:
                theme = replace(theme, channel_separator=_GOTHIC_CLEAN_SEPARATOR_ID)
                changed = True
            themes.append(theme)
        if changed:
            studio.THEMES = tuple(themes)
            studio.THEMES_BY_ID = {theme.id: theme for theme in studio.THEMES}
        studio._DANK_GOTHIC_SPACED_PIPE_ACTIVE = True
    except Exception:
        pass


def _lock_looks_gothic(lock: Mapping[str, Any], *, fallback_theme_id: str = "") -> bool:
    theme_id = _clean_key(lock.get("theme_id") or fallback_theme_id)
    font = _clean_key(lock.get("font"))
    return theme_id == "gothic_clean" or font in _GOTHIC_FONTS


def _normalize_gothic_lock(lock: Any, *, fallback_theme_id: str = "") -> Any:
    if not isinstance(lock, Mapping):
        return lock
    out = dict(lock)
    if _lock_looks_gothic(out, fallback_theme_id=fallback_theme_id):
        separator = _clean_key(out.get("separator_id"))
        if separator in _LEGACY_GOTHIC_SEPARATOR_IDS:
            out["separator_id"] = _GOTHIC_CLEAN_SEPARATOR_ID
    return out


def _normalize_gothic_design_options(options: Any) -> dict[str, Any]:
    out = dict(options) if isinstance(options, Mapping) else {}
    fallback_theme_id = _clean_key(out.get("theme_id")) or "gothic_clean"

    if fallback_theme_id == "gothic_clean" and _clean_key(out.get("separator_id")) in _LEGACY_GOTHIC_SEPARATOR_IDS:
        out["separator_id"] = _GOTHIC_CLEAN_SEPARATOR_ID

    global_lock = _normalize_gothic_lock(out.get("format_lock_global"), fallback_theme_id=fallback_theme_id)
    if isinstance(global_lock, Mapping):
        out["format_lock_global"] = dict(global_lock)

    for key in ("category_format_locks", "channel_format_locks"):
        locks = out.get(key)
        if not isinstance(locks, Mapping):
            continue
        out[key] = {
            str(lock_id): _normalize_gothic_lock(lock, fallback_theme_id=fallback_theme_id)
            for lock_id, lock in dict(locks).items()
        }

    return out


def _patch_command_guard_options() -> None:
    try:
        import sys

        for module_name in (
            "stoney_verify.commands_ext.public_design_studio",
            "stoney_verify.startup_guards.server_design_studio_command_guard",
        ):
            command_guard = sys.modules.get(module_name)
            if command_guard is None or getattr(command_guard, "_DANK_GOTHIC_LOCK_NORMALIZER_ACTIVE", False):
                continue

            original_load = getattr(command_guard, "_load_design_options", None)
            original_save = getattr(command_guard, "_save_design_options", None)
            if not callable(original_load) or not callable(original_save):
                continue

            async def _load_design_options_normalized(guild_id: int, _original_load=original_load) -> dict[str, Any]:
                return _normalize_gothic_design_options(await _original_load(guild_id))

            async def _save_design_options_normalized(guild_id: int, options: Mapping[str, Any], _original_save=original_save) -> None:
                await _original_save(guild_id, _normalize_gothic_design_options(options))

            command_guard._load_design_options = _load_design_options_normalized
            command_guard._save_design_options = _save_design_options_normalized
            command_guard._DANK_GOTHIC_LOCK_NORMALIZER_ACTIVE = True
    except Exception:
        pass


def _make_strict_match(original: Callable[..., bool], studio: Any) -> Callable[..., bool]:
    def _strict_already_semantically_matches_design(
        before: str,
        *,
        base: str,
        font: str,
        expected_after: str,
    ) -> bool:
        if not original(before, base=base, font=font, expected_after=expected_after):
            return False

        try:
            before_text = _clean_text(before)
            expected_text = _clean_text(expected_after)
            before_parsed = studio.parse_channel_name(before_text)
            expected_parsed = studio.parse_channel_name(expected_text)

            if _clean_text(before_parsed.get("emoji")) != _clean_text(expected_parsed.get("emoji")):
                return False
            if _clean_text(before_parsed.get("separator")) != _clean_text(expected_parsed.get("separator")):
                return False
            if _frame_signature(studio, before_text) != _frame_signature(studio, expected_text):
                return False
        except Exception:
            # If layout parsing ever becomes uncertain, prefer a safe rename plan
            # over silently calling an inconsistent channel "unchanged".
            return False

        return True

    return _strict_already_semantically_matches_design


def apply() -> bool:
    global _PATCHED, _ORIGINAL
    if _PATCHED:
        _patch_command_guard_options()
        return True

    try:
        from stoney_verify.services import server_design_studio as studio

        _relax_visual_name_defaults(studio)
        _normalize_theme_defaults(studio)

        original = getattr(studio, "_already_semantically_matches_design", None)
        if not callable(original):
            return False

        if not getattr(studio, "_DANK_STRICT_LAYOUT_MATCH_ACTIVE", False):
            _ORIGINAL = original
            studio._already_semantically_matches_design = _make_strict_match(original, studio)  # type: ignore[attr-defined]
            studio._DANK_STRICT_LAYOUT_MATCH_ACTIVE = True

        _PATCHED = True
        _patch_command_guard_options()
        print("✅ server_design_strict_layout_guard active; Gothic Clean uses clear spaced pipe and strict drift detection")
        return True
    except Exception as exc:
        try:
            print(f"⚠️ server_design_strict_layout_guard failed: {type(exc).__name__}: {exc}")
        except Exception:
            pass
        return False


__all__ = ["apply"]
