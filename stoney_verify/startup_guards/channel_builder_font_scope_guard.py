from __future__ import annotations

"""Bot-side Channel Builder font apply mode support.

Dashboard can already produce final names, but the bot runtime should also be able
to build names from raw rows + options. This adds support for:
- unicodeStyleScope / fontApplyMode = whole_name
- unicodeStyleScope / fontApplyMode = text_only

Text-only mode preserves emoji/decorative prefix/suffix and changes only the text
core, preventing duplicate emoji when restyling existing channel names.
"""

import re
import unicodedata
from typing import Any

_PATCHED = False
_ORIGINAL_NORMALIZE: Any = None

_A = "abcdefghijklmnopqrstuvwxyz"
_Z = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
_D = "0123456789"


def _log(message: str) -> None:
    try:
        print(f"🔤 channel_builder_font_scope_guard {message}")
    except Exception:
        pass


def _warn(message: str) -> None:
    try:
        print(f"⚠️ channel_builder_font_scope_guard {message}")
    except Exception:
        pass


def _range_map(chars: str, start: int) -> dict[str, str]:
    return {char: chr(start + index) for index, char in enumerate(chars)}


def _maps() -> dict[str, dict[str, str]]:
    return {
        "bold_sans": {**_range_map(_Z, 0x1D5D4), **_range_map(_A, 0x1D5EE), **_range_map(_D, 0x1D7EC)},
        "italic_sans": {**_range_map(_Z, 0x1D608), **_range_map(_A, 0x1D622)},
        "bold_italic_sans": {**_range_map(_Z, 0x1D63C), **_range_map(_A, 0x1D656)},
        "monospace": {**_range_map(_Z, 0x1D670), **_range_map(_A, 0x1D68A), **_range_map(_D, 0x1D7F6)},
        "fullwidth": {**_range_map(_Z, 0xFF21), **_range_map(_A, 0xFF41), **_range_map(_D, 0xFF10), "-": chr(0xFF0D), " ": chr(0x3000)},
        "serif_bold": {**_range_map(_Z, 0x1D400), **_range_map(_A, 0x1D41A), **_range_map(_D, 0x1D7CE)},
        "serif_italic": {**_range_map(_Z, 0x1D434), **_range_map(_A, 0x1D44E)},
        "serif_bold_italic": {**_range_map(_Z, 0x1D468), **_range_map(_A, 0x1D482)},
        "script": {**_range_map(_Z, 0x1D49C), **_range_map(_A, 0x1D4B6)},
        "bold_script": {**_range_map(_Z, 0x1D4D0), **_range_map(_A, 0x1D4EA)},
        "fraktur": {**_range_map(_Z, 0x1D504), **_range_map(_A, 0x1D51E)},
        "bold_fraktur": {**_range_map(_Z, 0x1D56C), **_range_map(_A, 0x1D586)},
        "circled": {**_range_map(_Z, 0x24B6), **_range_map(_A, 0x24D0)},
        "small_caps": {
            "a": chr(0x1D00), "b": chr(0x0299), "c": chr(0x1D04), "d": chr(0x1D05), "e": chr(0x1D07), "f": chr(0xA730), "g": chr(0x0262), "h": chr(0x029C),
            "i": chr(0x026A), "j": chr(0x1D0A), "k": chr(0x1D0B), "l": chr(0x029F), "m": chr(0x1D0D), "n": chr(0x0274), "o": chr(0x1D0F), "p": chr(0x1D18),
            "q": chr(0x01EB), "r": chr(0x0280), "s": chr(0xA731), "t": chr(0x1D1B), "u": chr(0x1D1C), "v": chr(0x1D20), "w": chr(0x1D21), "x": "x", "y": chr(0x028F), "z": chr(0x1D22),
        },
    }


def _safe_str(value: Any, default: str = "") -> str:
    try:
        if value is None:
            return default
        text = str(value).strip()
        return text if text else default
    except Exception:
        return default


def _strip_hidden(value: str) -> str:
    return "".join(ch for ch in str(value or "") if unicodedata.category(ch) != "Cf")


def _base(value: str, case_mode: str = "lower") -> str:
    cleaned = unicodedata.normalize("NFKC", _strip_hidden(value)).replace("&", " and ")
    cleaned = cleaned.replace("'", "").replace("`", "").replace(chr(0x2019), "")
    text = re.sub(r"-+", "-", "".join(ch if ch.isalnum() else "-" for ch in cleaned)).strip("-")
    if case_mode == "preserve":
        return text
    if case_mode == "compact":
        return text.replace("-", "").lower()
    if case_mode == "title":
        return "-".join(part[:1].upper() + part[1:].lower() for part in text.split("-") if part)
    return text.lower()


def _split_text(value: str) -> tuple[str, str, str]:
    normalized = unicodedata.normalize("NFKC", _strip_hidden(value))
    chars = [*normalized]
    first = -1
    last = -1
    for index, char in enumerate(chars):
        if char.isalnum():
            if first < 0:
                first = index
            last = index
    if first < 0 or last < 0:
        return "", normalized, ""
    return "".join(chars[:first]), "".join(chars[first : last + 1]), "".join(chars[last + 1 :])


def _style_options(value: Any) -> dict[str, Any]:
    raw = value if isinstance(value, dict) else {}
    style = _safe_str(raw.get("unicodeStyle") or raw.get("unicode_style") or raw.get("font") or "normal").lower().replace("-", "_")
    if style != "normal" and style not in _maps():
        style = "normal"
    scope = _safe_str(raw.get("unicodeStyleScope") or raw.get("unicode_style_scope") or raw.get("fontApplyMode") or raw.get("font_apply_mode") or "whole_name").lower().replace("-", "_")
    if scope not in {"whole_name", "text_only"}:
        scope = "whole_name"
    sep = _safe_str(raw.get("separator") or chr(0x30FB))
    if sep == "none":
        sep = ""
    return {
        "emoji": None if raw.get("emoji") is None else _safe_str(raw.get("emoji")),
        "emoji_position": _safe_str(raw.get("emojiPosition") or raw.get("emoji_position") or "first").lower(),
        "separator": sep[:4],
        "case_mode": _safe_str(raw.get("caseMode") or raw.get("case_mode") or "lower").lower(),
        "style": style,
        "scope": scope,
    }


def _transform(value: str, style: str) -> str:
    if not style or style == "normal":
        return value
    mapping = _maps().get(style) or {}
    return "".join(mapping.get(ch, ch) for ch in value)


def _format_name(value: Any, options: Any = None) -> str:
    opts = _style_options(options)
    source = _safe_str(value)
    if not source:
        return ""
    if opts["scope"] == "text_only":
        prefix, text, suffix = _split_text(source)
        final = f"{prefix}{_transform(_base(text or source, opts['case_mode']), opts['style'])}{suffix}"
    else:
        core = _transform(_base(source, opts["case_mode"]), opts["style"])
        emoji = _safe_str(opts.get("emoji"))
        sep = _safe_str(opts.get("separator"))
        if emoji and opts.get("emoji_position") != "none":
            final = f"{core}{sep}{emoji}" if opts.get("emoji_position") == "last" else f"{emoji}{sep}{core}"
        else:
            final = core
    return "".join([*_strip_hidden(final)][:100]).rstrip("-" + chr(0x30FB))


def apply() -> bool:
    global _PATCHED, _ORIGINAL_NORMALIZE
    if _PATCHED:
        return True
    try:
        from stoney_verify.services import channel_builder_runtime as runtime
        from stoney_verify.api_new import channel_builder_routes as routes

        original = getattr(runtime, "normalize_channel_builder_items", None)
        if not callable(original) or getattr(original, "_font_scope_wrapped", False):
            return False

        def wrapped(raw: Any, *, options: Any = None, limit: int = 150) -> list[dict[str, Any]]:
            items = original(raw, limit=limit)
            if not isinstance(raw, list):
                return items
            base_options = _style_options(options)
            for item in items:
                try:
                    if _safe_str(item.get("final_name")):
                        item["unicode_style_scope"] = base_options.get("scope")
                        continue
                    index = int(item.get("index", 0))
                    row = raw[index] if 0 <= index < len(raw) and isinstance(raw[index], dict) else {}
                    row_options = {**base_options, **_style_options(row.get("options") or row.get("styleOptions") or row.get("style_options"))}
                    source = row.get("baseName") or row.get("base_name") or row.get("name") or row.get("currentName") or row.get("current_name") or item.get("base_name")
                    item["final_name"] = _format_name(source, row_options)
                    item["unicode_style"] = row_options.get("style")
                    item["unicode_style_scope"] = row_options.get("scope")
                except Exception:
                    continue
            return items

        setattr(wrapped, "_font_scope_wrapped", True)
        _ORIGINAL_NORMALIZE = original
        runtime.normalize_channel_builder_items = wrapped
        routes.normalize_channel_builder_items = wrapped
        _PATCHED = True
        _log("active; bot Channel Builder supports whole-name and text-only font apply modes")
        return True
    except Exception as exc:
        _warn(f"failed: {exc!r}")
        return False


apply()

__all__ = ["apply"]
