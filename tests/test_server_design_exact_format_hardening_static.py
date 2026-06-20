from pathlib import Path


SOURCE = Path("stoney_verify/startup_guards/server_design_studio_command_guard.py").read_text()


def test_exact_format_uses_unique_separator_helper():
    assert "def _exact_separator_example_text(sep_id: str, lock: Mapping[str, Any])" in SOURCE
    assert "_exact_separator_example_text(sep, lock)" in SOURCE
    assert "_exact_separator_example_text(spec.id, lock)" in SOURCE


def test_old_duplicate_separator_gallery_is_removed():
    assert "SEPARATOR_GALLERY_PAGE_SIZE = 8" not in SOURCE
    assert 'custom_id="dank_design:exact_separator_examples"' not in SOURCE
    assert "def _separator_example_text(sep_id: str) -> str:" not in SOURCE


def test_exact_format_open_has_real_error_handler():
    assert "async def _open_exact_format_editor" in SOURCE
    assert "Exact Format could not open" in SOURCE
    assert "interaction.response.is_done()" in SOURCE
    assert "ExactFormatEditorViewFactory" in SOURCE


def test_exact_format_preview_language_is_current():
    assert "Apply These Changes" not in SOURCE
    assert "Apply Reviewed Changes" in SOURCE
    assert "Save Lock, then Preview/Fix" not in SOURCE
