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
    assert "𝔞𝔫𝔫" in after
    assert warnings


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
    assert "No leading emoji" in blockers[0]
