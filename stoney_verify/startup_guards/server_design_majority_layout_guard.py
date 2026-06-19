from __future__ import annotations

"""Majority-layout repair for Dank Design.

When a server was hand-built, Fix Mismatches should learn the common visible
layout from the current channel list and repair outliers to that layout instead
of forcing a generic draft.
"""

from collections import Counter
from collections.abc import Mapping
import re
from typing import Any

_PATCHED = False
_PIPE_ID = "pipe_spaced"
_PIPE_TOKENS = {"|", "｜", "│", "┃", "❘", "❙", "❚", "┆", "┊", "╏", "╎", "︱", "〢"}
_FRAME_SIGS = (
    ("premium_line", "✦────", "────✦"),
    ("heavy_line", "━━━", "━━━"),
    ("line", "───", "───"),
    ("top_box", "╭──", "──╮"),
    ("bottom_box", "╰──", "──╯"),
    ("box", "╔══", "══╗"),
    ("lenticular", "【", "】"),
    ("corner", "「", "」"),
)


def _text(value: Any) -> str:
    try:
        return str(value or "").strip()
    except Exception:
        return ""


def _key(value: Any) -> str:
    return _text(value).lower().replace("-", "_")


def _ensure_pipe(studio: Any) -> None:
    if _PIPE_ID in getattr(studio, "SEPARATORS_BY_ID", {}):
        return
    spec = studio.SeparatorSpec(_PIPE_ID, "Spaced Pipe", "Clean Vertical", " | ")
    studio.SEPARATOR_LIBRARY = (spec, *tuple(getattr(studio, "SEPARATOR_LIBRARY", tuple()) or tuple()))
    studio.SEPARATORS_BY_ID = {item.id: item for item in studio.SEPARATOR_LIBRARY}


def _has_locks(options: Mapping[str, Any]) -> bool:
    global_lock = options.get("format_lock_global")
    if isinstance(global_lock, Mapping) and bool(global_lock.get("enabled")):
        return True
    for name in ("category_format_locks", "channel_format_locks"):
        locks = options.get(name)
        if isinstance(locks, Mapping) and bool(locks):
            return True
    return False


def _sep_id_for_token(studio: Any, raw: Any) -> str:
    token = _text(raw)
    if not token:
        return ""
    compact = re.sub(r"\s+", "", token)
    if "|" in compact or any(mark in compact for mark in _PIPE_TOKENS):
        _ensure_pipe(studio)
        return _PIPE_ID
    stripped = token.strip()
    for sep in tuple(getattr(studio, "SEPARATOR_LIBRARY", tuple()) or tuple()):
        if _text(getattr(sep, "value", "")).strip() == stripped:
            return _text(getattr(sep, "id", ""))
    return ""


def _seen_separator(studio: Any, name: Any) -> str:
    raw_name = _text(name)
    if not raw_name:
        return ""
    try:
        parsed = studio.parse_channel_name(raw_name)
        parsed_sep = _sep_id_for_token(studio, parsed.get("separator"))
        if parsed_sep:
            return parsed_sep

        visible = studio.strip_known_unicode_fonts(raw_name)
        emoji = _text(parsed.get("emoji"))
        if emoji and visible.startswith(emoji):
            visible = visible[len(emoji):]
        visible = visible.lstrip()
        match = re.match(r"^([^A-Za-z0-9\s]+(?:\s*[^A-Za-z0-9\s]+)*)", visible)
        return _sep_id_for_token(studio, match.group(1) if match else "")
    except Exception:
        return ""


def _frame_id(studio: Any, name: Any) -> str:
    text = _text(name)
    if not text:
        return ""
    try:
        text = studio.strip_known_unicode_fonts(text).strip()
    except Exception:
        pass
    for frame_id, prefix, suffix in _FRAME_SIGS:
        if text.startswith(prefix) and text.endswith(suffix):
            return frame_id
    return "plain"


def _top(counter: Counter[str]) -> str:
    if not counter:
        return ""
    value, count = counter.most_common(1)[0]
    return value if count >= 2 else ""


def _infer_options(command_guard: Any, studio: Any, guild: Any, options: Mapping[str, Any]) -> dict[str, Any]:
    out = dict(options)
    if _has_locks(out):
        return out

    separators: Counter[str] = Counter()
    frames: Counter[str] = Counter()
    styled = 0
    normal = 0

    for channel in list(command_guard._editable_channels(guild) or []):
        kind = command_guard._kind(channel)
        name = _text(getattr(channel, "name", ""))
        if not name:
            continue
        try:
            parsed = studio.parse_channel_name(name, kind="category" if kind == "category" else "text")
            styled += 1 if parsed.get("styled_unicode_name") else 0
            normal += 0 if parsed.get("styled_unicode_name") else 1
        except Exception:
            pass
        if kind == "category":
            frame_id = _frame_id(studio, name)
            if frame_id:
                frames[frame_id] += 1
        else:
            sep_id = _seen_separator(studio, name)
            if sep_id:
                separators[sep_id] += 1

    sep_id = _top(separators)
    frame_id = _top(frames)
    if sep_id:
        out["separator_id"] = sep_id
    if frame_id:
        out["category_frame_id"] = frame_id
    if styled > normal:
        out.setdefault("font", "fraktur")
        try:
            out["strength"] = max(4, int(out.get("strength") or 4))
        except Exception:
            out["strength"] = 4
    if sep_id or frame_id:
        out["__majority_layout_inferred"] = True
    return out


def apply() -> bool:
    global _PATCHED
    if _PATCHED:
        return True
    try:
        import sys
        from stoney_verify.services import server_design_studio as studio

        _ensure_pipe(studio)
        command_guard = sys.modules.get("stoney_verify.startup_guards.server_design_studio_command_guard")
        if command_guard is None:
            return False
        if getattr(command_guard, "_DANK_MAJORITY_LAYOUT_PLAN_ACTIVE", False):
            _PATCHED = True
            return True
        original = getattr(command_guard, "build_design_plan", None)
        if not callable(original):
            return False

        async def _build_design_plan_with_majority(guild: Any, options: Mapping[str, Any]) -> list[dict[str, Any]]:
            inferred = _infer_options(command_guard, studio, guild, options)
            return await original(guild, inferred)

        command_guard.build_design_plan = _build_design_plan_with_majority
        command_guard._DANK_MAJORITY_LAYOUT_PLAN_ACTIVE = True
        _PATCHED = True
        print("✅ server_design_majority_layout_guard active; Fix Mismatches copies the majority visible layout")
        return True
    except Exception as exc:
        try:
            print(f"⚠️ server_design_majority_layout_guard failed: {type(exc).__name__}: {exc}")
        except Exception:
            pass
        return False


apply()

__all__ = ["apply"]
