from __future__ import annotations

from types import SimpleNamespace

from stoney_verify.lifecycle_card_text import image_card_member, image_safe_text


def test_decorative_compatibility_alphabets_become_readable_for_bitmap_fonts() -> None:
    assert image_safe_text("𝔼𝕪𝕖𝕫 𝕆𝕗 𝔹𝕠𝕓", fallback="Member") == "Eyez Of Bob"
    assert image_safe_text("𝓔𝔂𝓮𝔃 𝓞𝓯 𝓑𝓸𝓫", fallback="Member") == "Eyez Of Bob"
    assert image_safe_text("𝙴𝚢𝚎𝚣 𝙾𝚏 𝙱𝚘𝚋", fallback="Member") == "Eyez Of Bob"


def test_normal_unicode_accents_emoji_and_non_latin_text_are_preserved() -> None:
    assert image_safe_text("José 🚀", fallback="Member") == "José 🚀"
    assert image_safe_text("東京", fallback="Member") == "東京"


def test_image_member_adapter_changes_only_bitmap_facing_name_and_server_copy() -> None:
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

    assert adapted.display_name == "Eyez Of Bob"
    assert adapted.guild.name == "The 420 Lobby"
    assert adapted.id == member.id
    assert adapted.mention == member.mention
    assert adapted.guild.id == guild.id
    assert member.display_name == "𝔼𝕪𝕖𝕫 𝕆𝕗 𝔹𝕠𝕓"
    assert member.guild.name == "𝕋𝕙𝕖 𝟜𝟚𝟘 𝕃𝕠𝕓𝕓𝕪"
