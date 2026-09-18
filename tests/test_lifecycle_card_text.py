from __future__ import annotations

from types import SimpleNamespace

from stoney_verify.lifecycle_card_text import image_card_member, image_safe_text


def test_decorative_compatibility_alphabets_are_preserved_exactly() -> None:
    samples = (
        "𝔼𝕪𝕖𝕫 𝕆𝕗 𝔹𝕠𝕓",
        "𝓔𝔂𝓮𝔃 𝓞𝓯 𝓑𝓸𝓫",
        "𝙴𝚢𝚎𝚣 𝙾𝚏 𝙱𝚘𝚋",
        "PΛMELA",
        "𝓐𝓷𝓰𝓮𝓵♡",
    )
    for sample in samples:
        assert image_safe_text(sample, fallback="Member") == sample


def test_normal_unicode_accents_emoji_and_non_latin_text_are_preserved() -> None:
    samples = (
        "José 🚀",
        "東京",
        "玩家123",
        "محمد",
        "Женя",
        "👩🏽‍💻",
    )
    for sample in samples:
        assert image_safe_text(sample, fallback="Member") == sample


def test_image_text_cleanup_changes_whitespace_only() -> None:
    assert image_safe_text("  𝓐𝓷𝓰𝓮𝓵♡\n  420  ", fallback="Member") == "𝓐𝓷𝓰𝓮𝓵♡ 420"
    assert image_safe_text("", fallback="Member") == "Member"


def test_image_member_adapter_preserves_exact_name_and_server_copy() -> None:
    guild = SimpleNamespace(id=42, name="𝕋𝕙𝕖 𝟜𝟚𝟘 𝕃𝕠𝕓𝕓𝕪", member_count=73)
    member = SimpleNamespace(
        id=9,
        name="eyez",
        display_name="𝔼𝕪𝕖𝕫 𝕆𝕗 𝔹𝕠𝕓",
        guild=guild,
        mention="<@9>",
        display_avatar=object(),
    )

    adapted = image_card_member(member)

    assert adapted.display_name == "𝔼𝕪𝕖𝕫 𝕆𝕗 𝔹𝕠𝕓"
    assert adapted.guild.name == "𝕋𝕙𝕖 𝟜𝟚𝟘 𝕃𝕠𝕓𝕓𝕪"
    assert adapted.id == member.id
    assert adapted.mention == member.mention
    assert adapted.guild.id == guild.id
    assert member.display_name == "𝔼𝕪𝕖𝕫 𝕆𝕗 𝔹𝕠𝕓"
    assert member.guild.name == "𝕋𝕙𝕖 𝟜𝟚𝟘 𝕃𝕠𝕓𝕓𝕪"


def test_adapter_does_not_reintroduce_compatibility_normalization() -> None:
    import inspect
    import stoney_verify.lifecycle_card_text as module

    source = inspect.getsource(module.image_safe_text)
    assert "NFKC" not in source
    assert "unicodedata.normalize" not in source
