from __future__ import annotations

import sys
import types

fake_supabase = types.ModuleType("supabase")
fake_supabase.Client = object
fake_supabase.create_client = lambda *a, **k: None
sys.modules.setdefault("supabase", fake_supabase)

import discord

from stoney_verify.startup_guards import server_design_studio_command_guard as guard


def _width(item) -> int:
    return 5 if item.__class__.__name__.endswith("Select") else 1


def _row_widths(view: discord.ui.View) -> dict[int, int]:
    rows: dict[int, int] = {}
    for child in view.children:
        row = int(getattr(child, "row", 0) or 0)
        rows[row] = rows.get(row, 0) + _width(child)
    return rows


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

    rows = _row_widths(view)

    assert all(0 <= row <= 4 for row in rows), rows
    assert all(width <= 5 for width in rows.values()), rows


def test_exact_format_direct_save_button_is_gone_at_runtime():
    view = guard.ExactFormatEditorView(scope="channel", target_id=123)
    custom_ids = {getattr(child, "custom_id", "") for child in view.children}

    assert "dank_design:exact_save" not in custom_ids
    assert "dank_design:exact_save_preview" in custom_ids


def test_safe_add_item_refuses_sixth_row_button_when_all_rows_full_without_crashing():
    view = discord.ui.View(timeout=30)

    # Simulate Exact Format: rows 0-3 are full-width select rows.
    for row in range(4):
        ok = guard._safe_add_item(
            view,
            discord.ui.Select(
                placeholder=f"Select {row}",
                options=[discord.SelectOption(label="One", value="one")],
                row=row,
            ),
            preferred_row=row,
        )
        assert ok is True

    # Row 4 can hold exactly five buttons.
    for index in range(5):
        ok = guard._safe_add_item(
            view,
            discord.ui.Button(label=f"B{index}", custom_id=f"test:{index}", row=4),
            preferred_row=4,
        )
        assert ok is True

    # Now there is literally nowhere legal to place this.
    sixth = guard._safe_add_item(
        view,
        discord.ui.Button(label="B5", custom_id="test:5", row=4),
        preferred_row=4,
    )

    rows = _row_widths(view)

    assert sixth is False
    assert rows == {0: 5, 1: 5, 2: 5, 3: 5, 4: 5}
    assert len(view.children) == 9


def test_component_budget_assertion_catches_manual_overflow_if_forced():
    view = discord.ui.View(timeout=30)

    for index in range(5):
        view.add_item(discord.ui.Button(label=f"B{index}", custom_id=f"manual:{index}", row=4))

    guard._assert_design_view_component_budget(view)
