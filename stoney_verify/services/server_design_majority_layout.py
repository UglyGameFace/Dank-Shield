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

    wrapper_specs = (
        ("「", "」", "bracket_corner"),
        ("『", "』", "bracket_white_corner"),
        ("〔", "〕", "bracket_tortoise"),
        ("【", "】", "bracket_lenticular"),
        ("〖", "〗", "bracket_white_lenticular"),
        ("꒰", "꒱", "bracket_soft"),
    )
    for opener, closer, separator_id in wrapper_specs:
        if raw.startswith(opener) and closer in raw:
            close_at = raw.find(closer)
            inside = raw[len(opener):close_at].strip()
            remainder_after = raw[close_at + len(closer):].strip()
            if inside and remainder_after:
                return {
                    "emoji": inside,
                    "has_leading_emoji": True,
                    "token": f"{opener}{closer}",
                    "spacing": "wrapped",
                    "label": separator_id.replace("_", " "),
                    "separator_id": separator_id,
                    "missing": False,
                    "doubled": False,
                    "separator_in_name_text": False,
                }

    emoji, remainder = _remove_leading_emoji(studio, raw, kind="text")
    left_spaces = len(remainder) - len(remainder.lstrip(" "))
    scan = remainder.lstrip(" ")

    tokens = tuple(sorted({value.strip() for value in _separator_values_for(studio) if value and value.strip()}, key=len, reverse=True))
    for token in tokens:
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
        separator_id = ""
        expected_value = f" {token} " if spacing == "spaced" else token
        for spec in tuple(getattr(studio, "SEPARATOR_LIBRARY", tuple()) or tuple()):
            raw_value = str(getattr(spec, "value", "") or "")
            if raw_value == expected_value:
                separator_id = _text(getattr(spec, "id", ""))
                break
        return {
            "emoji": emoji,
            "has_leading_emoji": bool(emoji),
            "token": token,
            "spacing": spacing,
            "label": _separator_label(token, spacing),
            "separator_id": separator_id,
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
        "separator_id": "none",
        "missing": True,
        "doubled": False,
        "separator_in_name_text": bool(any(mark in scan for mark in VERTICAL_SEPARATOR_TOKENS)),
    }


def _font_compare_text(value: str) -> str:
    for token in VERTICAL_SEPARATOR_TOKENS:
        value = value.replace(token, "")
    value = re.sub(rf"[{re.escape(_FRAME_CHARS)}\s]+", "", value)
    return value


def detect_font_id(studio: Any, name: Any) -> str:
    """Detect the actual supported Unicode font family used by a visible name.

    Older auto-detection collapsed every decorated alphabet into ``fraktur``.
    That made Bold Sans, Monospace, Serif, Small Caps, Fullwidth, Script, and
    other intentional category styles look like mistakes.  Score the real
    runtime glyph maps instead and return the strongest exact family match.
    """

    raw = _strip_invisible_with(studio, name)
    if not raw:
        return "normal"

    styles = tuple(getattr(studio, "FONT_STYLES", ("normal", "fraktur")) or ("normal", "fraktur"))
    runtime_map = getattr(studio, "_runtime_unicode_map", None)
    if not callable(runtime_map):
        plain = _strip_fonts_with(studio, raw)
        return "fraktur" if _font_compare_text(raw) != _font_compare_text(plain) else "normal"

    plain = _strip_fonts_with(studio, raw)
    plain_count = max(1, sum(1 for ch in plain if ch.isalnum()))
    best_font = "normal"
    best_hits = 0

    for font_id in styles:
        font_id = _text(font_id, "normal").lower().replace("-", "_")
        if font_id in {"", "normal", "upside_down"}:
            continue
        try:
            mapping = runtime_map(font_id)
        except Exception:
            continue
        if not isinstance(mapping, Mapping):
            continue
        glyphs = {
            str(glyph)
            for plain_char, glyph in mapping.items()
            if glyph and str(glyph) != str(plain_char) and not str(glyph).isascii()
        }
        if not glyphs:
            continue
        hits = sum(1 for ch in raw if ch in glyphs)
        if hits > best_hits:
            best_font = font_id
            best_hits = hits

    minimum_hits = 1 if plain_count <= 2 else 2
    if best_hits >= minimum_hits and (best_hits / plain_count) >= 0.40:
        return best_font
    return "normal"


def detect_font_style(studio: Any, name: Any) -> str:
    """Backward-compatible coarse label used by older callers."""

    return "normal" if detect_font_id(studio, name) == "normal" else "styled"


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
    count = max(counter.values())
    top_values = [value for value, value_count in counter.items() if value_count == count]
    value = sorted(top_values, key=repr)[0]
    tied = len(top_values) > 1
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
    separator_id = _text(parts.get("separator_id"))
    if separator_id and separator_id != "none":
        return (f"id:{separator_id}", spacing)
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
        font_counts[detect_font_id(studio, name)] += 1
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
        candidate = dict(sep)
        current_example = separator_examples.get(key)
        if current_example is None or repr(sorted(candidate.items())) < repr(sorted(current_example.items())):
            separator_examples[key] = candidate
        if sep.get("separator_in_name_text"):
            issue_counts["separator_in_name_text"] += 1

    sep_key, sep_count, sep_mixed = _top_majority(separator_counts, total=text_total)
    frame_id, frame_count, frame_mixed = _top_majority(frame_counts, total=category_total)
    font_id, font_count, font_mixed = _top_majority(font_counts, total=sum(font_counts.values()))
    emoji_enabled, emoji_count, emoji_mixed = _top_majority(emoji_counts, total=text_total)

    separator = separator_examples.get(sep_key, {"token": "", "spacing": "unknown", "label": "mixed/unknown"}) if sep_key else {"token": "", "spacing": "unknown", "label": "mixed/unknown"}
    if sep_mixed:
        separator = {**separator, "spacing": "mixed/unknown", "label": "mixed/unknown"}
    else:
        majority_spacing = _text(separator.get("spacing"), "unknown")
        majority_token = _text(separator.get("token"))
        resolved_separator_id = ""
        if majority_spacing == "wrapped":
            candidate = _text(separator.get("separator_id"))
            if candidate in getattr(studio, "SEPARATORS_BY_ID", {}):
                resolved_separator_id = candidate
        elif majority_spacing in {"compact", "spaced", "none"}:
            resolved_separator_id = ensure_separator_spec(studio, majority_token, majority_spacing)
        if resolved_separator_id:
            separator = {**separator, "separator_id": resolved_separator_id}
    frame = frame_examples.get(frame_id, {"id": "", "kind": "unknown", "label": "mixed/unknown"}) if frame_id else {"id": "", "kind": "unknown", "label": "mixed/unknown"}
    if frame_mixed:
        frame = {**frame, "id": "", "kind": "unknown", "label": "mixed/unknown"}

    return {
        "text_total": text_total,
        "category_total": category_total,
        "separator": {**separator, "count": sep_count, "mixed": sep_mixed},
        # Keep frame["count"] as the visible frame width.
        # Example: "── name ──" has count=2.
        # Store how many categories used it separately.
        "category_frame": {**frame, "occurrence_count": frame_count, "mixed": frame_mixed},
        "font": {
            "id": _text(font_id) if font_id and not font_mixed else "",
            "label": (_text(font_id).replace("_", " ") if font_id and not font_mixed else "mixed/unknown"),
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
    raw_value = str(getattr(spec, "value", "") or "") if spec is not None else ""
    return bool(spec and raw_value == value)


def ensure_separator_spec(studio: Any, token: str, spacing: str) -> str:
    """Ensure the selected visible separator exists in the runtime library."""

    if spacing == "none" or not token:
        return "none"

    value = f" {token} " if spacing == "spaced" else token
    for spec in tuple(getattr(studio, "SEPARATOR_LIBRARY", tuple()) or tuple()):
        raw_value = str(getattr(spec, "value", "") or "")
        if raw_value == value:
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
    separator_id = _text(separator.get("separator_id"))
    if separator_id and spacing == "wrapped" and separator_id in getattr(studio, "SEPARATORS_BY_ID", {}):
        out["separator_id"] = separator_id
    elif spacing in {"compact", "spaced", "none"}:
        out["separator_id"] = ensure_separator_spec(studio, token, spacing)

    frame = analysis.get("category_frame") if isinstance(analysis.get("category_frame"), Mapping) else {}
    if _text(frame.get("kind")) not in {"", "unknown"}:
        out["category_frame_id"] = ensure_category_frame_spec(studio, frame)

    font = analysis.get("font") if isinstance(analysis.get("font"), Mapping) else {}
    font_id = _text(font.get("id"))
    if font_id in set(getattr(studio, "FONT_STYLES", ("normal", "fraktur"))):
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



def _majority_value(summary: Mapping[str, str], key: str) -> str:
    return _text(summary.get(key), "mixed/unknown")


def _is_unknown_majority(value: str) -> bool:
    text = _text(value).lower()
    return not text or "mixed" in text or "unknown" in text


def _separator_tuple(parts: Mapping[str, Any]) -> tuple[str, str]:
    return (_text(parts.get("token")), _text(parts.get("spacing"), "none"))


def classify_repair_item(studio: Any, item: Mapping[str, Any], analysis: Mapping[str, Any]) -> dict[str, Any]:
    """Explain exactly why one repair row is safe or unsafe.

    This is intentionally conservative: if the repair would change a visual
    dimension whose live majority is mixed/unknown, the row gets a blocker
    instead of being auto-applied.
    """

    summary = _summary_text(analysis)
    before = _text(item.get("before"))
    after = _text(item.get("after"))
    kind = _text(item.get("kind"), "text")
    status = _text(item.get("status"))

    details: list[str] = []
    blockers: list[str] = []

    if status != "changed" or not before or not after or before == after:
        return {"details": details, "blockers": blockers, "confidence": "not-applicable"}

    if kind == "category":
        before_frame = detect_category_frame(studio, before)
        after_frame = detect_category_frame(studio, after)
        if _text(before_frame.get("id")) != _text(after_frame.get("id")):
            target = _majority_value(summary, "category_frame")
            if _is_unknown_majority(target):
                blockers.append("Category frame majority is mixed/unknown, so this category frame repair needs manual review.")
            else:
                details.append(f"Category frame repaired to majority: {target}.")
        return {
            "details": details,
            "blockers": blockers,
            "confidence": "blocked" if blockers else ("high" if details else "medium"),
        }

    before_sep = detect_channel_separator(studio, before)
    after_sep = detect_channel_separator(studio, after)
    if _separator_tuple(before_sep) != _separator_tuple(after_sep):
        target = _majority_value(summary, "separator")
        if _is_unknown_majority(target):
            blockers.append("Separator majority is mixed/unknown, so this separator repair needs manual review.")
        elif bool(before_sep.get("missing")):
            details.append(f"Missing separator repaired to majority: {target}.")
        elif bool(before_sep.get("doubled")):
            details.append(f"Doubled separator cleaned to majority: {target}.")
        elif _text(before_sep.get("token")) != _text(after_sep.get("token")):
            details.append(f"Wrong separator repaired to majority: {target}.")
        elif _text(before_sep.get("spacing")) != _text(after_sep.get("spacing")):
            details.append(f"Separator spacing repaired to majority: {target}.")
        else:
            details.append(f"Separator repaired to majority: {target}.")

    before_font = detect_font_id(studio, before)
    after_font = detect_font_id(studio, after)
    if before_font != after_font:
        target = _majority_value(summary, "font")
        if _is_unknown_majority(target):
            blockers.append("Font/style majority is mixed/unknown, so this font repair needs manual review.")
        else:
            details.append(f"Font/style repaired to majority: {target}.")

    return {
        "details": details,
        "blockers": blockers,
        "confidence": "blocked" if blockers else ("high" if details else "medium"),
    }


def _fail_repair_item(item: dict[str, Any], reason: str) -> None:
    item.setdefault("blockers", []).append(clean_design_text(reason))
    item["status"] = "failed"
    item["majority_repair_safety_blocked"] = True


def _expected_separator(analysis: Mapping[str, Any]) -> tuple[str, str]:
    separator = analysis.get("separator") if isinstance(analysis.get("separator"), Mapping) else {}
    token = _text(separator.get("token"))
    spacing = _text(separator.get("spacing"), "unknown")
    if spacing not in {"compact", "spaced", "none"}:
        return "", "unknown"
    return token, spacing


def _separator_matches_expected(studio: Any, after: str, token: str, spacing: str) -> bool:
    parts = detect_channel_separator(studio, after)

    if parts.get("doubled"):
        return False

    if parts.get("separator_in_name_text") and spacing != "none":
        return False

    if spacing == "none":
        return bool(parts.get("missing"))

    return _text(parts.get("token")) == token and _text(parts.get("spacing")) == spacing


def _expected_frame(analysis: Mapping[str, Any]) -> Mapping[str, Any]:
    frame = analysis.get("category_frame") if isinstance(analysis.get("category_frame"), Mapping) else {}
    if _text(frame.get("kind")) in {"", "unknown"}:
        return {}
    return frame


def _frame_matches_expected(studio: Any, after: str, expected: Mapping[str, Any]) -> bool:
    """Return True when a category output visibly keeps the detected majority frame.

    This check is intentionally visible-first. The safety gate should block
    missing/wrong frames, but it must never false-block a category that visibly
    has the same frame family around the name.
    """

    expected_kind = _text(expected.get("kind"), "plain")
    if expected_kind in {"", "unknown"}:
        return True

    visible = _text(after).strip()

    if expected_kind == "plain":
        return _text(detect_category_frame(studio, after).get("kind"), "plain") == "plain"

    if expected_kind in {"line", "heavy_line", "dash_line"}:
        expected_char = _text(expected.get("char"))
        if not expected_char:
            expected_char = "─" if expected_kind == "line" else ("━" if expected_kind == "heavy_line" else "-")

        expected_count = max(2, _safe_int(expected.get("count"), 2))

        # Exact visible match: ── name ── / ━━ name ━━ / -- name --
        prefix = expected_char * expected_count
        suffix = expected_char * expected_count
        if visible.startswith(prefix) and visible.endswith(suffix):
            return True

        # Same frame family with a harmless count difference.
        # This prevents false-blocking ─── name ─── when the detected majority was ── name ──.
        if visible.startswith(expected_char * 2) and visible.endswith(expected_char * 2):
            return True

        detected = detect_category_frame(studio, after)
        detected_kind = _text(detected.get("kind"), "plain")
        detected_count = _safe_int(detected.get("count"), 0)
        detected_char = _text(detected.get("char"))

        return (
            detected_kind == expected_kind
            and detected_count >= 2
            and (not detected_char or detected_char == expected_char)
        )

    detected = detect_category_frame(studio, after)
    return _text(detected.get("kind"), "plain") == expected_kind



def _font_matches_expected(studio: Any, after: str, analysis: Mapping[str, Any]) -> bool:
    font = analysis.get("font") if isinstance(analysis.get("font"), Mapping) else {}
    expected = _text(font.get("id"))

    if expected not in set(getattr(studio, "FONT_STYLES", ("normal", "fraktur"))):
        return True

    detected = detect_font_id(studio, after)
    return detected == expected


def validate_majority_repair_items(
    studio: Any,
    items: list[dict[str, Any]],
    analysis: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Fail closed when a majority repair preview does not match the detected majority."""

    token, spacing = _expected_separator(analysis)
    frame = _expected_frame(analysis)

    for item in items:
        if item.get("status") != "changed":
            continue

        kind = _text(item.get("kind"), "text")
        after = _text(item.get("after"))

        if not after:
            _fail_repair_item(item, "Repair safety stopped this row because the proposed name was blank.")
            continue

        if kind == "category":
            if frame:
                expected_kind = _text(frame.get("kind"), "plain")
                expected_char = _text(frame.get("char"))
                if not expected_char and expected_kind in {"line", "heavy_line", "dash_line"}:
                    expected_char = "─" if expected_kind == "line" else ("━" if expected_kind == "heavy_line" else "-")

                visible = _text(after).strip()
                visibly_has_expected_frame = (
                    bool(expected_char)
                    and visible.startswith(expected_char * 2)
                    and visible.endswith(expected_char * 2)
                )

                if not visibly_has_expected_frame and not _frame_matches_expected(studio, after, frame):
                    _fail_repair_item(
                        item,
                        "Repair safety stopped this category because the preview did not match the detected majority category frame.",
                    )
            continue

        if spacing != "unknown" and not _separator_matches_expected(studio, after, token, spacing):
            label = _separator_label(token, spacing)
            _fail_repair_item(
                item,
                f"Repair safety stopped this channel because the preview did not keep the detected majority separator: {label}.",
            )
            continue

        if not _font_matches_expected(studio, after, analysis):
            font = analysis.get("font") if isinstance(analysis.get("font"), Mapping) else {}
            label = _text(font.get("label"), "detected majority font/style")
            _fail_repair_item(
                item,
                f"Repair safety stopped this channel because the preview did not match the detected majority font/style: {label}.",
            )

    return items


def annotate_plan_items(
    items: list[dict[str, Any]],
    analysis: Mapping[str, Any],
    options: Mapping[str, Any],
    *,
    studio: Any | None = None,
) -> list[dict[str, Any]]:
    summary = _summary_text(analysis)
    lock_count = _safe_int(options.get("__majority_layout_overrode_locks"), 0)
    lock_active = _safe_int(options.get("__majority_layout_lock_override_active"), 0)

    if studio is not None and bool(options.get("__majority_layout_inferred")):
        items = validate_majority_repair_items(studio, items, analysis)

    for item in items:
        item["majority_layout"] = dict(summary)
        if lock_count:
            item["majority_locks_overridden"] = lock_count
            item["format_lock_scope"] = "majority"
        if lock_active:
            item["majority_lock_override_active"] = lock_active

    return items




def _record_id(record: Any) -> str:
    if isinstance(record, Mapping):
        return _text(record.get("id"))
    return _text(getattr(record, "id", ""))


def _record_category_id(record: Any) -> str:
    if isinstance(record, Mapping):
        return _text(record.get("category_id"))
    return _text(getattr(record, "category_id", ""))


def infer_category_local_layouts(studio: Any, records: Iterable[Any]) -> dict[str, Any]:
    """Infer independent channel styles for each category.

    Category A is never allowed to become the template for Category B.  The
    uncategorized channel bucket is treated as its own group.  Category headers
    are intentionally not used as channel-majority votes because a category
    title often uses a different font/frame from the channels beneath it.
    """

    groups: dict[str, list[Any]] = {}
    category_names: dict[str, str] = {}
    for record in records:
        kind = _record_kind(record)
        rid = _record_id(record)
        if kind == "category":
            if rid:
                category_names[rid] = _record_name(record)
            continue
        category_id = _record_category_id(record) or "__uncategorized__"
        groups.setdefault(category_id, []).append(record)

    analyses = {
        category_id: infer_live_majority_layout(studio, rows)
        for category_id, rows in sorted(groups.items(), key=lambda item: item[0])
        if rows
    }
    return {
        "channel_groups": analyses,
        "category_names": dict(sorted(category_names.items(), key=lambda item: item[0])),
        "summaries": {category_id: _summary_text(analysis) for category_id, analysis in analyses.items()},
    }


def infer_target_layout(
    studio: Any,
    records: Iterable[Any],
    *,
    scope: str,
    target_id: int,
) -> dict[str, Any]:
    """Return the local live layout relevant to one category/channel editor."""

    rows = list(records)
    target_key = str(int(target_id))
    target = next((row for row in rows if _record_id(row) == target_key), None)
    profiles = infer_category_local_layouts(studio, rows)
    groups = profiles.get("channel_groups") if isinstance(profiles.get("channel_groups"), Mapping) else {}

    if scope == "channel" and target is not None:
        category_id = _record_category_id(target) or "__uncategorized__"
        analysis = groups.get(category_id)
        if isinstance(analysis, Mapping):
            return dict(analysis)

    if scope == "category":
        analysis = groups.get(target_key)
        if isinstance(analysis, Mapping):
            return dict(analysis)

    return infer_live_majority_layout(studio, rows)


def _saved_lock_scope(options: Mapping[str, Any], *, channel_id: str, category_id: str) -> str:
    channel_locks = options.get("channel_format_locks") if isinstance(options.get("channel_format_locks"), Mapping) else {}
    category_locks = options.get("category_format_locks") if isinstance(options.get("category_format_locks"), Mapping) else {}
    global_lock = options.get("format_lock_global") if isinstance(options.get("format_lock_global"), Mapping) else {}
    if channel_id and isinstance(channel_locks.get(channel_id), Mapping):
        return "channel"
    if category_id and isinstance(category_locks.get(category_id), Mapping):
        return "category"
    if global_lock.get("enabled"):
        return "global"
    return ""


def _separator_id_from_parts(studio: Any, parts: Mapping[str, Any]) -> str:
    separator_id = _text(parts.get("separator_id"))
    spacing = _text(parts.get("spacing"), "unknown")
    token = _text(parts.get("token"))
    if separator_id and spacing == "wrapped" and separator_id in getattr(studio, "SEPARATORS_BY_ID", {}):
        return separator_id
    if spacing in {"compact", "spaced", "none"}:
        return ensure_separator_spec(studio, token, spacing)
    return ""


def _auto_channel_lock(
    studio: Any,
    record: Mapping[str, Any],
    analysis: Mapping[str, Any],
    options: Mapping[str, Any],
) -> dict[str, Any] | None:
    current_name = _record_name(record)
    current_separator = detect_channel_separator(studio, current_name)
    majority_separator = analysis.get("separator") if isinstance(analysis.get("separator"), Mapping) else {}
    majority_spacing = _text(majority_separator.get("spacing"), "unknown")
    separator_id = ""
    if majority_spacing not in {"mixed/unknown", "unknown", "partial", "doubled"}:
        separator_id = _separator_id_from_parts(studio, majority_separator)
    if not separator_id:
        separator_id = _separator_id_from_parts(studio, current_separator)
    if not separator_id:
        return None

    majority_font = analysis.get("font") if isinstance(analysis.get("font"), Mapping) else {}
    font_id = _text(majority_font.get("id"))
    if font_id not in set(getattr(studio, "FONT_STYLES", ("normal", "fraktur"))):
        font_id = detect_font_id(studio, current_name)

    majority_emoji = analysis.get("leading_emoji") if isinstance(analysis.get("leading_emoji"), Mapping) else {}
    enabled = majority_emoji.get("enabled")
    current_has_emoji = bool(current_separator.get("has_leading_emoji"))
    if enabled is True:
        icon_mode = "replace_missing"
    elif enabled is False:
        icon_mode = "clear"
    else:
        icon_mode = "keep_existing" if current_has_emoji else "clear"

    strength = 4 if font_id != "normal" else 2
    return {
        "scope": "auto_category",
        "theme_id": _text(options.get("theme_id"), "gothic_clean"),
        "strength": strength,
        "font": font_id,
        "separator_id": separator_id,
        "category_frame_id": _text(options.get("category_frame_id"), "plain"),
        "icon_mode": icon_mode,
        "emoji_override": "",
        "exact_match": True,
        "__auto_detect": True,
    }


def build_category_aware_options(
    studio: Any,
    options: Mapping[str, Any],
    records: Iterable[Mapping[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Create ephemeral per-channel rules from each category's own majority.

    Saved channel/category/global locks always win and are never overwritten.
    Category headers without an explicit saved rule are preserved as-is.  When a
    category is too mixed to infer safely, only dimensions with a clear majority
    are changed and uncertain dimensions keep each channel's current style.
    """

    rows = [dict(row) for row in records]
    profiles = infer_category_local_layouts(studio, rows)
    analyses = profiles.get("channel_groups") if isinstance(profiles.get("channel_groups"), Mapping) else {}

    out = dict(options)
    saved_channel_locks = dict(options.get("channel_format_locks")) if isinstance(options.get("channel_format_locks"), Mapping) else {}
    merged_channel_locks = dict(saved_channel_locks)
    ephemeral_ids: list[str] = []
    preserve_ids: list[str] = []

    for record in rows:
        channel_id = _record_id(record)
        kind = _record_kind(record)
        category_id = channel_id if kind == "category" else (_record_category_id(record) or "__uncategorized__")
        saved_scope = _saved_lock_scope(options, channel_id=channel_id, category_id=(channel_id if kind == "category" else _record_category_id(record)))
        if saved_scope:
            continue
        if kind == "category":
            if channel_id:
                preserve_ids.append(channel_id)
            continue
        analysis = analyses.get(category_id)
        if not isinstance(analysis, Mapping):
            if channel_id:
                preserve_ids.append(channel_id)
            continue
        lock = _auto_channel_lock(studio, record, analysis, options)
        if lock is None:
            if channel_id:
                preserve_ids.append(channel_id)
            continue
        merged_channel_locks[channel_id] = lock
        ephemeral_ids.append(channel_id)

    out["channel_format_locks"] = merged_channel_locks
    out["__category_aware_auto_detect"] = True
    out["__auto_detect_ephemeral_channel_ids"] = sorted(set(ephemeral_ids))
    out["__auto_detect_preserve_ids"] = sorted(set(preserve_ids))
    out["__auto_detect_category_analyses"] = dict(sorted(analyses.items(), key=lambda item: str(item[0])))
    out["__auto_detect_category_profiles"] = dict(profiles.get("summaries") or {})
    out["__auto_detect_saved_rules_respected"] = _lock_counts(options)
    return out, profiles


def annotate_category_aware_plan_items(
    studio: Any,
    items: list[dict[str, Any]],
    options: Mapping[str, Any],
) -> list[dict[str, Any]]:
    analyses = options.get("__auto_detect_category_analyses") if isinstance(options.get("__auto_detect_category_analyses"), Mapping) else {}
    ephemeral = {str(value) for value in list(options.get("__auto_detect_ephemeral_channel_ids") or [])}
    preserve = {str(value) for value in list(options.get("__auto_detect_preserve_ids") or [])}
    saved_counts = options.get("__auto_detect_saved_rules_respected") if isinstance(options.get("__auto_detect_saved_rules_respected"), Mapping) else {}
    saved_total = sum(_safe_int(saved_counts.get(key), 0) for key in ("global", "categories", "channels"))

    for item in items:
        channel_id = _text(item.get("channel_id"))
        category_id = _text(item.get("category_id")) or "__uncategorized__"
        kind = _text(item.get("kind"), "text")

        if channel_id in preserve:
            item["after"] = item.get("before")
            item["status"] = "unchanged"
            item["blockers"] = []
            item["warnings"] = list(item.get("warnings") or []) + [
                "Smart Auto-Detect left this item unchanged because it has no safe local majority target."
            ]
            item["auto_detect_preserved"] = True
            continue

        if channel_id in ephemeral:
            analysis = analyses.get(category_id)
            if isinstance(analysis, Mapping):
                validate_majority_repair_items(studio, [item], analysis)
                item["majority_layout"] = _summary_text(analysis)
            item["format_lock_scope"] = "auto_category"
            item["auto_detect_category_id"] = category_id

        if saved_total:
            item["auto_detect_saved_rules_respected"] = saved_total

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
    "classify_repair_item",
    "detect_category_frame",
    "detect_channel_separator",
    "detect_font_id",
    "detect_font_style",
    "ensure_category_frame_spec",
    "ensure_separator_spec",
    "infer_live_majority_layout",
    "infer_category_local_layouts",
    "infer_target_layout",
    "build_category_aware_options",
    "annotate_category_aware_plan_items",
    "install_separator_safe_parser",
    "validate_majority_repair_items",
    "lock_notice_from_items",
    "majority_summary_from_items",
    "skipped_lines",
]
