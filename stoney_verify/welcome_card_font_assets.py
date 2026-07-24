from __future__ import annotations

"""Validation, normalization, and config storage for uploaded welcome-card fonts."""

import base64
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any, Mapping, Optional

from PIL import ImageFont
from fontTools.ttLib import TTCollection, TTFont

SUPPORTED_FONT_EXTENSIONS = frozenset({".ttf", ".otf", ".ttc", ".otc", ".woff", ".woff2"})
MAX_FONT_UPLOAD_BYTES = 4 * 1024 * 1024
MAX_STORED_FONT_BYTES = 2 * 1024 * 1024
MAX_FONT_GLYPHS = 20_000
_REJECTED_TABLES = frozenset({"SVG ", "CBDT", "CBLC", "sbix", "EBDT", "EBLC"})
_REQUIRED_ASCII = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789!?@#&-_."


@dataclass(frozen=True)
class NormalizedWelcomeFont:
    data: bytes
    display_name: str
    source_format: str
    glyph_count: int


def _cfg_value(cfg: Any, key: str, default: Any = None) -> Any:
    try:
        value = getattr(cfg, key, None)
        if value is not None:
            return value
    except Exception:
        pass
    try:
        if hasattr(cfg, "get"):
            value = cfg.get(key)
            if value is not None:
                return value
    except Exception:
        pass
    for bucket in ("settings", "config", "metadata", "meta"):
        try:
            nested = getattr(cfg, bucket, None)
            if isinstance(nested, Mapping) and nested.get(key) is not None:
                return nested.get(key)
        except Exception:
            pass
        try:
            if hasattr(cfg, "get"):
                nested = cfg.get(bucket)
                if isinstance(nested, Mapping) and nested.get(key) is not None:
                    return nested.get(key)
        except Exception:
            pass
    return default


def supported_font_types_text() -> str:
    return "TTF, OTF, TTC, OTC, WOFF, or WOFF2"


def _safe_font_name(value: Any, fallback: str = "Uploaded Font") -> str:
    text = " ".join(str(value or "").replace("\x00", " ").split()).strip()
    if not text:
        text = fallback
    return text[:80]


def _font_display_name(font: TTFont, filename: str) -> str:
    names = font.get("name")
    if names is not None:
        for name_id in (4, 1, 6):
            for record in getattr(names, "names", ()):
                if int(getattr(record, "nameID", -1)) != name_id:
                    continue
                try:
                    value = record.toUnicode()
                except Exception:
                    continue
                value = _safe_font_name(value, "")
                if value:
                    return value
    return _safe_font_name(Path(filename).stem)


def _load_font_container(data: bytes, suffix: str) -> tuple[TTFont, Optional[TTCollection]]:
    source = BytesIO(data)
    if suffix in {".ttc", ".otc"}:
        collection = TTCollection(source, lazy=False)
        fonts = list(getattr(collection, "fonts", ()) or ())
        if not fonts:
            raise ValueError("The font collection does not contain any usable faces.")
        return fonts[0], collection
    return (
        TTFont(
            source,
            lazy=False,
            checkChecksums=2,
            recalcBBoxes=False,
            recalcTimestamp=False,
        ),
        None,
    )


def _validate_font_tables(font: TTFont) -> int:
    required_tables = {"head", "hhea", "maxp", "cmap", "name"}
    missing = sorted(required_tables - set(font.keys()))
    if missing:
        raise ValueError("The font is missing required OpenType tables: " + ", ".join(missing))
    rejected = sorted(_REJECTED_TABLES & set(font.keys()))
    if rejected:
        raise ValueError(
            "Color, SVG, and bitmap-only font tables are not supported for welcome cards."
        )
    glyph_count = int(getattr(font["maxp"], "numGlyphs", 0) or 0)
    if glyph_count <= 0:
        raise ValueError("The font contains no glyphs.")
    if glyph_count > MAX_FONT_GLYPHS:
        raise ValueError(f"The font has too many glyphs ({glyph_count:,}); maximum is {MAX_FONT_GLYPHS:,}.")
    cmap: set[int] = set()
    for table in getattr(font["cmap"], "tables", ()):
        if not getattr(table, "isUnicode", lambda: False)():
            continue
        cmap.update(int(codepoint) for codepoint in getattr(table, "cmap", {}).keys())
    missing_chars = [character for character in _REQUIRED_ASCII if ord(character) not in cmap]
    if missing_chars:
        preview = "".join(missing_chars[:12])
        raise ValueError(
            "The font is missing basic letters, numbers, or punctuation needed for welcome cards "
            f"(for example: {preview!r})."
        )
    return glyph_count


def normalize_uploaded_font(data: bytes, filename: str) -> NormalizedWelcomeFont:
    suffix = Path(str(filename or "")).suffix.lower()
    if suffix not in SUPPORTED_FONT_EXTENSIONS:
        raise ValueError(f"Unsupported font type. Upload {supported_font_types_text()}.")
    if not data:
        raise ValueError("The uploaded font file is empty.")
    if len(data) > MAX_FONT_UPLOAD_BYTES:
        raise ValueError("The font file exceeds the 4 MB upload limit.")

    font: Optional[TTFont] = None
    collection: Optional[TTCollection] = None
    try:
        font, collection = _load_font_container(data, suffix)
        glyph_count = _validate_font_tables(font)
        display_name = _font_display_name(font, filename)
        # WOFF/WOFF2 are transport formats. Clearing flavor writes a normal
        # TrueType/OpenType sfnt stream that Pillow/FreeType can render from memory.
        font.flavor = None
        output = BytesIO()
        font.save(output, reorderTables=False)
        normalized = output.getvalue()
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError("The attachment is not a valid or supported font file.") from exc
    finally:
        try:
            if collection is not None:
                collection.close()
        except Exception:
            pass
        try:
            if font is not None:
                font.close()
        except Exception:
            pass

    if not normalized:
        raise ValueError("The font could not be normalized for rendering.")
    if len(normalized) > MAX_STORED_FONT_BYTES:
        raise ValueError("The normalized font exceeds the 2 MB storage limit. Try a smaller static font file.")
    try:
        test_font = ImageFont.truetype(BytesIO(normalized), 64)
        box = test_font.getbbox("Welcome UglyGameFace 123")
        if not box or box[2] <= box[0] or box[3] <= box[1]:
            raise OSError("empty font metrics")
    except Exception as exc:
        raise ValueError("The font passed validation but FreeType could not render it safely.") from exc

    return NormalizedWelcomeFont(
        data=normalized,
        display_name=display_name,
        source_format=suffix.lstrip(".").upper(),
        glyph_count=glyph_count,
    )


def encode_custom_font(data: bytes) -> str:
    if len(data) > MAX_STORED_FONT_BYTES:
        raise ValueError("Normalized custom font exceeds the storage limit.")
    return base64.b64encode(data).decode("ascii")


def decode_custom_font(cfg: Any) -> tuple[Optional[bytes], str]:
    raw = str(_cfg_value(cfg, "welcome_card_custom_font_b64", "") or "").strip()
    name = _safe_font_name(_cfg_value(cfg, "welcome_card_custom_font_name", ""), "Uploaded Font")
    if not raw:
        return None, name
    try:
        data = base64.b64decode(raw, validate=True)
    except Exception:
        return None, name
    if not data or len(data) > MAX_STORED_FONT_BYTES:
        return None, name
    try:
        font = ImageFont.truetype(BytesIO(data), 48)
        box = font.getbbox("Welcome 123")
        if not box or box[2] <= box[0]:
            return None, name
    except Exception:
        return None, name
    return data, name


__all__ = [
    "MAX_FONT_GLYPHS",
    "MAX_FONT_UPLOAD_BYTES",
    "MAX_STORED_FONT_BYTES",
    "NormalizedWelcomeFont",
    "SUPPORTED_FONT_EXTENSIONS",
    "decode_custom_font",
    "encode_custom_font",
    "normalize_uploaded_font",
    "supported_font_types_text",
]
