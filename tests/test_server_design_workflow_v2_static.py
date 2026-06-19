from pathlib import Path


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


def test_home_is_product_workflow_not_tool_pile():
    assert "🎨 Dank Design Studio" in SOURCE
    assert "Safe workflow" in SOURCE
    assert "Recommended" in SOURCE
    assert "Edit one thing" in SOURCE
    assert "Preview & Apply" not in SOURCE
    assert "Fix Mismatches" not in SOURCE


def test_home_buttons_are_clear_and_ordered():
    block = _block_for("class DesignHomeView")
    assert 'label="Review Repairs"' in block
    assert 'label="Preview Server"' in block
    assert 'label="Category Editor"' in block
    assert 'label="Channel Editor"' in block
    assert 'label="Guide"' in block
    assert 'label="Advanced"' in block


def test_review_repairs_uses_live_majority():
    block = _block_for("class DesignHomeView")
    assert '"__use_live_majority_layout"] = True' in block
    assert 'build_design_plan(guild, repair_options)' in block
    assert '"options": dict(repair_options)' in block


def test_home_does_not_apply_without_preview():
    block = _block_for("class DesignHomeView")
    assert "DesignPreviewView" in block
    assert "Apply These Changes" not in block
