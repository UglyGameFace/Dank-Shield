from pathlib import Path


COMMAND = Path("stoney_verify/startup_guards/server_design_studio_command_guard.py").read_text()
SERVICE = Path("stoney_verify/services/server_design_studio.py").read_text()


def test_home_uses_plain_intent_labels():
    required = [
        "Fix Mismatched Names",
        "Change One Style",
        "Preview Saved Design",
        "Category Editor",
        "Channel Editor",
        "Help",
        "More Tools",
    ]

    for phrase in required:
        assert phrase in COMMAND, f"Missing plain workflow phrase: {phrase}"


def test_change_one_style_explains_selection_is_not_apply():
    assert "Choosing a separator only updates this draft" in COMMAND
    assert "Preview This Change" in COMMAND
    assert "Apply Reviewed Changes" in COMMAND
    assert "Example result" in COMMAND


def test_separator_dropdown_shows_real_template_result():
    assert "def _style_change_separator_preview_text" in COMMAND
    assert "studio.separator_preview(separator_id" in COMMAND
    assert "Result: {_style_change_separator_preview_text(sep_id)}" in COMMAND
    assert 'f"Example: 🎮{value}gaming-news"' not in COMMAND


def test_bracket_separator_names_are_plain_language():
    assert '"Fullwidth Bar"' not in SERVICE
    assert '"Full-width Bar"' in SERVICE

    assert '"Lenticular Brackets"' not in SERVICE
    assert '"Square Brackets"' in SERVICE


def test_rename_vs_apply_language_is_clear():
    assert "Rename applies immediately" in COMMAND
    assert "No Apply button appears after Rename" in COMMAND
    assert "Applied immediately. No Apply button is needed after Rename." in COMMAND


def test_problem_previews_have_fix_paths():
    required = [
        "Choose Missing Icons",
        "Apply Safe Ones Only",
        "How to fix",
        "Needs-review rows were left untouched",
    ]

    for phrase in required:
        assert phrase in COMMAND, f"Missing problem-fix path copy: {phrase}"


def test_no_vague_old_main_labels():
    banned = [
        'label="Style Change"',
        'label="Review Repairs"',
        'label="Preview Server"',
        'label="Guide"',
        'label="Advanced"',
    ]

    for phrase in banned:
        assert phrase not in COMMAND, f"Old vague label still exists: {phrase}"
