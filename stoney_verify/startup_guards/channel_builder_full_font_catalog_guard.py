from __future__ import annotations

"""Extend bot Channel Builder runtime with the full dashboard font catalog."""

from typing import Any

_PATCHED = False
_QUEUE_FLOW_LOADED = False
_MENU_CLARITY_LOADED = False


def _log(message: str) -> None:
    try:
        print(f"🔤 channel_builder_full_font_catalog_guard {message}")
    except Exception:
        pass


def _range(chars: str, start: int) -> dict[str, str]:
    return {char: chr(start + index) for index, char in enumerate(chars)}


def _explicit(entries: list[tuple[str, str]]) -> dict[str, str]:
    return {k: v for k, v in entries}


def full_unicode_map(style: str) -> dict[str, str]:
    a = "abcdefghijklmnopqrstuvwxyz"
    z = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    d = "0123456789"
    style = str(style or "").strip().lower().replace("-", "_")
    if style == "bold_sans":
        return {**_range(z, 0x1D5D4), **_range(a, 0x1D5EE), **_range(d, 0x1D7EC)}
    if style == "italic_sans":
        return {**_range(z, 0x1D608), **_range(a, 0x1D622)}
    if style == "bold_italic_sans":
        return {**_range(z, 0x1D63C), **_range(a, 0x1D656)}
    if style == "monospace":
        return {**_range(z, 0x1D670), **_range(a, 0x1D68A), **_range(d, 0x1D7F6)}
    if style == "fullwidth":
        return {**_range(z, 0xFF21), **_range(a, 0xFF41), **_range(d, 0xFF10), "-": "－", " ": "　"}
    if style == "serif_bold":
        return {**_range(z, 0x1D400), **_range(a, 0x1D41A), **_range(d, 0x1D7CE)}
    if style == "serif_italic":
        return {**_range(z, 0x1D434), **_range(a, 0x1D44E), "h": "ℎ"}
    if style == "serif_bold_italic":
        return {**_range(z, 0x1D468), **_range(a, 0x1D482)}
    if style == "script":
        return {**_range(z, 0x1D49C), **_range(a, 0x1D4B6), "B": "ℬ", "E": "ℰ", "F": "ℱ", "H": "ℋ", "I": "ℐ", "L": "ℒ", "M": "ℳ", "R": "ℛ", "e": "ℯ", "g": "ℊ", "o": "ℴ"}
    if style == "bold_script":
        return {**_range(z, 0x1D4D0), **_range(a, 0x1D4EA)}
    if style == "fraktur":
        return {**_range(z, 0x1D504), **_range(a, 0x1D51E), "C": "ℭ", "H": "ℌ", "I": "ℑ", "R": "ℜ", "Z": "ℨ"}
    if style == "bold_fraktur":
        return {**_range(z, 0x1D56C), **_range(a, 0x1D586)}
    if style == "circled":
        return {**_range(z, 0x24B6), **_range(a, 0x24D0), **_range(d, 0x24EA), "0": "⓪", "1": "①", "2": "②", "3": "③", "4": "④", "5": "⑤", "6": "⑥", "7": "⑦", "8": "⑧", "9": "⑨"}
    if style == "parenthesized":
        return _explicit([
            ("a", "⒜"), ("b", "⒝"), ("c", "⒞"), ("d", "⒟"), ("e", "⒠"), ("f", "⒡"), ("g", "⒢"), ("h", "⒣"), ("i", "⒤"), ("j", "⒥"), ("k", "⒦"), ("l", "⒧"), ("m", "⒨"), ("n", "⒩"), ("o", "⒪"), ("p", "⒫"), ("q", "⒬"), ("r", "⒭"), ("s", "⒮"), ("t", "⒯"), ("u", "⒰"), ("v", "⒱"), ("w", "⒲"), ("x", "⒳"), ("y", "⒴"), ("z", "⒵"),
            ("1", "⑴"), ("2", "⑵"), ("3", "⑶"), ("4", "⑷"), ("5", "⑸"), ("6", "⑹"), ("7", "⑺"), ("8", "⑻"), ("9", "⑼"),
        ])
    if style == "small_caps":
        return _explicit([
            ("a", "ᴀ"), ("b", "ʙ"), ("c", "ᴄ"), ("d", "ᴅ"), ("e", "ᴇ"), ("f", "ꜰ"), ("g", "ɢ"), ("h", "ʜ"), ("i", "ɪ"), ("j", "ᴊ"), ("k", "ᴋ"), ("l", "ʟ"), ("m", "ᴍ"), ("n", "ɴ"), ("o", "ᴏ"), ("p", "ᴘ"), ("q", "ǫ"), ("r", "ʀ"), ("s", "ꜱ"), ("t", "ᴛ"), ("u", "ᴜ"), ("v", "ᴠ"), ("w", "ᴡ"), ("x", "x"), ("y", "ʏ"), ("z", "ᴢ"),
        ])
    if style == "upside_down":
        return _explicit([
            ("a", "ɐ"), ("b", "q"), ("c", "ɔ"), ("d", "p"), ("e", "ǝ"), ("f", "ɟ"), ("g", "ƃ"), ("h", "ɥ"), ("i", "ᴉ"), ("j", "ɾ"), ("k", "ʞ"), ("l", "l"), ("m", "ɯ"), ("n", "u"), ("o", "o"), ("p", "d"), ("q", "b"), ("r", "ɹ"), ("s", "s"), ("t", "ʇ"), ("u", "n"), ("v", "ʌ"), ("w", "ʍ"), ("x", "x"), ("y", "ʎ"), ("z", "z"),
            ("A", "∀"), ("C", "Ɔ"), ("E", "Ǝ"), ("F", "Ⅎ"), ("G", "פ"), ("J", "ſ"), ("L", "˥"), ("P", "Ԁ"), ("T", "⊥"), ("U", "Ո"), ("V", "Λ"), ("W", "M"), ("Y", "⅄"),
            ("1", "Ɩ"), ("2", "ᄅ"), ("3", "Ɛ"), ("4", "ㄣ"), ("5", "ϛ"), ("6", "9"), ("7", "ㄥ"), ("8", "8"), ("9", "6"), ("0", "0"),
        ])
    return {}


def _load_queue_flow() -> None:
    global _QUEUE_FLOW_LOADED
    if _QUEUE_FLOW_LOADED:
        return
    try:
        from stoney_verify.startup_guards import channel_font_rename_queue_guard

        channel_font_rename_queue_guard.apply()
        _QUEUE_FLOW_LOADED = True
    except Exception as exc:
        try:
            print(f"⚠️ channel_builder_full_font_catalog_guard queue flow failed: {exc!r}")
        except Exception:
            pass


def _load_menu_clarity() -> None:
    global _MENU_CLARITY_LOADED
    if _MENU_CLARITY_LOADED:
        return
    try:
        from stoney_verify.startup_guards import channel_font_menu_clarity_guard

        channel_font_menu_clarity_guard.apply()
        _MENU_CLARITY_LOADED = True
    except Exception as exc:
        try:
            print(f"⚠️ channel_builder_full_font_catalog_guard menu clarity failed: {exc!r}")
        except Exception:
            pass


def apply() -> bool:
    global _PATCHED
    _load_queue_flow()
    _load_menu_clarity()
    if _PATCHED:
        return True
    try:
        from stoney_verify.services import channel_builder_runtime as runtime

        runtime._unicode_map = full_unicode_map
        _PATCHED = True
        _log("active; bot runtime supports the full dashboard font catalog")
        return True
    except Exception as exc:
        try:
            print(f"⚠️ channel_builder_full_font_catalog_guard failed: {exc!r}")
        except Exception:
            pass
        return False


apply()

__all__ = ["apply", "full_unicode_map"]