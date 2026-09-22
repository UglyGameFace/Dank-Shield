from __future__ import annotations

from pathlib import Path

from stoney_verify.commands_ext import public_protection_center as center


ROOT = Path(__file__).resolve().parents[1]
STARTUP_INIT = (ROOT / "stoney_verify/startup_guards/__init__.py").read_text(encoding="utf-8")
COMMANDS_EXT = (ROOT / "stoney_verify/commands_ext/__init__.py").read_text(encoding="utf-8")


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


def test_retired_protection_patch_metadata_stays_absent() -> None:
    for module_name in (
        "protection_center_command_guard",
        "protection_import_button_patch",
        "protection_pack_manual_import_guard",
    ):
        assert module_name not in STARTUP_INIT


def test_native_command_registry_already_owns_protection_alias_shape() -> None:
    assert '"protection",' in COMMANDS_EXT
    assert '"automod",' in COMMANDS_EXT
    assert '"spam",' in COMMANDS_EXT
    assert "def _runtime_command_prune_disabled()" in COMMANDS_EXT
