from pathlib import Path
import re


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


def test_exact_format_defaults_from_live_majority():
    assert "def _live_design_records_for_exact_format" in SOURCE
    assert "def _live_majority_exact_lock" in SOURCE
    assert "majority.infer_live_majority_layout(studio, records)" in SOURCE
    assert "majority.apply_majority_to_options(studio, options, analysis, respect_locks=False)" in SOURCE


def test_initial_lock_uses_correct_priority_order():
    block = _block("def _initial_editor_lock")
    assert "majority_lock = _live_majority_exact_lock" in block
    assert '"__source"] = "saved_exact_rule"' in block
    assert "elif majority_lock:" in block
    assert '"__source"] = "saved_design_rule"' in block


def test_exact_format_shows_conflict_status():
    assert "def _exact_format_conflicts" in SOURCE
    assert "Conflict check" in SOURCE
    assert "Detected server style:" in SOURCE
    assert "Use **Server Style** to reset this draft" in SOURCE


def test_exact_format_has_server_style_button_not_toggle():
    block = _block("class ExactFormatEditorView")
    assert 'custom_id="dank_design:exact_use_majority"' in block
    assert 'label="Server Style"' in block
    assert "Toggle Smart/Exact" not in block
    assert "exact_toggle_mode" not in block


def test_exact_format_row_four_stays_at_five_buttons():
    start = SOURCE.find("class ExactFormatEditorView")
    end = SOURCE.find("\ndef ExactFormatEditorViewFactory", start)
    assert start != -1 and end != -1
    block = SOURCE[start:end]
    row4_buttons = re.findall(r"@discord\.ui\.button\([^\n]*row=4[^\n]*\)", block)
    assert len(row4_buttons) == 5, row4_buttons
    assert 'custom_id="dank_design:exact_save"' not in block
    assert 'custom_id="dank_design:exact_save_preview"' in block


def test_exact_lock_persistence_does_not_save_transient_majority_metadata():
    assert "def _persistable_exact_lock" in SOURCE
    assert "persist_lock = _persistable_exact_lock(lock)" in SOURCE
    assert 'not str(k).startswith("__")' in SOURCE
