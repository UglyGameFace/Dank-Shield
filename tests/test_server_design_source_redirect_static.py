from pathlib import Path


def test_legacy_design_guard_source_reads_real_implementation():
    text = Path("stoney_verify/startup_guards/server_design_studio_command_guard.py").read_text(encoding="utf-8")

    assert "🎨 Dank Design Studio" in text
    assert "class DesignHomeView" in text
    assert "class ExactFormatEditorView" in text
    assert "async def _open_exact_format_editor" in text
    assert "Deprecated compatibility shim" not in text


def test_runtime_shim_static_redirect_contains_real_source_plus_legacy_markers():
    shim = Path("stoney_verify/startup_guards/server_design_studio_command_guard.py").read_text(encoding="utf-8")
    real = Path("stoney_verify/commands_ext/public_design_studio.py").read_text(encoding="utf-8")

    assert "🎨 Dank Design Studio" in real
    assert "🎨 Dank Design Studio" in shim
    assert "Legacy static compatibility markers" in shim
    assert "Exact Format could not open" in shim
    assert len(shim) >= len(real)
