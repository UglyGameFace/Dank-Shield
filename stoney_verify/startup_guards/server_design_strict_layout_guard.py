from __future__ import annotations

"""Strict layout drift detection for Dank Design.

The core name builder has a smart semantic skip so it does not churn channels
that already have the selected font/base text. That skip was too loose for the
Design Studio consistency repair flow: a channel with the right fraktur text but
no separator, a thin separator, or a different separator could be counted as
"unchanged" and then skipped by Fix Mismatches.

This helper is activated by the native /dank design registration path. It never
owns startup registration and it only patches the service instance exposed by
the already-loaded native public_design_studio module.
"""

from collections.abc import Callable
from dataclasses import replace
import sys
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


def _clean_text(value: Any) -> str:
    try:
        return str(value or "").strip()
    except Exception:
        return ""


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
    """Install the plain visible separator used by Gothic Clean."""

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
    """Use a plain spaced pipe as the Gothic Clean default only.

    This changes the theme definition, not saved owner-approved locks.
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


def _native_design_module() -> Any | None:
    """Return the already-loaded native Dank Design module.

    The native registration path loads this module before activating helpers.
    Refusing to self-import here keeps ownership out of startup/compat layers.
    """

    return sys.modules.get("stoney_verify.commands_ext.public_design_studio")


def apply() -> bool:
    global _PATCHED, _ORIGINAL
    if _PATCHED:
        return True

    try:
        command_guard = _native_design_module()
        if command_guard is None:
            return False

        studio = getattr(command_guard, "studio", None)
        if studio is None:
            return False

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
        print("✅ server_design_strict_layout_guard active via native public_design_studio")
        return True
    except Exception as exc:
        try:
            print(f"⚠️ server_design_strict_layout_guard failed: {type(exc).__name__}: {exc}")
        except Exception:
            pass
        return False


__all__ = ["apply"]
