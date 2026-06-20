from __future__ import annotations

import sys
import types

fake_supabase = types.ModuleType("supabase")
fake_supabase.Client = object
fake_supabase.create_client = lambda *a, **k: None
sys.modules.setdefault("supabase", fake_supabase)

from stoney_verify.startup_guards import server_design_studio_command_guard as guard


def _width(item) -> int:
    return 5 if item.__class__.__name__.endswith("Select") else 1


def test_exact_format_view_opens_without_discord_row_overflow():
    view = guard.ExactFormatEditorViewFactory(
        object(),
        "channel",
        123,
        {
            "font": "fraktur",
            "separator_id": "bar_heavy",
            "category_frame_id": "line",
            "strength": 5,
        },
    )

    rows = {}
    for child in view.children:
        row = int(getattr(child, "row", 0) or 0)
        rows[row] = rows.get(row, 0) + _width(child)

    assert all(0 <= row <= 4 for row in rows), rows
    assert all(width <= 5 for width in rows.values()), rows


def test_exact_format_direct_save_button_is_gone_at_runtime():
    view = guard.ExactFormatEditorView(scope="channel", target_id=123)
    custom_ids = {getattr(child, "custom_id", "") for child in view.children}

    assert "dank_design:exact_save" not in custom_ids
    assert "dank_design:exact_save_preview" in custom_ids
