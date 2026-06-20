from pathlib import Path


SOURCE = Path("stoney_verify/startup_guards/server_design_studio_command_guard.py").read_text()


def test_vague_main_labels_are_replaced():
    banned = [
        'label="Style Change"',
        'label="Preview Server"',
        'label="Review Repairs"',
        'label="Advanced"',
        'label="Guide"',
        'label="Exact Format"',
        'label="Protection Manager"',
        'label="Design Doctor"',
        'label="Format Locks / Layouts"',
        'label="Manage Saved Locks"',
        'label="Skip Issues"',
        'label="Fix Missing Emojis"',
    ]

    for text in banned:
        assert text not in SOURCE, f"Vague button label still exists: {text}"


def test_clear_action_object_labels_exist():
    required = [
        'label="Change One Style"',
        'label="Preview Saved Design"',
        'label="Fix Mismatched Names"',
        'label="More Tools"',
        'label="Help"',
        'label="Custom Format"',
        'label="Rename Protection"',
        'label="Check Design Problems"',
        'label="Saved Layout Rules"',
        'label="Manage Saved Rules"',
        'label="Apply Safe Ones Only"',
        'label="Choose Missing Icons"',
        'label="Preview Fixes"',
        'label="Save Category Layout"',
        'label="Save Channel Layout"',
    ]

    for text in required:
        assert text in SOURCE, f"Clear button label missing: {text}"


def test_home_copy_explains_intent_not_feature_names():
    assert "copies the live server style and fixes only names that do not match" in SOURCE
    assert "add/change one thing, like a separator, while keeping everything else" in SOURCE
    assert "shows what saved rules would rename before anything changes" in SOURCE


def test_help_copy_uses_plain_workflow_language():
    assert "Change One Style" in SOURCE or "Change one style" in SOURCE
    assert "Fix Mismatched Names" in SOURCE or "Fix mismatches" in SOURCE
    assert "Preview Saved Design" in SOURCE or "Preview saved rules" in SOURCE
    assert "Custom Format" in SOURCE or "Custom one item" in SOURCE
