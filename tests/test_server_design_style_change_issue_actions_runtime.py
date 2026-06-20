from __future__ import annotations

import sys
import types

fake_supabase = types.ModuleType("supabase")
fake_supabase.Client = object
fake_supabase.create_client = lambda *a, **k: None
sys.modules.setdefault("supabase", fake_supabase)

from stoney_verify.startup_guards import server_design_studio_command_guard as guard


def test_manual_emoji_fix_builds_separator_name():
    after, warnings, blockers = guard._style_change_after_with_manual_emoji(
        "share-deals",
        "bar_heavy",
        "💸",
    )

    assert blockers == []
    assert after == "💸┃share-deals"
    assert warnings


def test_manual_emoji_fix_keeps_fraktur_body():
    after, warnings, blockers = guard._style_change_after_with_manual_emoji(
        "𝔠𝔩𝔬𝔰𝔢𝔡-𝟎𝟎𝟎𝟓",
        "bar_heavy",
        "📦",
    )

    assert blockers == []
    assert after == "📦┃𝔠𝔩𝔬𝔰𝔢𝔡-𝟎𝟎𝟎𝟓"


def test_issue_lines_detect_missing_emoji():
    items = [
        {
            "status": "failed",
            "blockers": ["No leading emoji found. Separator-only change keeps emoji behavior unchanged."],
        }
    ]

    lines = guard._style_change_issue_lines(items)

    assert any("missing" in line.lower() or "icon" in line.lower() or "emoji" in line.lower() for line in lines)
    assert any("Choose Missing Icons" in line or "Missing Icons" in line for line in lines)
