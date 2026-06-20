from pathlib import Path
import re


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


def test_exact_format_has_no_more_than_five_row_four_buttons():
    block = _block_for("class ExactFormatEditorView")
    row4_buttons = re.findall(r"@discord\.ui\.button\([^\n]*row=4\)", block)
    assert len(row4_buttons) <= 5, row4_buttons


def test_exact_format_removed_direct_save_button():
    block = _block_for("class ExactFormatEditorView")
    assert 'custom_id="dank_design:exact_save"' not in block
    assert 'label="Save & Preview"' in block
    assert "async def save_and_preview" in block


def test_exact_format_save_preview_is_the_save_path():
    block = _block_for("async def _save_exact_and_preview")
    assert "await _save_exact_lock" in block
    assert "DesignPreviewView" in block


def test_exact_format_toggle_rebuilds_from_current_state():
    block = _block_for("class ExactFormatEditorView")
    assert "current[\"exact_match\"] = not bool(current.get(\"exact_match\", False))" in block
    assert "ExactFormatEditorViewFactory(guild, self.scope, self.target_id, current)" in block
    assert "ExactFormatEditorViewFactory(guild, self.scope, self.target_id, lock)" not in block
