from pathlib import Path


SOURCE = Path("stoney_verify/startup_guards/server_design_studio_command_guard.py").read_text()


def _block(name: str) -> str:
    start = SOURCE.find(name)
    assert start != -1, f"{name} not found"
    end = len(SOURCE)
    for marker in ("\nclass ", "\ndef ", "\nasync def "):
        pos = SOURCE.find(marker, start + 1)
        if pos != -1:
            end = min(end, pos)
    return SOURCE[start:end]


def test_exact_format_has_scope_helpers():
    assert "def _exact_format_applies_category_frame" in SOURCE
    assert "def _exact_current_layout_example" in SOURCE
    assert "def _exact_selected_format_lines" in SOURCE


def test_category_preview_includes_category_before_children():
    block = _block("def _exact_format_sample_lines")
    assert "preview_items.append(category)" in block
    assert "_category_channels(guild, int(target_id))[:4]" in block
    assert 'label = "Category" if design_kind == "category" else "Channel"' in block


def test_channel_scope_does_not_show_category_frame_select():
    block = _block("def ExactFormatEditorViewFactory")
    assert "if _exact_format_applies_category_frame(scope):" in block
    assert "ExactFrameSelect" in block
    assert "strength.row = 2" in block


def test_category_frame_conflict_is_scope_aware():
    block = _block("def _exact_format_conflicts")
    assert "_exact_format_applies_category_frame(scope)" in block


def test_exact_format_embed_uses_scope_aware_current_example():
    block = _block("def _exact_format_embed")
    assert "_exact_current_layout_example(guild, scope=scope, target_id=target_id, lock=lock)" in block
    assert "_exact_selected_format_lines(scope, lock)" in block
