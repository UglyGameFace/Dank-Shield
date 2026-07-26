from __future__ import annotations

import ast
from pathlib import Path


SOURCE_PATH = Path("stoney_verify/profile_signature_studio.py")


def test_profile_signature_studio_source_compiles() -> None:
    source = SOURCE_PATH.read_text(encoding="utf-8")
    ast.parse(source, filename=str(SOURCE_PATH))


def test_reset_buttons_use_a_discord_valid_unicode_emoji() -> None:
    source = SOURCE_PATH.read_text(encoding="utf-8")

    # U+21BA is a text symbol, not a Discord-valid component emoji. Discord
    # rejects the entire interaction response when it is serialized as one.
    assert 'emoji="↺"' not in source
    assert '@discord.ui.button(label="Reset My Look", emoji="🔄"' in source
    assert 'label="Reset Server Defaults", emoji="🔄"' in source
