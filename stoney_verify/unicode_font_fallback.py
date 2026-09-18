from __future__ import annotations

"""Unicode-preserving font fallback for bitmap lifecycle cards.

The visual card engine may prefer a themed or uploaded font, but Discord display
names are not restricted to that font's cmap.  This module keeps the original
Unicode text intact, chooses a face per grapheme cluster, groups adjacent
clusters back into shaped runs, and asks Pillow/RAQM to shape each run.

Fallback fonts are discovered through JustMyType.  The production requirements
install Noto packs for Western/math/symbol, RTL, South Asian, Southeast Asian,
African, CJK, and monochrome emoji coverage.  System Noto faces are considered
as well.  No transliteration or compatibility normalization happens here.
"""

from dataclasses import dataclass, field
from functools import lru_cache
from hashlib import sha256
from io import BytesIO
import math
from pathlib import Path
import unicodedata
from typing import Iterable, Optional, Sequence

from fontTools.ttLib import TTFont
from PIL import Image, ImageDraw, ImageFont

try:
    import regex as _regex
except Exception:  # pragma: no cover - requirements install regex in production
    _regex = None


_VARIATION_SELECTORS = frozenset(
    list(range(0xFE00, 0xFE10)) + list(range(0xE0100, 0xE01F0))
)
_ZWJ = 0x200D


@dataclass(frozen=True)
class FontSource:
    key: str
    path: Optional[str] = None
    data: Optional[bytes] = field(default=None, repr=False, compare=False, hash=False)
    index: int = 0


@dataclass(frozen=True)
class FontRun:
    text: str
    source: FontSource


_BYTES_COVERAGE: dict[str, frozenset[int]] = {}


def _graphemes(text: str) -> list[str]:
    value = str(text or "")
    if not value:
        return []
    if _regex is not None:
        return [str(part) for part in _regex.findall(r"\X", value)]

    # Conservative fallback used only if regex is unavailable.  Keep combining
    # marks, variation selectors, emoji modifiers, and ZWJ sequences attached.
    clusters: list[str] = []
    regional_pending = False
    for character in value:
        cp = ord(character)
        combine = bool(unicodedata.combining(character))
        variation = cp in _VARIATION_SELECTORS
        modifier = 0x1F3FB <= cp <= 0x1F3FF
        regional = 0x1F1E6 <= cp <= 0x1F1FF
        attach = combine or variation or modifier
        if clusters and (attach or ord(clusters[-1][-1]) == _ZWJ or cp == _ZWJ):
            clusters[-1] += character
            regional_pending = False
            continue
        if regional and clusters and regional_pending:
            clusters[-1] += character
            regional_pending = False
            continue
        clusters.append(character)
        regional_pending = regional
    return clusters


def grapheme_clusters(text: str) -> tuple[str, ...]:
    """Return Unicode extended grapheme clusters without rewriting text."""

    return tuple(_graphemes(str(text or "")))


def _required_codepoints(text: str) -> frozenset[int]:
    required: set[int] = set()
    for character in text:
        cp = ord(character)
        if cp == _ZWJ or cp in _VARIATION_SELECTORS:
            continue
        # Other Unicode format controls are layout instructions rather than
        # visible glyphs and do not need a cmap entry in the selected face.
        if unicodedata.category(character) == "Cf":
            continue
        required.add(cp)
    return frozenset(required)


def _collect_unicode_cmap(font: TTFont) -> frozenset[int]:
    points: set[int] = set()
    cmap = font.get("cmap")
    if cmap is None:
        return frozenset()
    for table in getattr(cmap, "tables", ()):
        try:
            if not table.isUnicode():
                continue
        except Exception:
            continue
        points.update(int(codepoint) for codepoint in getattr(table, "cmap", {}).keys())
    return frozenset(points)


@lru_cache(maxsize=512)
def _path_codepoints(path: str, index: int = 0) -> frozenset[int]:
    try:
        font = TTFont(
            path,
            fontNumber=max(0, int(index)),
            lazy=True,
            recalcBBoxes=False,
            recalcTimestamp=False,
        )
    except Exception:
        return frozenset()
    try:
        return _collect_unicode_cmap(font)
    finally:
        try:
            font.close()
        except Exception:
            pass


def _bytes_codepoints(data: bytes, key: str, index: int = 0) -> frozenset[int]:
    cache_key = f"{key}:{int(index)}"
    cached = _BYTES_COVERAGE.get(cache_key)
    if cached is not None:
        return cached
    try:
        font = TTFont(
            BytesIO(data),
            fontNumber=max(0, int(index)),
            lazy=True,
            recalcBBoxes=False,
            recalcTimestamp=False,
        )
    except Exception:
        coverage = frozenset()
    else:
        try:
            coverage = _collect_unicode_cmap(font)
        finally:
            try:
                font.close()
            except Exception:
                pass
    _BYTES_COVERAGE[cache_key] = coverage
    return coverage


def source_codepoints(source: FontSource) -> frozenset[int]:
    if source.data is not None:
        return _bytes_codepoints(source.data, source.key, source.index)
    if source.path:
        return _path_codepoints(source.path, source.index)
    return frozenset()


def source_supports(source: FontSource, text: str) -> bool:
    required = _required_codepoints(text)
    return not required or required.issubset(source_codepoints(source))


def _font_family_priority(family: str) -> tuple[int, str]:
    folded = family.casefold()
    exact = {
        "noto sans": 0,
        "noto sans math": 5,
        "noto sans symbols": 6,
        "noto sans symbols 2": 7,
        "noto sans arabic": 10,
        "noto sans hebrew": 11,
        "noto sans devanagari": 12,
        "noto sans bengali": 13,
        "noto sans tamil": 14,
        "noto sans telugu": 15,
        "noto sans thai": 16,
        "noto sans lao": 17,
        "noto sans khmer": 18,
        "noto sans myanmar": 19,
        "noto sans ethiopic": 20,
        "noto sans sc": 30,
        "noto sans tc": 31,
        "noto sans jp": 32,
        "noto sans kr": 33,
        "noto emoji": 40,
        "noto color emoji": 99,
    }
    if folded in exact:
        return exact[folded], folded
    if "math" in folded:
        return 8, folded
    if "symbol" in folded:
        return 9, folded
    if folded.startswith("noto sans"):
        return 45, folded
    if folded.startswith("noto"):
        return 60, folded
    return 200, folded


@lru_cache(maxsize=2)
def _registered_fallback_paths(bold: bool) -> tuple[str, ...]:
    """Return bundled/system Noto faces registered by JustMyType."""

    try:
        from justmytype import get_default_registry
    except Exception:
        return ()

    try:
        registry = get_default_registry()
        families = list(registry.list_families())
    except Exception:
        return ()

    families = [
        str(family)
        for family in families
        if str(family).casefold().startswith("noto")
    ]
    families.sort(key=_font_family_priority)

    paths: list[str] = []
    seen: set[str] = set()
    weight = 700 if bold else 400
    for family in families:
        try:
            info = registry.find_font(
                family=family,
                weight=weight,
                style="normal",
            )
        except Exception:
            continue
        path = str(getattr(info, "path", "") or "").strip() if info else ""
        if not path or path in seen:
            continue
        try:
            if not Path(path).is_file():
                continue
        except Exception:
            continue
        seen.add(path)
        paths.append(path)
    return tuple(paths)


def _source_key_for_path(path: str, *, prefix: str) -> str:
    return f"{prefix}:{Path(path).name}:{sha256(path.encode('utf-8', 'ignore')).hexdigest()[:10]}"


def fallback_sources(
    *,
    primary_paths: Sequence[str],
    bold: bool,
    custom_font_bytes: Optional[bytes] = None,
) -> tuple[FontSource, ...]:
    """Build the ordered source stack used by one card text render."""

    sources: list[FontSource] = []
    seen_paths: set[str] = set()

    if custom_font_bytes:
        digest = sha256(custom_font_bytes).hexdigest()
        sources.append(
            FontSource(
                key=f"custom:{digest[:16]}",
                data=custom_font_bytes,
            )
        )

    for path_value in primary_paths:
        path = str(path_value or "").strip()
        if not path or path in seen_paths:
            continue
        try:
            if not Path(path).is_file():
                continue
        except Exception:
            continue
        seen_paths.add(path)
        sources.append(
            FontSource(
                key=_source_key_for_path(path, prefix="primary"),
                path=path,
            )
        )

    for path in _registered_fallback_paths(bool(bold)):
        if path in seen_paths:
            continue
        seen_paths.add(path)
        sources.append(
            FontSource(
                key=_source_key_for_path(path, prefix="fallback"),
                path=path,
            )
        )

    return tuple(sources)


def _choose_source(cluster: str, sources: Sequence[FontSource]) -> FontSource:
    if not sources:
        raise ValueError("No usable font sources are available.")

    required = _required_codepoints(cluster)
    if not required:
        return sources[0]

    best = sources[0]
    best_count = -1
    for source in sources:
        coverage = source_codepoints(source)
        if required.issubset(coverage):
            return source
        covered = len(required.intersection(coverage))
        if covered > best_count:
            best = source
            best_count = covered
    return best


def _contains_rtl(text: str) -> bool:
    return any(unicodedata.bidirectional(character) in {"R", "AL", "AN"} for character in text)


def resolve_font_runs(
    text: str,
    *,
    primary_paths: Sequence[str],
    bold: bool,
    custom_font_bytes: Optional[bytes] = None,
    tracking: int = 0,
) -> tuple[FontRun, ...]:
    """Resolve exact Unicode text to font runs without rewriting the text."""

    value = str(text or "")
    if not value:
        return ()

    sources = fallback_sources(
        primary_paths=primary_paths,
        bold=bold,
        custom_font_bytes=custom_font_bytes,
    )
    if not sources:
        return ()

    # Bidi shaping is most reliable when the whole line stays in one face.
    # Prefer that whenever a candidate actually covers the complete string.
    if _contains_rtl(value):
        for source in sources:
            if source_supports(source, value):
                return (FontRun(value, source),)

    clusters = _graphemes(value)
    resolved = [(cluster, _choose_source(cluster, sources)) for cluster in clusters]

    # Tracking is intentionally implemented per grapheme.  Complex text disables
    # tracking below, so grouping by source keeps Arabic/Indic shaping intact.
    effective_tracking = safe_tracking(value, tracking)
    if effective_tracking > 0:
        return tuple(FontRun(cluster, source) for cluster, source in resolved)

    runs: list[FontRun] = []
    for cluster, source in resolved:
        if runs and runs[-1].source.key == source.key:
            previous = runs[-1]
            runs[-1] = FontRun(previous.text + cluster, previous.source)
        else:
            runs.append(FontRun(cluster, source))
    return tuple(runs)


def safe_tracking(text: str, tracking: int) -> int:
    value = max(0, int(tracking or 0))
    if value <= 0:
        return 0
    for character in str(text or ""):
        cp = ord(character)
        if cp == _ZWJ or cp in _VARIATION_SELECTORS or unicodedata.combining(character):
            return 0
        # Keep manual tracking to Latin/Latin-extended text.  Other scripts need
        # their shaping engine to control glyph adjacency and mark placement.
        if cp > 0x024F:
            return 0
    return value


def _layout_engine():
    try:
        if bool(getattr(ImageFont.core, "HAVE_RAQM", False)):
            return ImageFont.Layout.RAQM
    except Exception:
        pass
    try:
        return ImageFont.Layout.BASIC
    except Exception:
        return None


def load_font(source: FontSource, size: int) -> ImageFont.ImageFont:
    pixel_size = max(8, int(size))
    layout = _layout_engine()
    kwargs = {"index": max(0, int(source.index))}
    if layout is not None:
        kwargs["layout_engine"] = layout
    if source.data is not None:
        return ImageFont.truetype(BytesIO(source.data), pixel_size, **kwargs)
    if source.path:
        return ImageFont.truetype(source.path, pixel_size, **kwargs)
    raise OSError("Font source has no path or data.")


def _render_plan(
    text: str,
    *,
    size: int,
    primary_paths: Sequence[str],
    bold: bool,
    custom_font_bytes: Optional[bytes],
    tracking: int,
) -> tuple[list[tuple[str, ImageFont.ImageFont]], int]:
    effective_tracking = safe_tracking(text, tracking)
    runs = resolve_font_runs(
        text,
        primary_paths=primary_paths,
        bold=bold,
        custom_font_bytes=custom_font_bytes,
        tracking=effective_tracking,
    )
    loaded: dict[str, ImageFont.ImageFont] = {}
    plan: list[tuple[str, ImageFont.ImageFont]] = []
    for run in runs:
        font = loaded.get(run.source.key)
        if font is None:
            try:
                font = load_font(run.source, size)
            except Exception:
                continue
            loaded[run.source.key] = font
        plan.append((run.text, font))
    return plan, effective_tracking


def measure_text(
    text: str,
    *,
    size: int,
    primary_paths: Sequence[str],
    bold: bool = True,
    custom_font_bytes: Optional[bytes] = None,
    tracking: int = 0,
) -> int:
    value = str(text or "")
    if not value:
        return 0
    plan, effective_tracking = _render_plan(
        value,
        size=size,
        primary_paths=primary_paths,
        bold=bold,
        custom_font_bytes=custom_font_bytes,
        tracking=tracking,
    )
    if not plan:
        return 0
    probe = ImageDraw.Draw(Image.new("L", (8, 8), 0))
    width = 0.0
    for index, (chunk, font) in enumerate(plan):
        try:
            width += float(probe.textlength(chunk, font=font))
        except Exception:
            box = probe.textbbox((0, 0), chunk, font=font)
            width += max(0, box[2] - box[0])
        if effective_tracking and index < len(plan) - 1:
            width += effective_tracking
    return max(0, int(math.ceil(width)))


def render_text_mask(
    text: str,
    *,
    size: int,
    primary_paths: Sequence[str],
    bold: bool = True,
    custom_font_bytes: Optional[bytes] = None,
    tracking: int = 0,
    padding: int = 4,
) -> Image.Image:
    """Render exact Unicode text to an antialiased L mask with font fallback."""

    value = str(text or "")
    if not value:
        return Image.new("L", (1, 1), 0)

    plan, effective_tracking = _render_plan(
        value,
        size=size,
        primary_paths=primary_paths,
        bold=bold,
        custom_font_bytes=custom_font_bytes,
        tracking=tracking,
    )
    if not plan:
        return Image.new("L", (1, 1), 0)

    probe = ImageDraw.Draw(Image.new("L", (8, 8), 0))
    cursor = 0.0
    placements: list[tuple[float, str, ImageFont.ImageFont]] = []
    left = 0.0
    top = 0.0
    right = 1.0
    bottom = 1.0

    for index, (chunk, font) in enumerate(plan):
        try:
            box = probe.textbbox((cursor, 0), chunk, font=font, anchor="ls")
        except Exception:
            box = probe.textbbox((cursor, 0), chunk, font=font)
        left = min(left, float(box[0]))
        top = min(top, float(box[1]))
        right = max(right, float(box[2]))
        bottom = max(bottom, float(box[3]))
        placements.append((cursor, chunk, font))
        try:
            advance = float(probe.textlength(chunk, font=font))
        except Exception:
            advance = float(max(0, box[2] - box[0]))
        cursor += advance
        if effective_tracking and index < len(plan) - 1:
            cursor += effective_tracking

    right = max(right, cursor)
    margin = max(1, int(padding))
    width = max(1, int(math.ceil(right - left)) + margin * 2)
    height = max(1, int(math.ceil(bottom - top)) + margin * 2)
    mask = Image.new("L", (width, height), 0)
    draw = ImageDraw.Draw(mask)
    origin_x = margin - left
    baseline_y = margin - top

    for x, chunk, font in placements:
        try:
            draw.text(
                (origin_x + x, baseline_y),
                chunk,
                font=font,
                fill=255,
                anchor="ls",
            )
        except Exception:
            # Some bitmap/color-only faces cannot be painted into an L target.
            # The installed monochrome Noto Emoji face should win before those;
            # if a platform still returns an unusable face, keep the renderer
            # alive without modifying the original text.
            try:
                draw.text(
                    (origin_x + x, baseline_y),
                    chunk,
                    font=font,
                    fill=255,
                )
            except Exception:
                continue
    return mask


__all__ = [
    "FontRun",
    "FontSource",
    "fallback_sources",
    "grapheme_clusters",
    "load_font",
    "measure_text",
    "render_text_mask",
    "resolve_font_runs",
    "safe_tracking",
    "source_codepoints",
    "source_supports",
]
