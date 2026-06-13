from __future__ import annotations

"""Exact Unicode font maps for Channel Name Fonts.

Some decorative Unicode alphabets have gaps, so simple contiguous ranges leave
letters unchanged or mapped to the wrong symbol. This guard builds mappings by
Unicode character name and patches both the runtime and the font catalog guard.
It also makes live channel apply use decoded plain names instead of re-styling
already-styled channel names.
"""

import re
import unicodedata
from typing import Any

_PATCHED = False


def _u(name: str, fallback: str | None = None) -> str | None:
    try:
        return unicodedata.lookup(name)
    except KeyError:
        return fallback


def _math_letters(prefix: str, *, digit_prefix: str | None = None, special: dict[str, str] | None = None) -> dict[str, str]:
    special = special or {}
    out: dict[str, str] = {}
    for ch in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
        out[ch] = special.get(ch) or _u(f"MATHEMATICAL {prefix} CAPITAL {ch}", ch) or ch
    for ch in "abcdefghijklmnopqrstuvwxyz":
        out[ch] = special.get(ch) or _u(f"MATHEMATICAL {prefix} SMALL {ch.upper()}", ch) or ch
    if digit_prefix:
        for digit, word in (("0", "ZERO"), ("1", "ONE"), ("2", "TWO"), ("3", "THREE"), ("4", "FOUR"), ("5", "FIVE"), ("6", "SIX"), ("7", "SEVEN"), ("8", "EIGHT"), ("9", "NINE")):
            out[digit] = _u(f"MATHEMATICAL {digit_prefix} DIGIT {word}", digit) or digit
    return out


def _range(chars: str, start: int) -> dict[str, str]:
    return {char: chr(start + index) for index, char in enumerate(chars)}


def _explicit(rows: list[tuple[str, int | str]]) -> dict[str, str]:
    result: dict[str, str] = {}
    for key, value in rows:
        result[key] = chr(value) if isinstance(value, int) else value
    return result


def exact_unicode_map(style: str) -> dict[str, str]:
    style = str(style or "").strip().lower().replace("-", "_")
    if style == "bold_sans":
        return _math_letters("SANS-SERIF BOLD", digit_prefix="SANS-SERIF BOLD")
    if style == "italic_sans":
        return _math_letters("SANS-SERIF ITALIC")
    if style == "bold_italic_sans":
        return _math_letters("SANS-SERIF BOLD ITALIC")
    if style == "monospace":
        return _math_letters("MONOSPACE", digit_prefix="MONOSPACE")
    if style == "fullwidth":
        return {**_range("ABCDEFGHIJKLMNOPQRSTUVWXYZ", 0xFF21), **_range("abcdefghijklmnopqrstuvwxyz", 0xFF41), **_range("0123456789", 0xFF10), "-": chr(0xFF0D), " ": chr(0x3000)}
    if style == "serif_bold":
        return _math_letters("BOLD", digit_prefix="BOLD")
    if style == "serif_italic":
        return _math_letters("ITALIC", special={"h": chr(0x210E)})
    if style == "serif_bold_italic":
        return _math_letters("BOLD ITALIC")
    if style == "script":
        return _math_letters(
            "SCRIPT",
            special={
                "B": chr(0x212C), "E": chr(0x2130), "F": chr(0x2131), "H": chr(0x210B),
                "I": chr(0x2110), "L": chr(0x2112), "M": chr(0x2133), "R": chr(0x211B),
                "e": chr(0x212F), "g": chr(0x210A), "o": chr(0x2134),
            },
        )
    if style == "bold_script":
        return _math_letters("BOLD SCRIPT")
    if style == "fraktur":
        return _math_letters(
            "FRAKTUR",
            special={"C": chr(0x212D), "H": chr(0x210C), "I": chr(0x2111), "R": chr(0x211C), "Z": chr(0x2128)},
        )
    if style == "bold_fraktur":
        return _math_letters("BOLD FRAKTUR")
    if style == "circled":
        return {**_range("ABCDEFGHIJKLMNOPQRSTUVWXYZ", 0x24B6), **_range("abcdefghijklmnopqrstuvwxyz", 0x24D0), "0": chr(0x24EA), "1": chr(0x2460), "2": chr(0x2461), "3": chr(0x2462), "4": chr(0x2463), "5": chr(0x2464), "6": chr(0x2465), "7": chr(0x2466), "8": chr(0x2467), "9": chr(0x2468)}
    if style == "parenthesized":
        return _explicit([
            ("a", 0x249C), ("b", 0x249D), ("c", 0x249E), ("d", 0x249F), ("e", 0x24A0), ("f", 0x24A1), ("g", 0x24A2), ("h", 0x24A3), ("i", 0x24A4), ("j", 0x24A5), ("k", 0x24A6), ("l", 0x24A7), ("m", 0x24A8), ("n", 0x24A9), ("o", 0x24AA), ("p", 0x24AB), ("q", 0x24AC), ("r", 0x24AD), ("s", 0x24AE), ("t", 0x24AF), ("u", 0x24B0), ("v", 0x24B1), ("w", 0x24B2), ("x", 0x24B3), ("y", 0x24B4), ("z", 0x24B5),
            ("1", 0x2474), ("2", 0x2475), ("3", 0x2476), ("4", 0x2477), ("5", 0x2478), ("6", 0x2479), ("7", 0x247A), ("8", 0x247B), ("9", 0x247C),
        ])
    if style == "small_caps":
        return _explicit([
            ("a", 0x1D00), ("b", 0x0299), ("c", 0x1D04), ("d", 0x1D05), ("e", 0x1D07), ("f", 0xA730), ("g", 0x0262), ("h", 0x029C), ("i", 0x026A), ("j", 0x1D0A), ("k", 0x1D0B), ("l", 0x029F), ("m", 0x1D0D), ("n", 0x0274), ("o", 0x1D0F), ("p", 0x1D18), ("q", 0x01EB), ("r", 0x0280), ("s", 0xA731), ("t", 0x1D1B), ("u", 0x1D1C), ("v", 0x1D20), ("w", 0x1D21), ("x", "x"), ("y", 0x028F), ("z", 0x1D22),
        ])
    if style == "upside_down":
        return _explicit([
            ("a", 0x0250), ("b", "q"), ("c", 0x0254), ("d", "p"), ("e", 0x01DD), ("f", 0x025F), ("g", 0x0183), ("h", 0x0265), ("i", 0x1D09), ("j", 0x027E), ("k", 0x029E), ("l", "l"), ("m", 0x026F), ("n", "u"), ("o", "o"), ("p", "d"), ("q", "b"), ("r", 0x0279), ("s", "s"), ("t", 0x0287), ("u", "n"), ("v", 0x028C), ("w", 0x028D), ("x", "x"), ("y", 0x028E), ("z", "z"),
            ("A", 0x2200), ("C", 0x0186), ("E", 0x018E), ("F", 0x2132), ("G", 0x05E4), ("J", 0x017F), ("L", 0x02E5), ("P", 0x0500), ("T", 0x22A5), ("U", 0x0548), ("V", 0x039B), ("W", "M"), ("Y", 0x2144),
            ("0", "0"), ("1", 0x0196), ("2", 0x1105), ("3", 0x0190), ("4", 0x3123), ("5", 0x03DB), ("6", "9"), ("7", 0x3125), ("8", "8"), ("9", "6"),
        ])
    return {}


def _decode(value: Any) -> str:
    reverse: dict[str, str] = {}
    for style in ("bold_sans", "italic_sans", "bold_italic_sans", "monospace", "fullwidth", "serif_bold", "serif_italic", "serif_bold_italic", "script", "bold_script", "fraktur", "bold_fraktur", "circled", "parenthesized", "small_caps", "upside_down"):
        for plain, styled in exact_unicode_map(style).items():
            if isinstance(styled, str) and len(styled) == 1 and styled != plain and not (styled.isascii() and styled.isalnum()):
                reverse[styled] = plain
    return "".join(reverse.get(ch, ch) for ch in unicodedata.normalize("NFKC", str(value or "")) if unicodedata.category(ch) != "Cf")


def plain_live_name(value: Any) -> str:
    text = _decode(value)
    chars = list(text)
    first = -1
    last = -1
    for idx, ch in enumerate(chars):
        if ch.isalnum():
            if first < 0:
                first = idx
            last = idx
    if first < 0 or last < 0:
        return text[:100]
    prefix = "".join(chars[:first])
    middle = "".join(chars[first : last + 1]).replace("&", " and ").replace("'", "").replace("`", "").replace(chr(0x2019), "")
    suffix = "".join(chars[last + 1 :])
    middle = re.sub(r"-+", "-", "".join(ch if ch.isalnum() else "-" for ch in middle)).strip("-").lower()
    return f"{prefix}{middle}{suffix}"[:100]


def _patch_live_plan() -> None:
    try:
        from stoney_verify.startup_guards import channel_font_rename_queue_guard as guard
        if getattr(guard, "_plain_live_names", False):
            return

        async def parts(guild: Any, options: dict[str, str]):
            ctx = await guard._skip_context(int(guild.id))
            channels = list(getattr(guild, "categories", []) or []) + [c for c in list(getattr(guild, "channels", []) or []) if not isinstance(c, guard.discord.CategoryChannel)]
            ready: list[dict[str, Any]] = []
            blocked: list[dict[str, Any]] = []
            skipped = 0
            seen: set[int] = set()
            for channel in channels:
                cid = guard._safe_int(getattr(channel, "id", 0), 0)
                if cid <= 0 or cid in seen:
                    continue
                seen.add(cid)
                if guard._kind(channel) == "other":
                    continue
                if guard._skip(channel, ctx):
                    skipped += 1
                    continue
                before = guard._safe_str(getattr(channel, "name", ""))
                after = plain_live_name(before)
                if not after or after == before:
                    continue
                row = {"channel_id": str(cid), "before": before, "after": after, "kind": guard._kind(channel)}
                reason = guard._bot_access_reason(guild, channel)
                if reason:
                    row["blocked_reason"] = reason
                    blocked.append(row)
                    continue
                ready.append(row)
                if len(ready) >= guard.MAX_PLAN_ITEMS:
                    break
            return ready, blocked, skipped

        guard._build_plan_parts = parts
        guard._plain_live_names = True
    except Exception as exc:
        try:
            print(f"⚠️ channel_font_exact_unicode_guard live plan failed: {exc!r}")
        except Exception:
            pass


def _load_preview_button_guard() -> None:
    try:
        from stoney_verify.startup_guards import channel_font_preview_button_guard
        channel_font_preview_button_guard.apply()
    except Exception as exc:
        try:
            print(f"⚠️ channel_font_exact_unicode_guard preview button failed: {exc!r}")
        except Exception:
            pass


def apply() -> bool:
    global _PATCHED
    _load_preview_button_guard()
    try:
        from stoney_verify.services import channel_builder_runtime as runtime
        runtime._unicode_map = exact_unicode_map
        try:
            from stoney_verify.startup_guards import channel_builder_full_font_catalog_guard as catalog
            catalog.full_unicode_map = exact_unicode_map
        except Exception:
            pass
        _patch_live_plan()
        _PATCHED = True
        print("🔤 channel_font_exact_unicode_guard active; exact maps and safe decoded live rename plans patched")
        return True
    except Exception as exc:
        try:
            print(f"⚠️ channel_font_exact_unicode_guard failed: {exc!r}")
        except Exception:
            pass
        return False


apply()

__all__ = ["apply", "exact_unicode_map", "plain_live_name"]