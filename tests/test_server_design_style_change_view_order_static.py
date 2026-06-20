from pathlib import Path


SOURCE = Path("stoney_verify/startup_guards/server_design_studio_command_guard.py").read_text()


def _block(start_marker: str, end_marker: str) -> str:
    start = SOURCE.find(start_marker)
    assert start != -1, f"{start_marker} not found"
    end = SOURCE.find(end_marker, start)
    assert end != -1, f"{end_marker} not found after {start_marker}"
    return SOURCE[start:end]


def test_style_change_preview_view_is_defined_after_design_preview_view():
    design_pos = SOURCE.find("class DesignPreviewView")
    style_pos = SOURCE.find("class StyleChangePreviewView")

    assert design_pos != -1
    assert style_pos != -1
    assert design_pos < style_pos


def test_generic_scope_preview_uses_normal_preview_view():
    block = _block("async def _preview_scope(", "class DesignCategoryEditorButton")
    assert "StyleChangePreviewView" not in block
    assert "DesignPreviewView(can_apply=not has_blockers and has_changes)" in block


def test_style_change_preview_uses_issue_resolution_preview_view():
    block = _block("async def preview_separator_change", "@discord.ui.button(label=\"Back to Design Studio\"")
    assert "StyleChangePreviewView(can_apply=not has_blockers and has_changes, has_blockers=has_blockers)" in block
