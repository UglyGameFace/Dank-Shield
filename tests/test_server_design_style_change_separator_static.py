from pathlib import Path


SOURCE = Path("stoney_verify/startup_guards/server_design_studio_command_guard.py").read_text()


def _block(name: str) -> str:
    start = SOURCE.find(name)
    assert start != -1, f"{name} not found"

    end = len(SOURCE)
    for marker in ("\nclass ", "\ndef ", "\nasync def "):
        pos = SOURCE.find(marker, start + 1)
        if pos != -1:
            end = min(end, pos)

    return SOURCE[start:end]


def test_home_has_style_change_workflow_button():
    block = _block("class DesignHomeView")
    assert 'label="Change One Style"' in block
    assert 'custom_id="dank_design:style_change"' in block
    assert "StyleChangeView" in block


def test_style_change_is_separator_only_and_preview_first():
    assert "class StyleChangeView" in SOURCE
    assert "class StyleChangeSeparatorSelect" in SOURCE
    assert "def _build_channel_separator_style_change_plan" in SOURCE
    assert 'custom_id="dank_design:style_change_preview_separator"' in SOURCE
    assert "DesignPreviewView(can_apply=not has_blockers and has_changes)" in SOURCE


def test_style_change_copy_says_it_preserves_other_systems():
    assert "Everything else stays as-is" in SOURCE
    assert "permissions, tickets, and verification" in SOURCE
    assert "Channel separator only" in SOURCE


def test_style_change_apply_has_custom_completion_title():
    assert "✅ Change One Style Applied" in SOURCE
    assert "Changed separator on **{changed}** channel(s)" in SOURCE
