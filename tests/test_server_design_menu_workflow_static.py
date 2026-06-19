from pathlib import Path


SOURCE = Path("stoney_verify/startup_guards/server_design_studio_command_guard.py").read_text()


def test_category_menu_is_guided_and_has_rename():
    assert "🗂️ Category Design" in SOURCE
    assert "Preview Repairs" in SOURCE
    assert "DirectRenameModal" in SOURCE
    assert "dank_design:category_rename" in SOURCE
    assert "Save Category Rule" in SOURCE
    assert "Edit Channels Inside" in SOURCE


def test_channel_menu_is_guided_and_has_rename():
    assert "#️⃣ Channel Design" in SOURCE
    assert "dank_design:channel_rename" in SOURCE
    assert "Save Channel Rule" in SOURCE
    assert "Preview Repairs" in SOURCE
    assert "Preview / Fix This Only" not in SOURCE
    assert "Preview / Fix This Category" not in SOURCE


def test_plain_language_replaces_draft_format_copy():
    assert "Use the current draft format on this category" not in SOURCE
    assert "Lock Current Format Here" not in SOURCE
    assert "Lock Current Format to This" not in SOURCE
    assert "current draft format" not in SOURCE


def test_scoped_preview_uses_live_majority_not_saved_draft():
    assert '"__use_live_majority_layout"] = True' in SOURCE
    assert 'mode in {"category_editor", "channel_editor"}' in SOURCE


def test_picker_copy_explains_workflow():
    assert "Step 1: pick a category. Step 2: preview, rename, or edit channels inside." in SOURCE
    assert "Step 1: pick an item. Step 2: preview, rename, or edit its rule." in SOURCE
