from pathlib import Path


COMMAND = Path("stoney_verify/startup_guards/server_design_studio_command_guard.py").read_text()
SERVICE = Path("stoney_verify/services/server_design_studio.py").read_text()


def test_style_change_separator_dropdown_uses_template_preview():
    assert "def _style_change_separator_preview_text" in COMMAND
    assert "studio.separator_preview(separator_id" in COMMAND
    assert "Result: {_style_change_separator_preview_text(sep_id)}" in COMMAND
    assert "f\"Example: 🎮{value}gaming-news\"" not in COMMAND


def test_style_change_bracket_options_show_actual_brackets():
    assert "bracket_corner" in COMMAND
    assert "bracket_lenticular" in COMMAND
    assert "_style_change_separator_option_label(sep_id)" in COMMAND


def test_style_change_screen_says_selection_is_not_apply():
    assert "Choosing a separator only updates this draft" in COMMAND
    assert "Preview This Change" in COMMAND
    assert "Apply Reviewed Changes" in COMMAND


def test_user_facing_separator_labels_are_plain_language():
    assert '"Fullwidth Bar"' not in SERVICE
    assert '"Full-width Bar"' in SERVICE
    assert '"Lenticular Brackets"' not in SERVICE
    assert '"Square Brackets"' in SERVICE
