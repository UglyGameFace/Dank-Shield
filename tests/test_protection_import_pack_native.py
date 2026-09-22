from __future__ import annotations

from pathlib import Path

from stoney_verify.commands_ext import public_protection_center as center


ROOT = Path(__file__).resolve().parents[1]


def test_native_import_merge_normalizes_dedupes_and_preserves_existing() -> None:
    merged, imported, skipped = center._merge_imported_filter_terms(
        "existing term,Second",
        " NEW   TERM \nsecond\n\u200bHidden\nX\nnew term",
    )

    assert merged == ["existing term", "second", "new term", "hidden"]
    assert imported == 2
    assert skipped == 3


def test_native_import_merge_respects_character_budget(monkeypatch) -> None:
    monkeypatch.setattr(center, "MAX_FILTER_PACK_CHARS", 12)

    merged, imported, skipped = center._merge_imported_filter_terms(
        "alpha",
        "beta\ngamma",
    )

    assert merged == ["alpha", "beta"]
    assert imported == 1
    assert skipped == 0


def test_native_protection_view_contains_exactly_one_import_pack_button() -> None:
    view = center.ProtectionCenterView(author_id=123, cfg={}, spam={})
    buttons = [
        child
        for child in view.children
        if getattr(child, "custom_id", None) == "dank_protection:import_pack"
    ]

    assert len(buttons) == 1
    assert str(getattr(buttons[0], "label", "")) == "Import Pack"


def test_retired_protection_patch_files_stay_absent() -> None:
    for relative in (
        "stoney_verify/startup_guards/protection_center_command_guard.py",
        "stoney_verify/startup_guards/protection_import_button_patch.py",
        "stoney_verify/startup_guards/protection_pack_manual_import_guard.py",
    ):
        assert not (ROOT / relative).exists()
