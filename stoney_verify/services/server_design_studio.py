from __future__ import annotations

"""Safe naming engine for Dank Shield Server Design Studio.

The key guarantee: font styling is never allowed to block a rename by itself.
If a glyph is not available in the requested style, the engine falls back per
character and keeps readable text as the final fallback.
"""

from dataclasses import dataclass, field
import re
import unicodedata
from typing import Any, Iterable, Mapping

DISCORD_NAME_LIMIT = 100
DEFAULT_DELAY_SECONDS = 2.0
MAX_PLAN_ITEMS = 150

FONT_STYLES = (
    "normal", "bold_sans", "italic_sans", "bold_italic_sans", "monospace", "fullwidth",
    "serif_bold", "serif_italic", "serif_bold_italic", "script", "bold_script",
    "fraktur", "bold_fraktur", "circled", "parenthesized", "small_caps", "upside_down",
)
RISKY_FONTS = {"script", "bold_script", "fraktur", "bold_fraktur", "circled", "parenthesized", "upside_down"}
DEFAULT_PROTECTED_NAMES = {
    "mod-log", "logs", "audit-log", "transcripts", "transcript", "archive", "archives",
    "staff-chat", "staff", "bot-commands", "setup", "active-tickets", "archived-tickets",
}
PROTECTION_MODES = {"never", "emoji_only", "separator_only", "font_only", "category_frame_only", "full"}


@dataclass(frozen=True)
class SeparatorSpec:
    id: str
    label: str
    pack: str
    value: str = ""
    template: str = "{emoji}{separator}{name}"
    safety: str = "safe"
    mobile: str = "good"
    clutter: int = 0


@dataclass(frozen=True)
class CategoryFrameSpec:
    id: str
    label: str
    template: str
    safety: str = "safe"
    clutter: int = 0


@dataclass(frozen=True)
class ThemePreset:
    id: str
    label: str
    category_frame: str
    channel_separator: str
    font: str
    icon_pack: str
    channel_format: str = "{emoji}{separator}{name}"
    cleanup_rules: tuple[str, ...] = ("strip_old_font", "dedupe_emoji", "dedupe_separator", "normalize_separator")
    fallback_rules: tuple[str, ...] = ("requested", "closest", "bold_sans", "monospace", "fullwidth", "normal")
    protected_defaults: tuple[str, ...] = tuple(sorted(DEFAULT_PROTECTED_NAMES))


@dataclass
class TransformSubstitution:
    char: str
    requested_font: str
    fallback_font: str
    replacement: str
    reason: str


@dataclass
class DesignNameResult:
    before: str
    after: str
    base_name: str
    kind: str = "text"
    emoji: str = ""
    separator_id: str = ""
    font: str = "normal"
    category_frame_id: str = ""
    changed: bool = False
    protected: bool = False
    warnings: list[str] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)
    substitutions: list[TransformSubstitution] = field(default_factory=list)
    readability_score: int = 100
    mobile_score: int = 100
    clutter_score: int = 0

    @property
    def status(self) -> str:
        if self.blockers:
            return "failed"
        if self.protected:
            return "protected"
        if self.changed:
            return "changed"
        return "unchanged"

    def to_plan_item(self, *, channel_id: int | str = "", category_id: int | str = "") -> dict[str, Any]:
        return {
            "channel_id": str(channel_id or ""),
            "category_id": str(category_id or ""),
            "kind": self.kind,
            "before": self.before,
            "after": self.after,
            "base_name": self.base_name,
            "status": self.status,
            "protected": self.protected,
            "warnings": list(self.warnings),
            "blockers": list(self.blockers),
            "substitutions": [s.__dict__ for s in self.substitutions],
            "readability_score": self.readability_score,
            "mobile_score": self.mobile_score,
            "clutter_score": self.clutter_score,
        }


SEPARATOR_LIBRARY: tuple[SeparatorSpec, ...] = (
    SeparatorSpec("bar_full", "Fullwidth Bar", "Clean Vertical", "｜"),
    SeparatorSpec("bar_thin", "Thin Bar", "Clean Vertical", "│"),
    SeparatorSpec("bar_heavy", "Heavy Bar", "Clean Vertical", "┃", clutter=1),
    SeparatorSpec("bar_dashed", "Dashed Bar", "Clean Vertical", "┆", clutter=1),
    SeparatorSpec("bar_dotted", "Dotted Bar", "Clean Vertical", "┊", clutter=1),
    SeparatorSpec("bar_light_left", "Light Left Bar", "Clean Vertical", "╏", clutter=1),
    SeparatorSpec("bar_light_dotted", "Light Dotted Bar", "Clean Vertical", "╎", clutter=1),
    SeparatorSpec("bar_medium", "Medium Vertical", "Clean Vertical", "❘", clutter=1),
    SeparatorSpec("bar_bold", "Bold Vertical", "Clean Vertical", "❙", clutter=1),
    SeparatorSpec("bar_block", "Block Vertical", "Clean Vertical", "❚", safety="balanced", clutter=2),
    SeparatorSpec("bar_triple_dash", "Triple Dash Bar", "Clean Vertical", "┋", safety="balanced", clutter=2),
    SeparatorSpec("bar_triple", "Triple Bar", "Clean Vertical", "┇", safety="balanced", clutter=2),
    SeparatorSpec("dash", "Dash", "Minimal", "-"),
    SeparatorSpec("en_dash", "En Dash", "Minimal", "–"),
    SeparatorSpec("em_dash", "Em Dash", "Minimal", "—", clutter=1),
    SeparatorSpec("middle_dot", "Middle Dot", "Minimal", "·"),
    SeparatorSpec("bullet", "Bullet", "Minimal", "•", clutter=1),
    SeparatorSpec("katakana_dot", "Katakana Dot", "Minimal", "・"),
    SeparatorSpec("colon", "Colon", "Minimal", ":"),
    SeparatorSpec("single_angle", "Single Angle", "Minimal", "›"),
    SeparatorSpec("double_angle", "Double Angle", "Minimal", "»", safety="balanced", clutter=1),
    SeparatorSpec("wave", "Wave", "Minimal", "⌁", safety="balanced", clutter=1),
    SeparatorSpec("small_dot", "Small Dot", "Aesthetic", "﹒"),
    SeparatorSpec("presentation_bar", "Presentation Bar", "Aesthetic", "︱"),
    SeparatorSpec("double_vertical", "Double Vertical", "Aesthetic", "〢", safety="balanced", clutter=1),
    SeparatorSpec("right_comma", "Right Comma", "Aesthetic", "⸝", safety="balanced", clutter=1),
    SeparatorSpec("left_comma", "Left Comma", "Aesthetic", "⸜", safety="balanced", clutter=1),
    SeparatorSpec("sparkle_small", "Small Sparkle", "Aesthetic", "⊹", safety="balanced", clutter=1),
    SeparatorSpec("sparkle", "Sparkle", "Aesthetic", "✦", safety="balanced", clutter=2),
    SeparatorSpec("sparkle_thin", "Thin Sparkle", "Aesthetic", "✧", safety="balanced", clutter=2),
    SeparatorSpec("star", "Star", "Aesthetic", "⋆", safety="balanced", clutter=2),
    SeparatorSpec("diamond_star", "Diamond Star", "Aesthetic", "⟡", safety="balanced", clutter=2),
    SeparatorSpec("bracket_corner", "Corner Brackets", "Brackets", template="「{emoji}」{name}", clutter=1),
    SeparatorSpec("bracket_white_corner", "White Corner Brackets", "Brackets", template="『{emoji}』{name}", clutter=1),
    SeparatorSpec("bracket_tortoise", "Tortoise Brackets", "Brackets", template="〔{emoji}〕{name}", clutter=1),
    SeparatorSpec("bracket_lenticular", "Lenticular Brackets", "Brackets", template="【{emoji}】{name}", clutter=1),
    SeparatorSpec("bracket_white_lenticular", "White Lenticular Brackets", "Brackets", template="〖{emoji}〗{name}", safety="balanced", clutter=2),
    SeparatorSpec("bracket_soft", "Soft Brackets", "Brackets", template="꒰{emoji}꒱{name}", safety="balanced", clutter=2),
    SeparatorSpec("tri_right", "Right Triangle", "Gaming / Tech", "▸", clutter=1),
    SeparatorSpec("tri_small", "Small Triangle", "Gaming / Tech", "▹", clutter=1),
    SeparatorSpec("tri_outline", "Outline Triangle", "Gaming / Tech", "▷", clutter=1),
    SeparatorSpec("tri_filled", "Filled Triangle", "Gaming / Tech", "▶", safety="balanced", clutter=2),
    SeparatorSpec("angle_math", "Math Angle", "Gaming / Tech", "⟩", clutter=1),
    SeparatorSpec("hex", "Hex", "Gaming / Tech", "⌬", safety="balanced", clutter=2),
    SeparatorSpec("tech_wave", "Tech Wave", "Gaming / Tech", "⌁", safety="balanced", clutter=1),
    SeparatorSpec("tech_corner", "Tech Corner", "Gaming / Tech", "⌐", safety="balanced", clutter=1),
    SeparatorSpec("tech_square", "Tech Square", "Gaming / Tech", "⌑", safety="balanced", clutter=2),
    SeparatorSpec("tech_hash", "Tech Hash", "Gaming / Tech", "⌗", safety="balanced", clutter=2),
    SeparatorSpec("premium_sparkle", "Premium Sparkle", "Loud / Premium", "✦", safety="balanced", clutter=2),
    SeparatorSpec("premium_thin_sparkle", "Premium Thin Sparkle", "Loud / Premium", "✧", safety="balanced", clutter=2),
    SeparatorSpec("premium_star", "Premium Star", "Loud / Premium", "✪", safety="decorative", clutter=3),
    SeparatorSpec("premium_diamond_filled", "Filled Diamond", "Loud / Premium", "❖", safety="decorative", clutter=3),
    SeparatorSpec("premium_black_diamond", "Black Diamond", "Loud / Premium", "◆", safety="balanced", clutter=2),
    SeparatorSpec("premium_diamond", "Diamond", "Loud / Premium", "◈", safety="decorative", clutter=3),
    SeparatorSpec("premium_white_diamond", "White Diamond", "Loud / Premium", "◇", safety="balanced", clutter=2),
    SeparatorSpec("premium_star_diamond", "Star Diamond", "Loud / Premium", "⟡", safety="balanced", clutter=2),
    SeparatorSpec("premium_burst", "Burst", "Loud / Premium", "✺", safety="decorative", clutter=3),
    SeparatorSpec("none", "No Separator", "Minimal", "", template="{emoji}{name}"),
)
SEPARATORS_BY_ID = {spec.id: spec for spec in SEPARATOR_LIBRARY}

CATEGORY_FRAMES: tuple[CategoryFrameSpec, ...] = (
    CategoryFrameSpec("line", "Clean Line", "─── {emoji} {name} ───", clutter=1),
    CategoryFrameSpec("heavy_line", "Heavy Line", "━━━ {emoji} {name} ━━━", clutter=2),
    CategoryFrameSpec("top_box", "Top Box", "╭── {emoji} {name} ──╮", safety="balanced", clutter=2),
    CategoryFrameSpec("bottom_box", "Bottom Box", "╰── {emoji} {name} ──╯", safety="balanced", clutter=2),
    CategoryFrameSpec("dreamy", "Dreamy Stars", "⋆｡°✩ {emoji} {name} ✩°｡⋆", safety="decorative", clutter=4),
    CategoryFrameSpec("premium_line", "Premium Line", "✦──── {emoji} {name} ────✦", safety="decorative", clutter=4),
    CategoryFrameSpec("box", "Box", "╔══ {emoji} {name} ══╗", safety="decorative", clutter=4),
    CategoryFrameSpec("lenticular", "Lenticular", "【 {emoji} {name} 】", safety="balanced", clutter=2),
    CategoryFrameSpec("corner", "Corner", "「 {emoji} {name} 」", safety="balanced", clutter=2),
    CategoryFrameSpec("plain", "Plain", "{emoji} {name}"),
)
CATEGORY_FRAMES_BY_ID = {spec.id: spec for spec in CATEGORY_FRAMES}

THEMES: tuple[ThemePreset, ...] = (
    ThemePreset("420_lounge", "🍃 420 Lounge", "line", "bar_full", "normal", "420_lounge"),
    ThemePreset("gothic_clean", "🕯 Gothic Clean", "line", "bar_full", "fraktur", "gothic"),
    ThemePreset("lit_hype", "🔥 Lit / Hype", "premium_line", "premium_sparkle", "bold_sans", "lit"),
    ThemePreset("cyber_bot_hub", "🤖 Cyber Bot Hub", "heavy_line", "tri_right", "monospace", "bot_utility"),
    ThemePreset("gaming_arcade", "🎮 Gaming Arcade", "premium_line", "tri_right", "bold_sans", "gaming"),
    ThemePreset("chill_social", "🌊 Chill Social", "line", "small_dot", "normal", "social"),
    ThemePreset("premium_clean", "💎 Premium Clean", "lenticular", "bar_thin", "serif_bold", "premium"),
    ThemePreset("staff_security", "🛡 Staff / Security", "heavy_line", "bar_heavy", "bold_sans", "staff_security"),
    ThemePreset("support_ticket", "🎫 Support / Ticket Server", "line", "bar_full", "normal", "ticket_support"),
)
THEMES_BY_ID = {theme.id: theme for theme in THEMES}

ICON_PACKS: dict[str, dict[str, str]] = {
    "420_lounge": {"announcements": "📢", "welcome": "👋", "rules": "📜", "verification": "🔐", "verify": "🔐", "support": "🎫", "profile": "🎭", "voice": "🎙️", "general": "💬", "chat": "💬", "high-thoughts": "🧠", "thoughts": "🧠", "memes": "🤡", "munchies": "🍔", "food": "🍔", "glass": "📸", "setups": "📸", "smoke": "🌬", "music": "🎧", "lounge": "🍃"},
    "bot_utility": {"bot": "🤖", "commands": "⌨️", "logs": "📋", "log": "📋", "status": "🟢", "setup": "🛠️", "dashboard": "📊", "tickets": "🎫", "transcripts": "🧾", "archive": "🗄️", "audit": "📋"},
    "lit": {"announcements": "🚨", "general": "🔥", "media": "📸", "clips": "🎬", "events": "🎉", "wins": "🏆"},
    "gothic": {"rules": "📜", "announcements": "🕯️", "general": "🌙", "lounge": "🦇", "staff": "🛡️", "archive": "⚰️"},
    "gaming": {"general": "🎮", "clips": "🎬", "news": "📰", "cod": "🎯", "loadouts": "🔫", "arcade": "🕹️"},
    "social": {"general": "💬", "welcome": "👋", "introductions": "🙋", "media": "📸", "music": "🎧", "voice": "🔊"},
    "staff_security": {"staff": "🛡️", "mod": "👮", "logs": "📋", "reports": "🚨", "audit": "🧾", "security": "🔐"},
    "ticket_support": {"support": "🎫", "tickets": "🎫", "transcripts": "🧾", "archive": "🗄️", "claims": "🙋", "help": "🛟"},
    "chill": {"general": "🌊", "lounge": "🛋️", "music": "🎧", "photos": "📸", "voice": "🔊"},
    "premium": {"announcements": "💎", "general": "💬", "rules": "📜", "support": "🎫", "staff": "🛡️", "events": "✨"},
}

INTENT_ICONS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("announcement", "announce", "news", "updates"), "📢"), (("welcome", "start", "intro"), "👋"),
    (("rule", "guidelines", "terms"), "📜"), (("verify", "verification", "access"), "🔐"),
    (("support", "ticket", "help"), "🎫"), (("profile", "pronoun", "identity", "roles"), "🎭"),
    (("voice", "vc", "call"), "🎙️"), (("general", "chat", "lounge"), "💬"),
    (("meme", "funny"), "🤡"), (("munch", "food", "snack"), "🍔"),
    (("glass", "setup", "photo", "media"), "📸"), (("game", "gaming", "arcade", "cod"), "🎮"),
    (("bot", "command"), "🤖"), (("log", "audit", "modlog"), "📋"), (("archive", "transcript"), "🗄️"),
)


def safe_str(value: Any, default: str = "") -> str:
    try:
        if value is None:
            return default
        text = str(value).strip()
        return text if text else default
    except Exception:
        return default


def _runtime_unicode_map(style: str) -> dict[str, str]:
    try:
        from stoney_verify.services.channel_builder_runtime import _unicode_map  # type: ignore
        return dict(_unicode_map(style))
    except Exception:
        return {}


def fallback_ladder(style: str) -> tuple[str, ...]:
    style = safe_str(style or "normal").lower().replace("-", "_")
    close = {
        "script": ("script", "bold_script", "serif_italic", "serif_bold_italic", "bold_sans", "monospace", "fullwidth"),
        "bold_script": ("bold_script", "script", "serif_bold_italic", "serif_italic", "bold_sans", "monospace", "fullwidth"),
        "fraktur": ("fraktur", "bold_fraktur", "serif_bold", "bold_sans", "monospace", "fullwidth"),
        "bold_fraktur": ("bold_fraktur", "fraktur", "serif_bold", "bold_sans", "monospace", "fullwidth"),
        "serif_italic": ("serif_italic", "serif_bold_italic", "italic_sans", "bold_italic_sans", "bold_sans", "monospace", "fullwidth"),
        "serif_bold_italic": ("serif_bold_italic", "serif_italic", "bold_italic_sans", "bold_sans", "monospace", "fullwidth"),
        "small_caps": ("small_caps", "bold_sans", "monospace", "fullwidth"),
        "parenthesized": ("parenthesized", "circled", "bold_sans", "monospace", "fullwidth"),
        "circled": ("circled", "parenthesized", "bold_sans", "monospace", "fullwidth"),
    }
    seen: list[str] = []
    for item in (*close.get(style, (style, "bold_sans", "monospace", "fullwidth")), "bold_sans", "monospace", "fullwidth", "normal"):
        clean = safe_str(item or "normal").lower().replace("-", "_")
        if clean and clean not in seen:
            seen.append(clean)
    return tuple(seen)


def _has_forbidden_invisible(value: str) -> bool:
    return any(unicodedata.category(ch) in {"Cf", "Cc"} for ch in str(value or ""))


def strip_invisible(value: Any) -> str:
    return "".join(ch for ch in safe_str(value) if not _has_forbidden_invisible(ch))


def validate_separator(spec_or_value: SeparatorSpec | str | None) -> tuple[bool, list[str]]:
    value = spec_or_value.value if isinstance(spec_or_value, SeparatorSpec) else safe_str(spec_or_value)
    template = spec_or_value.template if isinstance(spec_or_value, SeparatorSpec) else ""
    warnings: list[str] = []
    if isinstance(spec_or_value, str) and not value:
        return False, ["Separator cannot be blank unless the explicit No Separator option is selected."]
    if _has_forbidden_invisible(value) or _has_forbidden_invisible(template):
        return False, ["Separator contains invisible/control characters that can hide moderation context."]
    if len(value) > 4:
        warnings.append("Separator is long and may be hard to read on mobile.")
    return True, warnings


def separator_preview(separator_id: str, *, emoji: str = "📢", name: str = "announcements") -> str:
    spec = SEPARATORS_BY_ID.get(separator_id) or SEPARATORS_BY_ID["bar_full"]
    return spec.template.format(emoji=emoji, separator=spec.value, name=name)


def category_frame_preview(frame_id: str, *, emoji: str = "🛰", name: str = "central-command") -> str:
    spec = CATEGORY_FRAMES_BY_ID.get(frame_id) or CATEGORY_FRAMES_BY_ID["line"]
    return spec.template.format(emoji=emoji, name=name).strip()[:DISCORD_NAME_LIMIT]


def _reverse_font_map() -> dict[str, str]:
    reverse: dict[str, str] = {}
    for style in FONT_STYLES:
        for plain, glyph in _runtime_unicode_map(style).items():
            # Critical bug fix: some styles intentionally use ASCII letters as
            # glyphs, e.g. upside-down maps b -> q and u -> n. Mapping ASCII
            # glyphs back would corrupt normal names like announcements into
            # unreadable text. Only reverse non-ASCII styled glyphs.
            if glyph and glyph != plain and not glyph.isascii():
                reverse.setdefault(glyph, plain)
    return reverse


def strip_known_unicode_fonts(value: Any) -> str:
    raw = strip_invisible(value)
    reverse = _reverse_font_map()
    partially_reversed = "".join(reverse.get(ch, ch) for ch in raw)
    return unicodedata.normalize("NFKC", partially_reversed)


def _all_separator_values() -> list[str]:
    values = {spec.value for spec in SEPARATOR_LIBRARY if spec.value}
    values.update({"|", "┃", "│", "｜", "・", "·", "•", "﹒", "✦", "✧", "⋆", "⟡", "▸", "▹", "▷", "▶", "›", "»"})
    return sorted(values, key=len, reverse=True)


def _strip_category_frame(value: str) -> str:
    text = safe_str(value)
    text = re.sub(r"^[\s─━═╭╮╰╯╔╗【】「」✦⋆｡°✩]+", "", text)
    text = re.sub(r"[\s─━═╭╮╰╯╔╗【】「」✦⋆｡°✩]+$", "", text)
    return text.strip()


def _is_emojiish(ch: str) -> bool:
    if not ch:
        return False
    cp = ord(ch)
    return unicodedata.category(ch) == "So" or 0x1F000 <= cp <= 0x1FAFF or 0x2600 <= cp <= 0x27BF


def _strip_leading_icon(value: str) -> tuple[str, str]:
    text = safe_str(value)
    bracket_match = re.match(r"^([「『〔【〖꒰]\s*[^\w\s-]+\s*[」』〕】〗꒱])\s*(.*)$", text)
    if bracket_match:
        icon = re.sub(r"[「『〔【〖꒰」』〕】〗꒱\s]", "", bracket_match.group(1))
        return icon, bracket_match.group(2).strip()
    chars = list(text)
    icon_chars: list[str] = []
    index = 0
    variation = chr(0xFE0F)
    joiner = chr(0x200D)
    while index < len(chars):
        ch = chars[index]
        if _is_emojiish(ch) or (icon_chars and ch in {variation, joiner}):
            icon_chars.append(ch)
            index += 1
            continue
        break
    return "".join(icon_chars).strip(), "".join(chars[index:]).strip()


def normalize_base_name(value: Any, *, default: str = "channel") -> str:
    text = _strip_category_frame(strip_known_unicode_fonts(value))
    _icon, text = _strip_leading_icon(text)
    for sep in _all_separator_values():
        text = text.replace(sep, "-")
    text = text.replace("&", " and ").replace("_", "-").replace("'", "").replace("`", "").replace("’", "")
    normalized = re.sub(r"-+", "-", "".join(ch if ch.isalnum() else "-" for ch in text)).strip("-").lower()
    return normalized or default


def parse_channel_name(value: Any, *, kind: str = "text") -> dict[str, Any]:
    before = safe_str(value)
    stripped = strip_known_unicode_fonts(before)
    body = _strip_category_frame(stripped) if kind == "category" else stripped
    emoji, remainder = _strip_leading_icon(body)
    matched_separator = ""
    for sep in _all_separator_values():
        if remainder.startswith(sep):
            matched_separator = sep
            remainder = remainder[len(sep):].strip()
            break
    base = normalize_base_name(remainder or body or before)
    return {
        "emoji": emoji,
        "separator": matched_separator,
        "base_name": base,
        "styled_unicode_name": stripped != before,
        "category_frame": kind == "category" and body != stripped,
        "duplicate_emojis": bool(emoji and _strip_leading_icon(remainder)[0]),
        "duplicate_separators": any((sep + sep) in before for sep in _all_separator_values()),
        "raw": before,
    }


def transform_text_safe(value: Any, font: str, *, fallback_order: Iterable[str] | None = None) -> tuple[str, list[TransformSubstitution]]:
    plain = safe_str(value)
    style = safe_str(font or "normal").lower().replace("-", "_")
    if style not in FONT_STYLES:
        style = "normal"
    if style == "normal":
        return plain, []
    order = tuple(fallback_order or fallback_ladder(style))
    requested_map = _runtime_unicode_map(style)
    out: list[str] = []
    substitutions: list[TransformSubstitution] = []
    for ch in plain:
        if not ch.isalnum():
            out.append(ch)
            continue
        requested = requested_map.get(ch, ch) if requested_map else ch
        if requested != ch:
            out.append(requested)
            continue
        replacement = ch
        replacement_style = "normal"
        for candidate in order:
            if candidate in {style, "requested", "closest"}:
                continue
            if candidate == "normal":
                break
            glyph = _runtime_unicode_map(candidate).get(ch, ch)
            if glyph != ch:
                replacement = glyph
                replacement_style = candidate
                break
        out.append(replacement)
        substitutions.append(TransformSubstitution(ch, style, replacement_style, replacement, "requested font has no distinct glyph for this character"))
    final = "".join(out)
    return final or plain, substitutions


def _already_semantically_matches_design(before: str, *, base: str, font: str, expected_after: str) -> bool:
    """Return True when a channel already visually matches the selected design.

    This prevents the preview from trying to re-normalize channels that already
    have the selected font/base text but differ only by minor separator/frame
    decoration. Full exact-layout enforcement can be added later as a separate
    explicit mode; the default design repair should avoid needless churn.
    """

    before_text = safe_str(before)
    if not before_text:
        return False

    before_base = normalize_base_name(before_text, default="")
    expected_base = normalize_base_name(expected_after, default="")
    wanted_base = normalize_base_name(base, default="")

    if wanted_base and before_base not in {wanted_base, expected_base}:
        return False

    clean_font = safe_str(font or "normal").lower().replace("-", "_")
    if clean_font == "normal":
        return before_base == expected_base

    expected_font_text, _subs = transform_text_safe(
        wanted_base or expected_base,
        clean_font,
        fallback_order=fallback_ladder(clean_font),
    )

    # If the already-visible name contains the selected styled base text, the
    # font/base are already correct. Do not churn just to normalize separators.
    return bool(expected_font_text and expected_font_text in strip_invisible(before_text))


def _theme(theme_id: str | None) -> ThemePreset:
    return THEMES_BY_ID.get(safe_str(theme_id or "gothic_clean"), THEMES_BY_ID["gothic_clean"])


def suggested_icon(base_name: str, *, icon_pack: str = "420_lounge", existing: str = "", mode: str = "replace_missing") -> str:
    if mode == "keep_existing" and existing:
        return existing
    if mode == "clear":
        return ""
    if mode == "replace_missing" and existing:
        return existing
    base = normalize_base_name(base_name)
    pack = ICON_PACKS.get(icon_pack, {})
    parts = set(base.split("-")) | {base}
    for key, icon in pack.items():
        if key in parts or key in base:
            return icon
    for keywords, icon in INTENT_ICONS:
        if any(keyword in base for keyword in keywords):
            return icon
    return existing or "#️⃣"


def _protection_mode_for(base_name: str, protection_rules: Mapping[str, str] | None = None) -> str:
    base = normalize_base_name(base_name)
    if protection_rules:
        for key, value in protection_rules.items():
            if normalize_base_name(key) == base:
                mode = safe_str(value).lower().replace("-", "_")
                return mode if mode in PROTECTION_MODES else "never"
    if base in DEFAULT_PROTECTED_NAMES:
        return "never"
    return "full"


def _readability(before: str, after: str, *, font: str, frame_clutter: int = 0, separator_clutter: int = 0) -> tuple[int, int, int, list[str]]:
    warnings: list[str] = []
    length = len(after)
    clutter = max(0, frame_clutter + separator_clutter)
    readability = 100
    mobile = 100
    if length > 32:
        readability -= 10
        mobile -= 15
        warnings.append("Long name may be cramped on mobile.")
    if length > 60:
        readability -= 20
        mobile -= 25
        warnings.append("Very long name should use normal text or a lighter frame.")
    if font in RISKY_FONTS:
        readability -= 18
        mobile -= 10
        warnings.append("Decorative font may be harder to read or search.")
    if clutter >= 4:
        readability -= 12
        mobile -= 12
        warnings.append("This style is visually busy; Safe Mode is recommended for important channels.")
    if len(after) > len(before) + 20:
        mobile -= 8
    return max(0, readability), max(0, mobile), clutter, warnings


def build_styled_name(
    current_name: Any,
    *,
    kind: str = "text",
    theme_id: str = "gothic_clean",
    strength: int = 2,
    saved_base_name: str | None = None,
    icon_mode: str = "replace_missing",
    protection_rules: Mapping[str, str] | None = None,
    separator_id: str | None = None,
    category_frame_id: str | None = None,
    font: str | None = None,
    emoji_override: str | None = None,
    exact_match: bool = False,
) -> DesignNameResult:
    before = safe_str(current_name)
    parsed = parse_channel_name(before, kind=kind)
    base = normalize_base_name(saved_base_name or parsed["base_name"] or before)
    theme = _theme(theme_id)
    protection = _protection_mode_for(base, protection_rules)
    result = DesignNameResult(before=before, after=before, base_name=base, kind=kind, protected=protection == "never")
    if result.protected:
        result.warnings.append("Safe skip — protected ticket/log/system item. This is intentional and does not block Apply.")
        return result
    try:
        strength = max(1, min(5, int(strength)))
    except Exception:
        strength = 2
    use_emoji = strength >= 1
    use_separator = strength >= 2 and protection in {"separator_only", "full", "font_only", "category_frame_only"}
    use_category_frame = kind == "category" and strength in {3, 5} and protection in {"category_frame_only", "full"}

    # A theme-selected font is part of the theme identity. Strength controls how
    # much structure/clutter is added; it must not silently turn Goth/Clean back
    # into plain text.
    requested_font = safe_str(font or theme.font or "normal").lower().replace("-", "_")
    if requested_font not in FONT_STYLES:
        requested_font = "normal"
    use_font = (
        strength >= 2
        and requested_font != "normal"
        and protection in {"font_only", "full", "category_frame_only"}
    )
    chosen_font = requested_font if use_font else "normal"
    name_text, substitutions = transform_text_safe(base, chosen_font, fallback_order=fallback_ladder(chosen_font))
    if use_emoji and emoji_override is not None:
        emoji = strip_invisible(safe_str(emoji_override))[:16]
    else:
        emoji = suggested_icon(base, icon_pack=theme.icon_pack, existing=safe_str(parsed.get("emoji")), mode=icon_mode) if use_emoji else ""
    sep_spec = SEPARATORS_BY_ID.get(separator_id or theme.channel_separator) or SEPARATORS_BY_ID["bar_full"]
    frame_spec = CATEGORY_FRAMES_BY_ID.get(category_frame_id or theme.category_frame) or CATEGORY_FRAMES_BY_ID["line"]
    ok, sep_warnings = validate_separator(sep_spec)
    if not ok:
        result.blockers.extend(sep_warnings)
        return result
    result.warnings.extend(sep_warnings)
    if kind == "category" and use_category_frame:
        after = frame_spec.template.format(emoji=emoji, name=name_text).strip()
    elif use_separator:
        after = sep_spec.template.format(emoji=emoji, separator=sep_spec.value, name=name_text).strip()
    elif use_emoji:
        after = f"{emoji}{name_text}".strip()
    else:
        after = name_text.strip()
    after = strip_invisible(after).strip()
    if not after and before:
        after = base or "channel"
        result.warnings.append("Generated name was empty, so a readable base name was used.")
    if _has_forbidden_invisible(after):
        result.blockers.append("Final name contains invisible/control characters.")
    if len(after) > DISCORD_NAME_LIMIT:
        result.blockers.append(f"Final name is too long for Discord ({len(after)}/{DISCORD_NAME_LIMIT}).")
    if not after:
        result.blockers.append("Final name would be empty.")
    readability, mobile, clutter, score_warnings = _readability(before, after, font=chosen_font, frame_clutter=frame_spec.clutter if use_category_frame else 0, separator_clutter=sep_spec.clutter if use_separator else 0)
    result.after = after[:DISCORD_NAME_LIMIT]

    if (
        result.after
        and not result.blockers
        and result.after != before
        and not bool(exact_match)
        and _already_semantically_matches_design(before, base=base, font=chosen_font, expected_after=result.after)
    ):
        result.after = before
        result.changed = False
        result.warnings.append("Already matches the selected font/base; no rename needed.")
    else:
        result.changed = bool(result.after and result.after != before and not result.blockers)

    result.emoji = emoji
    result.separator_id = sep_spec.id if use_separator else ""
    result.font = chosen_font
    result.category_frame_id = frame_spec.id if use_category_frame else ""
    result.substitutions = substitutions
    result.readability_score = readability
    result.mobile_score = mobile
    result.clutter_score = clutter
    result.warnings.extend(score_warnings)
    if substitutions:
        result.warnings.append("Auto-Safe Transform used fallback glyphs for unsupported letters; rename can still be applied.")
    return result


def detect_duplicate_outputs(items: list[dict[str, Any]]) -> list[str]:
    seen: dict[str, str] = {}
    duplicates: list[str] = []
    for item in items:
        if item.get("status") == "failed" or item.get("protected"):
            continue
        key = normalize_base_name(item.get("after"), default="")
        if not key:
            continue
        before = safe_str(item.get("before"))
        if key in seen:
            duplicates.append(f"`{seen[key]}` and `{before}` would both become `{item.get('after')}`")
        else:
            seen[key] = before
    return duplicates


def summarize_plan(items: list[dict[str, Any]]) -> dict[str, int]:
    summary = {"total": len(items), "changed": 0, "unchanged": 0, "protected": 0, "failed": 0, "warnings": 0}
    for item in items:
        status = safe_str(item.get("status"), "unchanged")
        if status in summary:
            summary[status] += 1
        if item.get("warnings"):
            summary["warnings"] += 1
    return summary


def preview_lines(items: list[dict[str, Any]], *, filter_mode: str = "all", limit: int = 12) -> list[str]:
    rows: list[str] = []
    for item in items:
        status = safe_str(item.get("status"), "unchanged")
        warnings = list(item.get("warnings") or [])
        blockers = list(item.get("blockers") or [])
        if filter_mode == "changed" and status != "changed":
            continue
        if filter_mode == "warnings" and not warnings:
            continue
        if filter_mode == "protected" and status != "protected":
            continue
        if filter_mode == "failed" and status != "failed":
            continue
        icon = {"changed": "✅", "protected": "🔒", "failed": "❌", "unchanged": "▫️"}.get(status, "▫️")
        line = f"{icon} `{item.get('before')}` → `{item.get('after')}`"
        if blockers:
            line += f" — {blockers[0]}"
        elif warnings:
            line += f" — ⚠️ {warnings[0]}"
        rows.append(line[:260])
        if len(rows) >= limit:
            break
    return rows or ["No matching preview rows."]


def design_score(items: list[dict[str, Any]]) -> dict[str, int | str]:
    if not items:
        return {"readability": 100, "mobile_fit": 100, "clutter_risk": 0, "length_risk": 0, "duplicate_risk": 0, "accessibility": "Good"}
    readable = int(sum(int(item.get("readability_score") or 100) for item in items) / max(1, len(items)))
    mobile = int(sum(int(item.get("mobile_score") or 100) for item in items) / max(1, len(items)))
    clutter = max(int(item.get("clutter_score") or 0) for item in items)
    length_risk = sum(1 for item in items if len(safe_str(item.get("after"))) > 60)
    duplicate_risk = len(detect_duplicate_outputs(items))
    accessibility = "Needs review" if readable < 75 or any(item.get("substitutions") for item in items) else "Good"
    return {"readability": readable, "mobile_fit": mobile, "clutter_risk": clutter, "length_risk": length_risk, "duplicate_risk": duplicate_risk, "accessibility": accessibility}


__all__ = [
    "CATEGORY_FRAMES", "DEFAULT_DELAY_SECONDS", "DEFAULT_PROTECTED_NAMES", "DISCORD_NAME_LIMIT", "FONT_STYLES",
    "ICON_PACKS", "MAX_PLAN_ITEMS", "SEPARATOR_LIBRARY", "THEMES", "build_styled_name", "category_frame_preview",
    "design_score", "detect_duplicate_outputs", "fallback_ladder", "normalize_base_name", "parse_channel_name",
    "preview_lines", "separator_preview", "strip_known_unicode_fonts", "summarize_plan", "suggested_icon",
    "transform_text_safe", "validate_separator",
]
