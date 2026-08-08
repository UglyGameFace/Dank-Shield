from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVICE = (ROOT / "stoney_verify/welcome_event_services.py").read_text(encoding="utf-8")


def test_compatibility_join_leave_preview_uses_canonical_template_renderer() -> None:
    assert "from .lifecycle_template_renderer import render_lifecycle_template" in SERVICE
    assert "return render_lifecycle_template(" in SERVICE
    assert "preview=True" in SERVICE


def test_old_exact_placeholder_replace_loop_cannot_return() -> None:
    assert 'out = out.replace("{" + key + "}", value)' not in SERVICE
    assert "pairs.update(_preview_invite_values())" not in SERVICE
    assert "def _preview_invite_values(" not in SERVICE
    assert "def _age_text(" not in SERVICE
    assert "def _discord_time(" not in SERVICE
