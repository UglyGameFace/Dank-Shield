from __future__ import annotations

"""Full-name fitting contracts for modern and legacy Discord profile cards."""

from io import BytesIO

import pytest
from PIL import Image

import stoney_verify.profile_signature_live_renderer as live
import stoney_verify.profile_signature_renderer as legacy
import stoney_verify.welcome_card_typography_engine as typography


FULL_EMOJI_NAME = "🎃👻💀UglyGameFace💀👻🎃"
DECORATED_NAME = "『𝑼𝒈𝒍𝒚』✦𝕲𝖆𝖒𝖊𝕱𝖆𝖈𝖊✦"


@pytest.mark.parametrize("name", [
    "UGLYGAMEFACE-" * 20,
    DECORATED_NAME * 4,
    "W" * 90,
])
def test_styled_text_preserve_mode_never_replaces_username_with_ellipsis(name: str) -> None:
    style = typography._render_style("tech", None)
    whole, tile = typography._fitted_tile(
        name,
        style=style,
        start_size=64,
        min_size=20,
        max_width=480,
        max_height=76,
        role="name",
        primary=(150, 255, 80),
        secondary=(30, 180, 120),
        preserve_text=True,
    )
    assert whole == name
    assert tile.width <= 480
    assert tile.height <= 76
    assert tile.getbbox() is not None


@pytest.mark.parametrize("layout", ["classic", "minimal", "spotlight"])
def test_emoji_name_preserved_as_one_line_in_each_modern_layout(layout: str, monkeypatch) -> None:
    spec = live._LAYOUTS[layout]
    drawn: list[str] = []
    original_draw = live._draw_rich

    def spy(image, draw, xy, text, **kwargs):
        drawn.append(text)
        return original_draw(image, draw, xy, text, **kwargs)

    monkeypatch.setattr(live, "_draw_rich", spy)
    name = "🎃👻💀UglyGameFace💀👻🎃"
    tile = live._single_line_emoji_name_tile(
        name, spec=spec, palette=live.PROFILE_THEME_PALETTES["420_lobby"],
        assets={}, style_key="tech",
    )
    assert drawn == [name]
    assert "…" not in drawn[0] and "..." not in drawn[0]
    assert tile.size == (spec.content_right - spec.content_x - 24, 76)
    assert tile.getbbox() is not None


@pytest.mark.parametrize("layout", ["classic", "minimal", "spotlight"])
def test_unusually_long_emoji_name_is_rendered_whole_and_bounded(layout: str, monkeypatch) -> None:
    spec = live._LAYOUTS[layout]
    name = FULL_EMOJI_NAME * 12
    drawn: list[str] = []
    original_draw = live._draw_rich

    def spy(image, draw, xy, text, **kwargs):
        drawn.append(text)
        return original_draw(image, draw, xy, text, **kwargs)

    monkeypatch.setattr(live, "_draw_rich", spy)
    tile = live._single_line_emoji_name_tile(
        name,
        spec=spec,
        palette=live.PROFILE_THEME_PALETTES["420_lobby"],
        assets={},
        style_key="clean",
    )
    assert drawn == [name]
    assert tile.width == spec.content_right - spec.content_x - 24
    assert tile.height == 76
    assert tile.getbbox() is not None


@pytest.mark.parametrize("layout", ["classic", "minimal", "spotlight"])
def test_legacy_compact_card_renders_undivided_username(layout: str, monkeypatch) -> None:
    name = DECORATED_NAME * 5
    captured: list[tuple[str, bool]] = []
    original = legacy.render_styled_text_tile

    def spy(value, **kwargs):
        if kwargs.get("role") == "name":
            captured.append((value, kwargs.get("preserve_text", False)))
        return original(value, **kwargs)

    monkeypatch.setattr(legacy, "render_styled_text_tile", spy)
    data = legacy.render_profile_signature(
        avatar_bytes=b"",
        display_name=name,
        server_name="The 420 Lobby",
        role_labels=[],
        date_labels=[],
        platform_labels=[],
        style={"layout": layout, "font": "tech", "theme": "default"},
    )
    assert captured == [(name, True)]
    with Image.open(BytesIO(data)) as image:
        assert image.size == (legacy.SIGNATURE_WIDTH, legacy.SIGNATURE_HEIGHT)


@pytest.mark.parametrize("layout", ["classic", "minimal", "spotlight"])
def test_modern_styled_card_uses_preserve_mode(layout: str, monkeypatch) -> None:
    name = DECORATED_NAME * 4
    spec = live._LAYOUTS[layout]
    original = live.render_styled_text_tile
    captured = []

    def spy(value, **kwargs):
        captured.append((value, kwargs.get("preserve_text", False)))
        return original(value, **kwargs)

    monkeypatch.setattr(live, "render_styled_text_tile", spy)
    tile = live._bounded_name_tile(
        name,
        style_key="tech",
        spec=spec,
        palette=live.PROFILE_THEME_PALETTES["420_lobby"],
        custom_font_bytes=b"",
    )
    assert captured == [(name, True)]
    assert tile.width == spec.content_right - spec.content_x - 24
    assert tile.height == 76
    assert tile.getbbox() is not None


def test_rich_name_preserves_joined_emoji_as_complete_tokens() -> None:
    name = "🇺🇸👨‍👩‍👧‍👦👍🏽🎃CrypticSnipe👻"
    tokens = live._emoji_tokens(name)
    emojis = [word for word, is_emoji in tokens if is_emoji]
    assert emojis == ["🇺🇸", "👨‍👩‍👧‍👦", "👍🏽", "🎃", "👻"]
    assert "CrypticSnipe" in "".join(word for word, is_emoji in tokens if not is_emoji)


def test_non_name_typography_maintains_existing_truncation_contract() -> None:
    # Preserve the default shared typography behavior for welcome cards and
    # other consumers. Only profile user names opt into preserve_text=True.
    style = typography._render_style("tech", None)
    shortened, tile = typography._fitted_tile(
        "UPPERCASE-" * 20,
        style=style,
        start_size=46,
        min_size=20,
        max_width=150,
        max_height=55,
        role="subtitle",
        primary=(150, 255, 80),
        secondary=(30, 180, 120),
    )
    assert shortened.endswith("...") and len(shortened) < 20 * len("UPPERCASE-")
    assert tile.width <= 150
