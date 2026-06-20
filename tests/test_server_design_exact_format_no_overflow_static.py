from pathlib import Path


SOURCE = Path("stoney_verify/startup_guards/server_design_studio_command_guard.py").read_text()


def test_exact_format_direct_save_button_cannot_return():
    assert 'custom_id="dank_design:exact_save"' not in SOURCE
    assert 'custom_id="dank_design:exact_save_preview"' in SOURCE


def test_exact_format_error_handler_exists():
    assert "Exact Format could not open" in SOURCE


def test_exact_format_factory_exists():
    assert "def ExactFormatEditorViewFactory" in SOURCE
