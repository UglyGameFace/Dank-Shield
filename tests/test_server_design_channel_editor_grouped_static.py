from pathlib import Path


SOURCE = Path("stoney_verify/startup_guards/server_design_studio_command_guard.py").read_text()


def _block_for(name: str) -> str:
    start = SOURCE.find(name)
    assert start != -1, f"{name} not found"
    end = len(SOURCE)
    for marker in ("\nclass ", "\ndef ", "\nasync def "):
        pos = SOURCE.find(marker, start + 1)
        if pos != -1:
            end = min(end, pos)
    return SOURCE[start:end]


def test_category_channels_returns_children_only():
    block = _block_for("def _category_channels")
    assert "[category] +" not in block
    assert "getattr(category, \"channels\", [])" in block


def test_channel_editor_is_grouped_by_category():
    assert "def _channel_editor_groups" in SOURCE
    block = _block_for("def _channel_editor_embed")
    assert "This page shows one category and the channels inside it." in block
    assert "Category on this page" in block
    assert "Pick one channel or category below." not in block


def test_channel_picker_uses_grouped_pages_not_flat_all_items():
    block = _block_for("class ChannelEditorPickerView")
    assert "_channel_editor_groups(guild)" in block
    assert "_all_editor_channels(guild)" not in block
    assert "EditCategoryFromChannelEditorButton" in block


def test_channel_editor_can_jump_to_category_editor_action():
    assert "class EditCategoryFromChannelEditorButton" in SOURCE
    assert "_category_action_embed(category)" in SOURCE
    assert "CategoryEditorActionView" in SOURCE
