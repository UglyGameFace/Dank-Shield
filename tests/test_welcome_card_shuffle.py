from __future__ import annotations

from stoney_verify import welcome_card_service as service


def _cfg(mode: str) -> dict[str, object]:
    return {
        "welcome_card_theme": "cyber_neon",
        "welcome_card_font_style": "street",
        "welcome_card_color_mode": "profile",
        "welcome_card_custom_primary": "#123456",
        "welcome_card_custom_secondary": "#ABCDEF",
        "welcome_card_shuffle_mode": mode,
    }


def test_invalid_shuffle_mode_falls_back_to_off() -> None:
    assert service.configured_shuffle_mode(
        {"welcome_card_shuffle_mode": "not-a-real-mode"}
    ) == "off"


def test_shuffle_off_preserves_every_configured_value(monkeypatch) -> None:
    monkeypatch.setattr(
        service,
        "decode_custom_background",
        lambda _cfg: b"custom-background",
    )

    result = service._resolve_effective_welcome_style(
        guild_id=100,
        user_id=200,
        cfg=_cfg("off"),
        custom_font_present=False,
    )

    assert result == (
        "cyber_neon",
        b"custom-background",
        "street",
        "profile",
        "#123456",
        "#ABCDEF",
    )


def test_font_shuffle_preserves_background_theme_and_colors(monkeypatch) -> None:
    monkeypatch.setattr(
        service,
        "decode_custom_background",
        lambda _cfg: b"custom-background",
    )

    (
        theme_key,
        custom_background,
        font_style_key,
        color_mode,
        custom_primary,
        custom_secondary,
    ) = service._resolve_effective_welcome_style(
        guild_id=100,
        user_id=200,
        cfg=_cfg("fonts"),
        custom_font_present=False,
    )

    assert theme_key == "cyber_neon"
    assert custom_background == b"custom-background"
    assert font_style_key in service.FONT_STYLES
    assert color_mode == "profile"
    assert custom_primary == "#123456"
    assert custom_secondary == "#ABCDEF"


def test_theme_shuffle_uses_builtin_theme_and_skips_custom_background(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        service,
        "decode_custom_background",
        lambda _cfg: b"custom-background",
    )

    (
        theme_key,
        custom_background,
        font_style_key,
        color_mode,
        _primary,
        _secondary,
    ) = service._resolve_effective_welcome_style(
        guild_id=100,
        user_id=200,
        cfg=_cfg("themes"),
        custom_font_present=False,
    )

    assert theme_key in service.BUILTIN_THEMES
    assert custom_background is None
    assert font_style_key == "street"
    assert color_mode == "profile"


def test_everything_shuffle_selects_a_safe_named_palette(monkeypatch) -> None:
    monkeypatch.setattr(
        service,
        "decode_custom_background",
        lambda _cfg: b"custom-background",
    )

    (
        theme_key,
        custom_background,
        font_style_key,
        color_mode,
        custom_primary,
        custom_secondary,
    ) = service._resolve_effective_welcome_style(
        guild_id=100,
        user_id=200,
        cfg=_cfg("everything"),
        custom_font_present=False,
    )

    valid_palettes = {
        (preset.primary, preset.secondary)
        for preset in service.COLOR_PRESETS.values()
    }

    assert theme_key in service.BUILTIN_THEMES
    assert custom_background is None
    assert font_style_key in service.FONT_STYLES
    assert color_mode == "custom"
    assert (custom_primary, custom_secondary) in valid_palettes


def test_shuffle_is_stable_for_same_member(monkeypatch) -> None:
    monkeypatch.setattr(
        service,
        "decode_custom_background",
        lambda _cfg: None,
    )

    first = service._resolve_effective_welcome_style(
        guild_id=123,
        user_id=456,
        cfg=_cfg("fonts_themes"),
        custom_font_present=False,
    )
    second = service._resolve_effective_welcome_style(
        guild_id=123,
        user_id=456,
        cfg=_cfg("fonts_themes"),
        custom_font_present=False,
    )

    assert first == second


def test_shuffle_produces_variety_across_members(monkeypatch) -> None:
    monkeypatch.setattr(
        service,
        "decode_custom_background",
        lambda _cfg: None,
    )

    results = {
        service._resolve_effective_welcome_style(
            guild_id=123,
            user_id=user_id,
            cfg=_cfg("fonts_themes"),
            custom_font_present=False,
        )[:3]
        for user_id in range(1000, 1040)
    }

    assert len(results) > 1


def test_custom_uploaded_font_can_join_font_shuffle(monkeypatch) -> None:
    monkeypatch.setattr(
        service,
        "decode_custom_background",
        lambda _cfg: None,
    )

    observed_fonts = {
        service._resolve_effective_welcome_style(
            guild_id=999,
            user_id=user_id,
            cfg=_cfg("fonts"),
            custom_font_present=True,
        )[2]
        for user_id in range(1, 400)
    }

    assert service.CUSTOM_FONT_STYLE_KEY in observed_fonts
