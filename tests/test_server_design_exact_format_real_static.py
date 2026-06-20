from pathlib import Path


SOURCE = Path("stoney_verify/startup_guards/server_design_studio_command_guard.py").read_text()


def test_only_one_separator_example_text_function_exists():
    assert SOURCE.count("def _separator_example_text(") == 1


def test_separator_example_text_accepts_lock():
    assert "def _separator_example_text(sep_id: str, lock: Mapping[str, Any])" in SOURCE
    assert "_separator_example_text(sep, lock)" in SOURCE
    assert "def _separator_example_text(sep_id: str) -> str:" not in SOURCE


def test_only_one_exact_format_examples_button_exists():
    assert SOURCE.count('custom_id="dank_design:exact_layout_examples"') == 1
    assert 'custom_id="dank_design:exact_separator_examples"' not in SOURCE


def test_exact_format_save_preview_is_preview_first():
    assert "async def _save_exact_and_preview" in SOURCE
    assert "await interaction.response.defer(ephemeral=True, thinking=True)" in SOURCE
    assert "DesignPreviewView" in SOURCE
    assert "Apply Reviewed Changes" in SOURCE


def test_exact_format_copy_has_no_old_apply_wording():
    assert "Apply These Changes" not in SOURCE
    assert "Save Lock, then Preview/Fix" not in SOURCE
