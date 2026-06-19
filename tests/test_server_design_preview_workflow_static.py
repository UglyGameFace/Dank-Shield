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


def test_preview_is_decision_screen():
    block = _block_for("def _preview_embed")
    assert "Nothing changed yet. This is a review screen." in block
    assert "Decision summary" in block
    assert "Before → After" in block
    assert "Skipped on purpose" in block
    assert "Needs attention before Apply" in block


def test_preview_uses_plain_language():
    block = _block_for("def _preview_embed")
    assert "Mobile/accessibility check" in block
    assert "Plain-English notes" in block
    assert "Will Change" not in block
    assert 'name="Plan"' not in block


def test_apply_button_is_reviewed_action_not_danger_copy():
    block = _block_for("class DesignPreviewView")
    assert 'label="Apply Reviewed Changes"' in block
    assert "discord.ButtonStyle.success" in block
    assert 'label="Apply These Changes"' not in block
