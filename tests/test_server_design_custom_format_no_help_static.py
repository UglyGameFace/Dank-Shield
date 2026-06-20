from pathlib import Path


SOURCE = Path("stoney_verify/startup_guards/server_design_studio_command_guard.py").read_text()


def test_custom_format_not_exact_format_copy():
    assert "🎛️ Custom Format" in SOURCE
    assert "Exact Format Editor" not in SOURCE
    assert "Press Save Rule & Preview" in SOURCE
    assert "Apply Reviewed Changes" in SOURCE


def test_font_dropdown_shows_examples_not_jargon():
    assert "def _exact_font_example_text" in SOURCE
    assert "Example:" in SOURCE
    assert "Use {font.replace('_', ' ')} font with fallback glyphs" not in SOURCE
    assert "placeholder=\"1) Choose text style\"" in SOURCE


def test_separator_dropdown_has_real_result_examples_not_raw_ids():
    assert "def _exact_separator_preview_text" in SOURCE
    assert "def _exact_separator_option_label" in SOURCE
    assert "Result: {_exact_separator_preview_text(separator_id)}" in SOURCE
    assert 'label=f"{spec.label} ({sep_id})"' not in SOURCE
    assert "Choose channel separator" in SOURCE


def test_frame_and_strength_dropdowns_explain_results():
    assert "def _exact_frame_option_description" in SOURCE
    assert "Result: {studio.category_frame_preview" in SOURCE
    assert "def _exact_strength_description" in SOURCE
    assert "Recommended balance for most servers" in SOURCE
    assert "Pick how much styling to use" in SOURCE
