from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "stoney_verify/profile_signature_style.py"
source = PATH.read_text(encoding="utf-8")

old_import = '''from .welcome_card_typography_engine import (
    COLOR_PRESETS,
    FONT_STYLES,
    parse_hex_color,
)
'''
new_import = '''from .welcome_card_typography_engine import (
    BUILTIN_THEMES,
    COLOR_PRESETS,
    DEFAULT_THEME_KEY,
    FONT_STYLES,
    parse_hex_color,
)
'''
if source.count(old_import) != 1:
    raise SystemExit("profile theme import marker changed")
source = source.replace(old_import, new_import, 1)

old_keys = "PROFILE_THEME_KEYS = frozenset(PROFILE_THEME_SPECS)"
new_keys = "PROFILE_THEME_KEYS = frozenset(set(PROFILE_THEME_SPECS) | set(BUILTIN_THEMES))"
if source.count(old_keys) != 1:
    raise SystemExit("profile theme key marker changed")
source = source.replace(old_keys, new_keys, 1)

old_default = '    "theme": "default",'
new_default = '    "theme": DEFAULT_THEME_KEY,'
if source.count(old_default) != 1:
    raise SystemExit("profile default theme marker changed")
source = source.replace(old_default, new_default, 1)

PATH.write_text(source, encoding="utf-8")
