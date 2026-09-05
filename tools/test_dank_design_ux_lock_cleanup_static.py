from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LEGACY = (ROOT / "stoney_verify/commands_ext/public_design_studio.py").read_text(encoding="utf-8")
V2 = (ROOT / "stoney_verify/commands_ext/public_design_studio_v2.py").read_text(encoding="utf-8")
SAFE_TEST = (ROOT / "tools/test_dank_design_safe_repair_cleanup_static.py").read_text(encoding="utf-8")


def test_home_uses_five_plain_language_jobs() -> None:
    for label in (
        "Design Entire Server",
        "Edit One Category / Channel",
        "Fix Inconsistent Names",
        "Saved Rules & Protection",
        "Undo Last Apply",
    ):
        assert f'label="{label}"' in V2
    assert "Pick **one job** below" in V2
    assert "Only one action is immediate" in V2
    assert "Change One Style" not in V2
    assert "Fix Mismatched Names" not in V2


def test_rules_and_protection_copy_explains_non_rename_behavior() -> None:
    assert "Saved Rules & Protection" in V2
    assert "does **not** rename a Discord item by itself" in V2
    assert "Layout Rules" in V2
    assert "Unlock / Clean" in V2
    assert "Protection" in V2
    assert "Narrower rules always win" in V2
    assert "Protection is separate" in V2


def test_exact_item_editor_makes_immediate_rename_exception_explicit() -> None:
    assert "**Rename** is the only immediate name change" in V2
    assert "**Preview Fixes** and **Custom Format** show a preview" in V2
    assert "Rename applies immediately. No Apply button appears after Rename." in LEGACY
    assert "Rename is instant • Preview Fixes and Custom Format use Apply later" in LEGACY


def test_repair_flow_is_scan_then_preview_then_apply() -> None:
    assert 'label="Scan Saved Design"' in V2
    assert 'label="Build Smart Repair Preview"' in V2
    assert "Read-only scan. Nothing was renamed." in V2
    assert "Smart Repair analyzed each category independently" in V2
    assert "Saved exact/channel/category/global rules still win" in V2
    assert "Apply is enabled only when the plan is fully reviewable and confidence is high" in V2


def test_safe_repair_audit_tracks_current_authority_contract() -> None:
    assert "Narrow saved rules always win" in SAFE_TEST
    assert "keeps saved narrow rules authoritative" in SAFE_TEST
    assert "respect_saved_rules=True" in SAFE_TEST
    assert "reviews saved rules first" not in SAFE_TEST


if __name__ == "__main__":
    for test in (
        test_home_uses_five_plain_language_jobs,
        test_rules_and_protection_copy_explains_non_rename_behavior,
        test_exact_item_editor_makes_immediate_rename_exception_explicit,
        test_repair_flow_is_scan_then_preview_then_apply,
        test_safe_repair_audit_tracks_current_authority_contract,
    ):
        test()
        print(f"PASS {test.__name__}")
