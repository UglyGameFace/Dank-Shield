from __future__ import annotations

import re
import sys
import types
from pathlib import Path

fake_supabase = types.ModuleType("supabase")
fake_supabase.Client = object
fake_supabase.create_client = lambda *a, **k: None
sys.modules.setdefault("supabase", fake_supabase)

from stoney_verify.startup_guards import server_design_studio_command_guard as guard


SOURCE = Path("stoney_verify/startup_guards/server_design_studio_command_guard.py").read_text()


def _width(item) -> int:
    return 5 if item.__class__.__name__.endswith("Select") else 1


def test_exact_format_source_has_no_direct_save_button():
    start = SOURCE.find("class ExactFormatEditorView")
    end = SOURCE.find("\ndef ExactFormatEditorViewFactory", start)

    assert start != -1
    assert end != -1

    block = SOURCE[start:end]

    assert 'custom_id="dank_design:exact_save"' not in block
    assert 'custom_id="dank_design:exact_save_preview"' in block

    row4_buttons = re.findall(r"@discord\.ui\.button\([^\n]*row=4[^\n]*\)", block)
    assert len(row4_buttons) == 5, row4_buttons


def test_exact_format_raw_view_opens_with_five_row_four_buttons():
    view = guard.ExactFormatEditorView(scope="channel", target_id=123)

    rows = {}
    custom_ids = set()

    for child in view.children:
        row = int(getattr(child, "row", 0) or 0)
        rows[row] = rows.get(row, 0) + _width(child)
        custom_ids.add(getattr(child, "custom_id", ""))

    assert rows.get(4) == 5, rows
    assert "dank_design:exact_save" not in custom_ids
    assert "dank_design:exact_save_preview" in custom_ids


def test_exact_format_factory_opens_with_valid_discord_rows():
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

    assert rows == {0: 5, 1: 5, 2: 5, 3: 5, 4: 5}, rows
