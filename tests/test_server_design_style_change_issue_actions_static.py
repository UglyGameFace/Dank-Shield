from pathlib import Path


SOURCE = Path("stoney_verify/startup_guards/server_design_studio_command_guard.py").read_text()


def test_style_change_preview_has_issue_actions():
    assert "class StyleChangePreviewView" in SOURCE
    assert "class StyleChangeFixMissingEmojiButton" in SOURCE
    assert "class StyleChangeApplySafeOnlyButton" in SOURCE
    assert 'custom_id="dank_design:style_change_fix_missing_emojis"' in SOURCE
    assert 'custom_id="dank_design:style_change_skip_issues"' in SOURCE


def test_style_change_missing_emoji_modal_is_manual_not_blind():
    assert "class StyleChangeFixMissingEmojiModal" in SOURCE
    assert "Emoji for {base}" in SOURCE
    assert "_style_change_after_with_manual_emoji" in SOURCE
    assert "manual_emoji" in SOURCE


def test_style_change_preview_explains_real_fixes():
    assert "How to fix" in SOURCE
    assert "missing emoji" in SOURCE
    assert "fix bot access/role order" in SOURCE
    assert "rename one conflicting channel" in SOURCE


def test_style_change_can_skip_issues_without_renaming_failed_rows():
    assert "Skipped by user from Style Change issues review." in SOURCE
    assert "Needs-review rows were left untouched" in SOURCE
    assert "Style Change Preview · Safe Changes Only" in SOURCE
