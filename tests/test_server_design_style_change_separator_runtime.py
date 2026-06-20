from __future__ import annotations

import sys
import types

fake_supabase = types.ModuleType("supabase")
fake_supabase.Client = object
fake_supabase.create_client = lambda *a, **k: None
sys.modules.setdefault("supabase", fake_supabase)

from stoney_verify.startup_guards import server_design_studio_command_guard as guard


def test_separator_only_adds_heavy_bar_without_changing_fraktur_body():
    before = "📢𝔞𝔫𝔫𝔬𝔲𝔫𝔠𝔢𝔪𝔢𝔫𝔱𝔰"
    after, warnings, blockers = guard._style_change_separator_after(before, "bar_heavy")

    assert blockers == []
    assert after == "📢┃𝔞𝔫𝔫𝔬𝔲𝔫𝔠𝔢𝔪𝔢𝔫𝔱𝔰"


def test_separator_only_replaces_old_separator_without_doubling():
    before = "👋│𝔴𝔢𝔩𝔠𝔬𝔪𝔢"
    after, warnings, blockers = guard._style_change_separator_after(before, "bar_heavy")

    assert blockers == []
    assert after == "👋┃𝔴𝔢𝔩𝔠𝔬𝔪𝔢"
    assert "│" not in after
    assert "┃┃" not in after


def test_separator_only_can_remove_separator():
    before = "🎫┃𝔰𝔲𝔭𝔭𝔬𝔯𝔱"
    after, warnings, blockers = guard._style_change_separator_after(before, "none")

    assert blockers == []
    assert after == "🎫𝔰𝔲𝔭𝔭𝔬𝔯𝔱"


def test_separator_only_blocks_no_emoji_when_adding_separator():
    before = "𝔤𝔢𝔫𝔢𝔯𝔞𝔩"
    after, warnings, blockers = guard._style_change_separator_after(before, "bar_heavy")

    assert after == before
    assert blockers
    assert "No leading emoji/icon" in blockers[0]


def test_separator_only_rejects_failed_hash_placeholder_square():
    before = "🔲𝔰𝔥𝔞𝔯𝔢-𝔡𝔢𝔞𝔩𝔰"
    after, warnings, blockers = guard._style_change_separator_after(before, "bar_heavy")

    assert after == before
    assert blockers
    assert "failed/unsupported #️⃣ placeholder" in blockers[0]


def test_manual_emoji_rejects_hash_keycap_as_channel_icon():
    after, warnings, blockers = guard._style_change_after_with_manual_emoji(
        "share-deals",
        "bar_heavy",
        "#️⃣",
    )

    assert blockers
    assert "not safe channel-name icons" in blockers[0]


def test_manual_emoji_allows_real_icon():
    after, warnings, blockers = guard._style_change_after_with_manual_emoji(
        "share-deals",
        "bar_heavy",
        "💸",
    )

    assert blockers == []
    assert after == "💸┃share-deals"


def test_style_change_corner_bracket_separator_applies_template():
    before = "🎮gaming-news"
    after, warnings, blockers = guard._style_change_separator_after(before, "bracket_corner")

    assert blockers == []
    assert after == "「🎮」gaming-news"


def test_style_change_square_bracket_separator_applies_template():
    before = "🎮gaming-news"
    after, warnings, blockers = guard._style_change_separator_after(before, "bracket_lenticular")

    assert blockers == []
    assert after == "【🎮】gaming-news"


def test_style_change_separator_preview_text_shows_brackets():
    assert guard._style_change_separator_preview_text("bracket_corner") == "「🎮」gaming-news"
    assert guard._style_change_separator_preview_text("bracket_lenticular") == "【🎮】gaming-news"


def test_style_change_corner_bracket_separator_applies_template():
    before = "🎮gaming-news"
    after, warnings, blockers = guard._style_change_separator_after(before, "bracket_corner")

    assert blockers == []
    assert after == "「🎮」gaming-news"


def test_style_change_square_bracket_separator_applies_template():
    before = "🎮gaming-news"
    after, warnings, blockers = guard._style_change_separator_after(before, "bracket_lenticular")

    assert blockers == []
    assert after == "【🎮】gaming-news"


def test_style_change_separator_preview_text_shows_brackets():
    assert guard._style_change_separator_preview_text("bracket_corner") == "「🎮」gaming-news"
    assert guard._style_change_separator_preview_text("bracket_lenticular") == "【🎮】gaming-news"
