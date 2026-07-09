from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PUBLIC = (ROOT / "stoney_verify/commands_ext/public_design_studio.py").read_text(encoding="utf-8")
APPLIER = (ROOT / "tools/apply_p0_int_design_style_change_native_guard.py").read_text(encoding="utf-8")


def test_missing_icons_no_longer_dead_ends_when_more_than_five() -> None:
    assert "Too many missing-emoji rows for one modal" not in PUBLIC
    assert "design.style_change.fix_missing.too_many" not in PUBLIC
    assert "fix the rest from Channel Editor" not in PUBLIC


def test_missing_icons_modal_uses_first_batch_of_five() -> None:
    assert "batch = missing[:5]" in PUBLIC
    assert "StyleChangeFixMissingEmojiModal(items=batch" in PUBLIC
    assert "Discord modals support at most 5 text inputs" in PUBLIC


def test_missing_icons_help_explains_batching() -> None:
    assert "opens them in batches of 5" in PUBLIC


def test_old_style_change_applier_will_not_reintroduce_dead_end() -> None:
    assert "Too many missing-emoji rows for one modal" not in APPLIER
    assert "fix the rest from Channel Editor" not in APPLIER
    assert "batch = missing[:5]" in APPLIER


if __name__ == "__main__":
    for test in (
        test_missing_icons_no_longer_dead_ends_when_more_than_five,
        test_missing_icons_modal_uses_first_batch_of_five,
        test_missing_icons_help_explains_batching,
        test_old_style_change_applier_will_not_reintroduce_dead_end,
    ):
        test()
        print(f"PASS {test.__name__}")
