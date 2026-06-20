from __future__ import annotations

import ast
import re
from pathlib import Path


SOURCE_PATH = Path("stoney_verify/startup_guards/server_design_studio_command_guard.py")
SOURCE = SOURCE_PATH.read_text()


def _block(start_marker: str, end_marker: str | None = None) -> str:
    start = SOURCE.find(start_marker)
    assert start != -1, f"{start_marker!r} not found"

    if end_marker is not None:
        end = SOURCE.find(end_marker, start)
        assert end != -1, f"{end_marker!r} not found after {start_marker!r}"
        return SOURCE[start:end]

    candidates = []
    for marker in ("\nclass ", "\ndef ", "\nasync def "):
        pos = SOURCE.find(marker, start + 1)
        if pos != -1:
            candidates.append(pos)

    end = min(candidates) if candidates else len(SOURCE)
    return SOURCE[start:end]


def test_home_screen_explains_safe_scope_and_main_paths():
    assert "Design channel/category names without touching permissions" in SOURCE
    assert "tickets" in SOURCE
    assert "verification" in SOURCE

    assert "Fix Mismatched Names" in SOURCE
    assert "Change One Style" in SOURCE
    assert "Preview Saved Design" in SOURCE
    assert "Category Editor" in SOURCE
    assert "Channel Editor" in SOURCE

    assert "Fix Mismatched Names follows live style" in SOURCE
    assert "Preview Saved Design follows saved rules" in SOURCE


def test_rename_workflow_is_not_confused_with_apply_workflow():
    assert "Rename applies immediately. No Apply button appears after Rename." in SOURCE
    assert "Applied immediately. No Apply button is needed after Rename." in SOURCE
    assert "Discord result:" in SOURCE
    assert "Refresh" in SOURCE

    assert 'custom_id="dank_design:category_action_refresh"' in SOURCE
    assert 'custom_id="dank_design:channel_action_refresh"' in SOURCE


def test_preview_apply_workflow_is_clearly_separate_from_rename():
    assert "Apply Reviewed Changes" in SOURCE
    assert "Nothing is renamed until" in SOURCE or "preview first" in SOURCE.lower()
    assert "rollback snapshot" in SOURCE.lower()
    assert "can_apply=not has_blockers and has_changes" in SOURCE


def test_category_editor_has_complete_local_workflow():
    block = _block("class CategoryEditorActionView", "class ChannelEditorActionView")

    assert "Preview Fixes" in block
    assert "Rename" in block
    assert "Edit Channels Here" in block
    assert "Custom Format" in block
    assert "Save Category Layout" in block
    assert "Rename Protection" in block
    assert "Refresh" in block
    assert "Back" in block


def test_channel_editor_has_complete_local_workflow():
    block = _block("class ChannelEditorActionView", "class BackToDesignButton")

    assert "Preview Fixes" in block
    assert "Rename" in block
    assert "Custom Format" in block
    assert "Save Channel Layout" in block
    assert "Rename Protection" in block
    assert "Refresh" in block
    assert "Back" in block


def test_channel_editor_is_grouped_by_category_and_can_jump_to_category():
    assert "This page shows one category and the channels inside it." in SOURCE
    assert "Category on this page" in SOURCE
    assert "Edit This Category" in SOURCE
    assert "EditCategoryFromChannelEditorButton" in SOURCE


def test_review_repairs_uses_live_majority_from_editors_not_saved_rule_blindly():
    block = _block("async def _preview_scope", "class DesignCategoryEditorButton")

    assert '"__use_live_majority_layout"] = True' in block
    assert "mode in {\"category_editor\", \"channel_editor\"}" in block
    assert "build_design_plan(guild, repair_options)" in block


def test_style_change_has_fix_paths_not_dead_end_preview():
    assert "class StyleChangePreviewView" in SOURCE
    assert "Choose Missing Icons" in SOURCE
    assert "Apply Safe Ones Only" in SOURCE
    assert "How to fix" in SOURCE
    assert "Needs-review rows were left untouched" in SOURCE


def test_style_change_does_not_blindly_guess_missing_icons():
    assert "No leading emoji/icon found" in SOURCE or "No leading emoji found" in SOURCE
    assert "manual_emoji" in SOURCE
    assert "Emoji for {base}" in SOURCE
    assert "Pick a real emoji/icon" in SOURCE


def test_hash_keycap_is_not_allowed_as_channel_name_icon():
    assert "def _style_change_is_unsafe_channel_icon" in SOURCE
    assert "#️⃣ and square placeholder icons are not safe channel-name icons" in SOURCE
    assert "`#️⃣` and square placeholder icons can break into blocks" in SOURCE


def test_exact_format_is_preview_first_and_has_no_old_dead_copy():
    assert "Save & Preview" in SOURCE
    assert "Apply Reviewed Changes" in SOURCE
    assert "Layout Examples" in SOURCE

    banned = [
        "Apply These Changes",
        "current draft format",
        "Current draft format",
        "draft format",
        "Draft format",
        "Save Lock, then Preview/Fix",
    ]
    for phrase in banned:
        assert phrase not in SOURCE, f"Old confusing Custom Format copy still exists: {phrase}"


def test_no_visible_newline_artifacts_in_user_copy():
    exact_allowed = {"\\n", "\\\\n", "/n", "\n"}

    tree = ast.parse(SOURCE, filename=str(SOURCE_PATH))
    bad: list[str] = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
            continue

        text = node.value
        stripped = text.strip()

        if stripped in exact_allowed or len(stripped) <= 4:
            continue

        # Real copy bugs: visible slash-newline tokens. Do not flag words like emoji/name/font.
        if "\\\\n" in text or "\\n" in text or re.search(r"(?<![A-Za-z0-9_])/n(?![A-Za-z0-9_])", text):
            preview = text.replace("\n", "\\n")[:160]
            bad.append(f"{getattr(node, 'lineno', '?')}: {preview!r}")

    assert not bad, "Visible newline artifacts found:\n" + "\n".join(bad[:50])


def test_decorator_button_rows_do_not_overflow_discord_limits():
    # Discord allows max 5 component width per row. Decorator buttons are width 1.
    class_blocks = re.finditer(r"(?ms)^class\s+(\w+).*?(?=^class\s+\w+|\Z)", SOURCE)
    failures: list[str] = []

    for match in class_blocks:
        class_name = match.group(1)
        block = match.group(0)

        row_counts: dict[int, int] = {}
        for row_match in re.finditer(r"@discord\.ui\.button\([^\n]*row=(\d+)", block):
            row = int(row_match.group(1))
            row_counts[row] = row_counts.get(row, 0) + 1

        for row, count in row_counts.items():
            if count > 5:
                failures.append(f"{class_name} row {row} has {count} decorator buttons")

    assert not failures, "Discord button row overflow risk:\n" + "\n".join(failures)


def test_every_action_error_message_tells_user_what_to_fix():
    required_phrases = [
        "Manage Channels",
        "role must be high enough",
        "Discord rejected",
        "That item no longer exists",
        "That category no longer exists",
        "That channel no longer exists",
    ]

    for phrase in required_phrases:
        assert phrase in SOURCE


def test_protection_and_skips_are_explained_not_silent():
    assert "Rename Protection" in SOURCE
    assert "skipped" in SOURCE.lower()
    assert "protected" in SOURCE.lower()


def test_rollback_is_available_from_design_surface():
    assert "Rollback" in SOURCE
    assert "Rollback Preview" in SOURCE or "Rollback Last Apply" in SOURCE
    assert "rollback snapshot" in SOURCE.lower()
