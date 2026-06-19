from __future__ import annotations

"""Live-server majority layout detection for Dank Design.

This module is intentionally pure: it reads already-visible channel/category names,
infers the dominant visual layout, and returns ordinary Server Design Studio
options. It does not touch Discord permissions, roles, topics, ticket settings, or
setup config.
"""

from collections import Counter
from collections.abc import Iterable, Mapping
import re
import unicodedata
from typing import Any

VERTICAL_SEPARATOR_TOKENS: tuple[str, ...] = ("|", "｜", "│", "┃", "❘", "❙", "❚")

_TOKEN_BASE_IDS: dict[str, str] = {
    "｜": "bar_full",
    "│": "bar_thin",
    "┃": "bar_heavy",
    "❘": "bar_medium",
    "❙": "bar_bold",
    "❚": "bar_block",
    "|": "pipe_compact",
}

_FRAME_CHARS = "─━═-╭╮╰╯╔╗【】「」✦⋆｡°✩"
_FRAME_DYNAMIC_PREFIX = "majority_"


def _text(value: Any, default: str = "") -> str:
    try:
        if value is None:
            return default
        text = str(value).strip()
        return text if text else default
    except Exception:
        return default


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or isinstance(value, bool):
            return int(default)
        return int(str(value).strip() or default)
    except Exception:
        return int(default)


def clean_design_text(value: Any) -> str:
    """Convert visible newline artifacts in Dank Design copy to real newlines."""

    text = _text(value)
    if not text:
        return text
    for bad in ("\\\\n", "\\\\N", "\\n", "\\N", "\\/n", "\\/N", "/n", "/N"):
        text = text.replace(bad, "\n")
    while "\n\n\n" in text:
        text = text.replace("\n\n\n", "\n\n")
    return text.strip()


def _strip_invisible_with(studio: Any, value: Any) -> str:
    try:
        return studio.strip_invisible(value)
    except Exception:
        return _text(value)


def _strip_fonts_with(studio: Any, value: Any) -> str:
    try:
        return studio.strip_known_unicode_fonts(value)
    except Exception:
        return _strip_invisible_with(studio, value)


def _parse_with(studio: Any, name: str, *, kind: str = "text") -> dict[str, Any]:
    try:
        parsed = studio.parse_channel_name(name, kind=kind)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def _separator_values_for(studio: Any) -> tuple[str, ...]:
    values = set(VERTICAL_SEPARATOR_TOKENS)
    try:
        values.update(str(spec.value) for spec in tuple(getattr(studio, "SEPARATOR_LIBRARY", tuple()) or tuple()) if getattr(spec, "value", ""))
    except Exception:
        pass
    return tuple(sorted((value for value in values if value), key=len, reverse=True))


def _starts_with_separator(text: str, separators: tuple[str, ...]) -> bool:
    return any(text.startswith(sep) for sep in separators if sep)


def _is_emojiish_not_separator(ch: str) -> bool:
    if not ch or ch in VERTICAL_SEPARATOR_TOKENS:
        return False
    cp = ord(ch)
    return unicodedata.category(ch) == "So" or 0x1F000 <= cp <= 0x1FAFF or 0x2600 <= cp <= 0x27BF


def _split_leading_emoji(studio: Any, value: Any, *, preserve_remainder_spaces: bool) -> tuple[str, str]:
    text = _strip_invisible_with(studio, value)
    separators = _separator_values_for(studio)
    bracket_match = re.match(r"^([「『〔【〖꒰]\s*[^\w\s-]+\s*[」』〕】〗꒱])\s*(.*)$", text)
    if bracket_match:
        icon = re.sub(r"[「『〔【〖꒰」』〕】〗꒱\s]", "", bracket_match.group(1))
        remainder = bracket_match.group(2)
        return icon, remainder if preserve_remainder_spaces else remainder.strip()

    chars = list(text)
    icon_chars: list[str] = []
    index = 0
    variation = chr(0xFE0F)
    joiner = chr(0x200D)
    while index < len(chars):
        remaining = "".join(chars[index:])
        if _starts_with_separator(remaining, separators):
            break
        ch = chars[index]
        if _is_emojiish_not_separator(ch) or (icon_chars and ch in {variation, joiner}):
            icon_chars.append(ch)
            index += 1
            continue
        break

    remainder = "".join(chars[index:])
    return "".join(icon_chars).strip(), remainder if preserve_remainder_spaces else remainder.strip()


def install_separator_safe_parser(studio: Any) -> None:
    """Keep visual separator bars from being swallowed as part of an emoji.

    The existing parser treats Unicode symbols as emoji-ish. Box/vertical bar
    separators are also Unicode symbols, so names such as `🎭│profile` can be
    misread as emoji=`🎭│` with no separator. The repair path installs this
    parser before building the rename plan so separator drift can be detected and
    repaired accurately.
    """

    if getattr(studio, "_DANK_SEPARATOR_SAFE_ICON_PARSE_ACTIVE", False):
        return

    def _separator_safe_strip_leading_icon(value: str) -> tuple[str, str]:
        return _split_leading_emoji(studio, value, preserve_remainder_spaces=False)

    try:
        studio._strip_leading_icon = _separator_safe_strip_leading_icon  # type: ignore[attr-defined]
        studio._DANK_SEPARATOR_SAFE_ICON_PARSE_ACTIVE = True
    except Exception:
        pass


def _remove_leading_emoji(studio: Any, raw: str, *, kind: str = "text") -> tuple[str, str]:
    return _split_leading_emoji(studio, raw, preserve_remainder_spaces=True)


def _separator_label(token: str, spacing: str) -> str:
    if spacing == "none" or not token:
        return "no separator"
    if spacing == "spaced":
        return f"emoji {token} name"
    if spacing == "compact":
        return f"emoji{token}name"
    if spacing == "partial":
        return f"mixed spacing around {token}"
    if spacing == "doubled":
        return f"doubled {token} separator"
    return "mixed/unknown"


def detect_channel_separator(studio: Any, name: Any) -> dict[str, Any]:
    """Detect the exact visible separator token and spacing in a channel name."""

    raw = _strip_invisible_with(studio, name)
    emoji, remainder = _remove_leading_emoji(studio, raw, kind="text")
    left_spaces = len(remainder) - len(remainder.lstrip(" "))
    scan = remainder.lstrip(" ")

    for token in VERTICAL_SEPARATOR_TOKENS:
        if not scan.startswith(token):
            continue
        after_token = scan[len(token) :]
        right_spaces = len(after_token) - len(after_token.lstrip(" "))
        after_visible = after_token.lstrip(" ")
        doubled = after_visible.startswith(token) or scan.startswith(token + token)
        if doubled:
            spacing = "doubled"
        elif left_spaces and right_spaces:
            spacing = "spaced"
        elif not left_spaces and not right_spaces:
            spacing = "compact"
        else:
            spacing = "partial"
        return {
            "emoji": emoji,
            "has_leading_emoji": bool(emoji),
            "token": token,
            "spacing": spacing,
            "label": _separator_label(token, spacing),
            "missing": False,
            "doubled": doubled,
            "separator_in_name_text": bool(any(mark in after_visible for mark in VERTICAL_SEPARATOR_TOKENS)),
        }

    return {
        "emoji": emoji,
        "has_leading_emoji": bool(emoji),
        "token": "",
        "spacing": "none",
        "label": "no separator",
        "missing": True,
        "doubled": False,
        "separator_in_name_text": bool(any(mark in scan for mark in VERTICAL_SEPARATOR_TOKENS)),
    }


def _font_compare_text(value: str) -> str:
    for token in VERTICAL_SEPARATOR_TOKENS:
        value = value.replace(token, "")
    value = re.sub(rf"[{re.escape(_FRAME_CHARS)}\s]+", "", value)
    return value


def detect_font_style(studio: Any, name: Any) -> str:
    raw = _strip_invisible_with(studio, name)
    plain = _strip_fonts_with(studio, raw)
    return "styled" if _font_compare_text(raw) != _font_compare_text(plain) else "normal"


def _frame_label(frame: Mapping[str, Any]) -> str:
    kind = _text(frame.get("kind"), "plain")
    if kind == "plain":
        return "plain category names"
    if kind == "line":
        return f"{'─' * _safe_int(frame.get('count'), 3)} name {'─' * _safe_int(frame.get('count'), 3)}"
    if kind == "heavy_line":
        return f"{'━' * _safe_int(frame.get('count'), 3)} name {'━' * _safe_int(frame.get('count'), 3)}"
    if kind == "dash_line":
        return f"{'-' * _safe_int(frame.get('count'), 3)} name {'-' * _safe_int(frame.get('count'), 3)}"
    if kind == "lenticular":
        return "【 name 】"
    if kind == "corner":
        return "「 name 」"
    return kind.replace("_", " ")


def detect_category_frame(studio: Any, name: Any) -> dict[str, Any]:
    raw = _strip_fonts_with(studio, name).strip()
    if not raw:
        return {"kind": "plain", "id": "plain", "label": "plain category names", "count": 0}

    for open_char, kind, default_id in (("─", "line", "line"), ("━", "heavy_line", "heavy_line"), ("-", "dash_line", "")):
        match = re.match(rf"^({re.escape(open_char)}{{2,}})\s*(.+?)\s*({re.escape(open_char)}{{2,}})$", raw)
        if not match:
            continue
        count = min(len(match.group(1)), len(match.group(3)))
        frame = {
            "kind": kind,
            "id": default_id if default_id and count == 3 else f"{_FRAME_DYNAMIC_PREFIX}{kind}_{count}",
            "label": "",
            "count": count,
            "char": open_char,
        }
        frame["label"] = _frame_label(frame)
        return frame

    known = (
        ("premium_line", "✦────", "────✦"),
        ("top_box", "╭──", "──╮"),
        ("bottom_box", "╰──", "──╯"),
        ("box", "╔══", "══╗"),
        ("lenticular", "【", "】"),
        ("corner", "「", "」"),
    )
    for frame_id, prefix, suffix in known:
        if raw.startswith(prefix) and raw.endswith(suffix):
            return {"kind": frame_id, "id": frame_id, "label": _frame_label({"kind": frame_id}), "count": 0}

    return {"kind": "plain", "id": "plain", "label": "plain category names", "count": 0}


def _top_majority(counter: Counter[Any], *, total: int) -> tuple[Any, int, bool]:
    if not counter:
        return None, 0, False
    winners = counter.most_common()
    value, count = winners[0]
    tied = len(winners) > 1 and winners[1][1] == count
    return value, count, bool(tied or count <= max(1, total // 2))


def _record_name(record: Any) -> str:
    if isinstance(record, Mapping):
        return _text(record.get("name"))
    return _text(getattr(record, "name", ""))


def _record_kind(record: Any) -> str:
    if isinstance(record, Mapping):
        return _text(record.get("kind"), "text").lower()
    return _text(getattr(record, "kind", "text"), "text").lower()


def _separator_key(parts: Mapping[str, Any]) -> tuple[str, str]:
    spacing = _text(parts.get("spacing"), "none")
    token = _text(parts.get("token"))
    if spacing in {"partial", "doubled"}:
        return ("", "mixed")
    if spacing == "none" or not token:
        return ("", "none")
    return (token, spacing)


def infer_live_majority_layout(studio: Any, records: Iterable[Any]) -> dict[str, Any]:
    """Infer the majority visible layout from live category/channel names."""

    install_separator_safe_parser(studio)
    separator_counts: Counter[tuple[str, str]] = Counter()
    separator_examples: dict[tuple[str, str], dict[str, Any]] = {}
    frame_counts: Counter[str] = Counter()
    frame_examples: dict[str, dict[str, Any]] = {}
    font_counts: Counter[str] = Counter()
    emoji_counts: Counter[bool] = Counter()
    issue_counts: Counter[str] = Counter()
    text_total = 0
    category_total = 0

    for record in records:
        name = _record_name(record)
        kind = _record_kind(record)
        if not name:
            continue
        font_counts[detect_font_style(studio, name)] += 1
        if kind == "category":
            category_total += 1
            frame = detect_category_frame(studio, name)
            frame_id = _text(frame.get("id"), "plain")
            frame_counts[frame_id] += 1
            frame_examples.setdefault(frame_id, dict(frame))
            continue

        text_total += 1
        sep = detect_channel_separator(studio, name)
        emoji_counts[bool(sep.get("has_leading_emoji"))] += 1
        key = _separator_key(sep)
        if key == ("", "mixed"):
            issue_counts[_text(sep.get("spacing"), "mixed")] += 1
            if sep.get("doubled"):
                issue_counts["doubled_separator"] += 1
            if sep.get("separator_in_name_text"):
                issue_counts["separator_in_name_text"] += 1
            continue
        separator_counts[key] += 1
        separator_examples.setdefault(key, dict(sep))
        if sep.get("separator_in_name_text"):
            issue_counts["separator_in_name_text"] += 1

    sep_key, sep_count, sep_mixed = _top_majority(separator_counts, total=text_total)
    frame_id, frame_count, frame_mixed = _top_majority(frame_counts, total=category_total)
    font_id, font_count, font_mixed = _top_majority(font_counts, total=sum(font_counts.values()))
    emoji_enabled, emoji_count, emoji_mixed = _top_majority(emoji_counts, total=text_total)

    separator = separator_examples.get(sep_key, {"token": "", "spacing": "unknown", "label": "mixed/unknown"}) if sep_key else {"token": "", "spacing": "unknown", "label": "mixed/unknown"}
    if sep_mixed:
        separator = {**separator, "spacing": "mixed/unknown", "label": "mixed/unknown"}
    frame = frame_examples.get(frame_id, {"id": "", "kind": "unknown", "label": "mixed/unknown"}) if frame_id else {"id": "", "kind": "unknown", "label": "mixed/unknown"}
    if frame_mixed:
        frame = {**frame, "id": "", "kind": "unknown", "label": "mixed/unknown"}

    return {
        "text_total": text_total,
        "category_total": category_total,
        "separator": {**separator, "count": sep_count, "mixed": sep_mixed},
        "category_frame": {**frame, "count": frame_count, "mixed": frame_mixed},
        "font": {
            "id": "fraktur" if font_id == "styled" and not font_mixed else ("normal" if font_id == "normal" and not font_mixed else ""),
            "label": "styled/fraktur" if font_id == "styled" and not font_mixed else ("normal text" if font_id == "normal" and not font_mixed else "mixed/unknown"),
            "count": font_count,
            "mixed": font_mixed,
        },
        "leading_emoji": {
            "enabled": bool(emoji_enabled) if not emoji_mixed and text_total else None,
            "label": "yes" if bool(emoji_enabled) and not emoji_mixed else ("no" if emoji_enabled is False and not emoji_mixed else "mixed/unknown"),
            "count": emoji_count,
            "mixed": emoji_mixed,
        },
        "issues": dict(issue_counts),
    }


def _separator_spec_exists(studio: Any, sep_id: str, value: str) -> bool:
    spec = getattr(studio, "SEPARATORS_BY_ID", {}).get(sep_id)
    return bool(spec and _text(getattr(spec, "value", "")) == value)


def ensure_separator_spec(studio: Any, token: str, spacing: str) -> str:
    """Ensure the selected visible separator exists in the runtime library."""

    if spacing == "none" or not token:
        return "none"

    value = f" {token} " if spacing == "spaced" else token
    for spec in tuple(getattr(studio, "SEPARATOR_LIBRARY", tuple()) or tuple()):
        if _text(getattr(spec, "value", "")) == value:
            return _text(getattr(spec, "id", ""))

    base_id = _TOKEN_BASE_IDS.get(token, "vertical")
    if spacing == "compact" and base_id != "pipe_compact" and base_id in getattr(studio, "SEPARATORS_BY_ID", {}):
        return base_id

    sep_id = "pipe_spaced" if token == "|" and spacing == "spaced" else ("pipe_compact" if token == "|" else f"{base_id}_{spacing}")
    sep_id = sep_id.replace("__", "_").strip("_")
    if _separator_spec_exists(studio, sep_id, value):
        return sep_id

    label = "Spaced Pipe" if sep_id == "pipe_spaced" else f"Majority {token} {spacing.title()}"
    spec = studio.SeparatorSpec(sep_id, label, "Clean Vertical", value)
    studio.SEPARATOR_LIBRARY = (spec, *tuple(getattr(studio, "SEPARATOR_LIBRARY", tuple()) or tuple()))
    studio.SEPARATORS_BY_ID = {item.id: item for item in studio.SEPARATOR_LIBRARY}
    return sep_id


def ensure_category_frame_spec(studio: Any, frame: Mapping[str, Any]) -> str:
    frame_id = _text(frame.get("id"), "plain")
    kind = _text(frame.get("kind"), "plain")
    if kind == "plain" or frame_id == "plain":
        return "plain"
    if frame_id in getattr(studio, "CATEGORY_FRAMES_BY_ID", {}):
        return frame_id

    char = _text(frame.get("char"), "─")
    count = max(2, _safe_int(frame.get("count"), 3))
    prefix = char * count
    suffix = char * count
    template = f"{prefix} {{emoji}} {{name}} {suffix}"
    label = f"Majority {_frame_label(frame)}"
    spec = studio.CategoryFrameSpec(frame_id, label[:80], template, clutter=1)
    studio.CATEGORY_FRAMES = (spec, *tuple(getattr(studio, "CATEGORY_FRAMES", tuple()) or tuple()))
    studio.CATEGORY_FRAMES_BY_ID = {item.id: item for item in studio.CATEGORY_FRAMES}
    return frame_id


def _lock_counts(options: Mapping[str, Any]) -> dict[str, int]:
    global_lock = options.get("format_lock_global") if isinstance(options, Mapping) else {}
    category_locks = options.get("category_format_locks") if isinstance(options, Mapping) else {}
    channel_locks = options.get("channel_format_locks") if isinstance(options, Mapping) else {}
    return {
        "global": 1 if isinstance(global_lock, Mapping) and bool(global_lock.get("enabled")) else 0,
        "categories": len(category_locks) if isinstance(category_locks, Mapping) else 0,
        "channels": len(channel_locks) if isinstance(channel_locks, Mapping) else 0,
    }


def _summary_text(analysis: Mapping[str, Any]) -> dict[str, str]:
    separator = analysis.get("separator") if isinstance(analysis.get("separator"), Mapping) else {}
    frame = analysis.get("category_frame") if isinstance(analysis.get("category_frame"), Mapping) else {}
    font = analysis.get("font") if isinstance(analysis.get("font"), Mapping) else {}
    emoji = analysis.get("leading_emoji") if isinstance(analysis.get("leading_emoji"), Mapping) else {}
    return {
        "separator": _text(separator.get("label"), "mixed/unknown"),
        "category_frame": _text(frame.get("label"), "mixed/unknown"),
        "font": _text(font.get("label"), "mixed/unknown"),
        "leading_emoji": _text(emoji.get("label"), "mixed/unknown"),
    }


def apply_majority_to_options(
    studio: Any,
    options: Mapping[str, Any],
    analysis: Mapping[str, Any],
    *,
    respect_locks: bool = False,
) -> dict[str, Any]:
    """Return design options that repair toward the live majority layout."""

    install_separator_safe_parser(studio)
    out = dict(options)
    counts = _lock_counts(options)
    lock_total = counts["global"] + counts["categories"] + counts["channels"]
    if lock_total and not respect_locks:
        out["format_lock_global"] = {}
        out["category_format_locks"] = {}
        out["channel_format_locks"] = {}
        out["__majority_layout_overrode_locks"] = lock_total
    elif lock_total:
        out["__majority_layout_lock_override_active"] = lock_total

    separator = analysis.get("separator") if isinstance(analysis.get("separator"), Mapping) else {}
    token = _text(separator.get("token"))
    spacing = _text(separator.get("spacing"), "unknown")
    if spacing in {"compact", "spaced", "none"}:
        out["separator_id"] = ensure_separator_spec(studio, token, spacing)

    frame = analysis.get("category_frame") if isinstance(analysis.get("category_frame"), Mapping) else {}
    if _text(frame.get("kind")) not in {"", "unknown"}:
        out["category_frame_id"] = ensure_category_frame_spec(studio, frame)

    font = analysis.get("font") if isinstance(analysis.get("font"), Mapping) else {}
    font_id = _text(font.get("id"))
    if font_id in {"normal", "fraktur"}:
        out["font"] = font_id

    emoji = analysis.get("leading_emoji") if isinstance(analysis.get("leading_emoji"), Mapping) else {}
    if emoji.get("enabled") is False:
        out["icon_mode"] = "clear"
    elif emoji.get("enabled") is True:
        out["icon_mode"] = "replace_missing"

    desired_strength = 2
    frame_id = _text(out.get("category_frame_id"), "plain")
    if frame_id and frame_id != "plain":
        desired_strength = 5 if _text(out.get("font"), "normal") != "normal" else 3
    elif _text(out.get("font"), "normal") != "normal":
        desired_strength = 4
    out["strength"] = max(desired_strength, min(5, _safe_int(out.get("strength"), desired_strength)))
    out["exact_match"] = True
    out["__majority_layout_inferred"] = True
    out["__majority_layout_summary"] = _summary_text(analysis)
    return out


def annotate_plan_items(items: list[dict[str, Any]], analysis: Mapping[str, Any], options: Mapping[str, Any]) -> list[dict[str, Any]]:
    summary = _summary_text(analysis)
    lock_count = _safe_int(options.get("__majority_layout_overrode_locks"), 0)
    lock_active = _safe_int(options.get("__majority_layout_lock_override_active"), 0)
    for item in items:
        item["majority_layout"] = dict(summary)
        if lock_count:
            item["majority_locks_overridden"] = lock_count
            item["format_lock_scope"] = "majority"
        if lock_active:
            item["majority_lock_override_active"] = lock_active
    return items


def majority_summary_from_items(items: Iterable[Mapping[str, Any]]) -> dict[str, str]:
    for item in items:
        summary = item.get("majority_layout")
        if isinstance(summary, Mapping):
            return {str(k): _text(v) for k, v in summary.items()}
    return {}


def lock_notice_from_items(items: Iterable[Mapping[str, Any]]) -> tuple[int, int]:
    overridden = 0
    active = 0
    for item in items:
        overridden = max(overridden, _safe_int(item.get("majority_locks_overridden"), 0))
        active = max(active, _safe_int(item.get("majority_lock_override_active"), 0))
    return overridden, active


def skipped_lines(items: Iterable[Mapping[str, Any]], *, limit: int = 6) -> list[str]:
    lines: list[str] = []
    for item in items:
        status = _text(item.get("status"))
        if status not in {"protected", "failed"}:
            continue
        before = _text(item.get("before"), "unnamed")
        reasons = list(item.get("blockers") or item.get("warnings") or [])
        reason = clean_design_text(reasons[0] if reasons else "Protected by Dank Design safety rules.")
        lines.append(f"• `{before}` — {reason}"[:220])
        if len(lines) >= limit:
            break
    return lines


__all__ = [
    "VERTICAL_SEPARATOR_TOKENS",
    "apply_majority_to_options",
    "annotate_plan_items",
    "clean_design_text",
    "detect_category_frame",
    "detect_channel_separator",
    "detect_font_style",
    "ensure_category_frame_spec",
    "ensure_separator_spec",
    "infer_live_majority_layout",
    "install_separator_safe_parser",
    "lock_notice_from_items",
    "majority_summary_from_items",
    "skipped_lines",
]
