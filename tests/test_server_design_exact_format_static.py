from pathlib import Path


SOURCE = Path("stoney_verify/startup_guards/server_design_studio_command_guard.py").read_text()


def test_exact_format_embed_passes_lock_to_separator_example():
    assert '_separator_example_text(sep, lock)' in SOURCE
    assert '_separator_example_text(sep)`' not in SOURCE


def test_exact_format_layout_examples_uses_current_gallery_signature():
    assert 'embed=_separator_gallery_embed(guild, scope=self.scope, target_id=self.target_id, lock=lock, page=0)' in SOURCE
    assert 'view=SeparatorExamplesView(guild, scope=self.scope, target_id=self.target_id, lock=lock, page=0)' in SOURCE
    assert 'embed=_separator_gallery_embed(page=0, current=current)' not in SOURCE
    assert 'view=SeparatorGalleryView(scope=self.scope' not in SOURCE


def test_save_exact_preview_has_no_undefined_mode_reference():
    save_start = SOURCE.index("async def _save_exact_and_preview")
    save_end = SOURCE.index("class ExactFormatEditorView", save_start)
    save_block = SOURCE[save_start:save_end]
    assert "if mode in" not in save_block
