from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RAIDGUARD = (ROOT / "stoney_verify/raidguard.py").read_text(encoding="utf-8")
AUDIT_GUARD = (ROOT / "stoney_verify/startup_guards/member_lifecycle_audit_context_guard.py").read_text(encoding="utf-8")


def test_suspicious_name_regex_is_not_substring_real_trap() -> None:
    assert "real|backup|alt|test|temp|burner" not in RAIDGUARD
    assert "real[\\W_]*(support|staff|admin|mod|discord)" in RAIDGUARD
    assert "free[\\W_]*nitro" in RAIDGUARD


def test_low_flagged_accounts_do_not_render_as_clear() -> None:
    assert "tier == \"CLEAR\"" in RAIDGUARD
    assert "tier = \"WATCHLIST\"" in RAIDGUARD
    assert "while also showing heuristic flags" in RAIDGUARD


def test_staff_join_audit_has_dm_safety_context_and_quick_mod() -> None:
    assert "DM spam limitation" in AUDIT_GUARD
    assert "Discord does not expose member-to-member DMs to bots" in AUDIT_GUARD
    assert "build_quick_mod_view" in AUDIT_GUARD
    assert "view=view" in AUDIT_GUARD


if __name__ == "__main__":
    for test in (
        test_suspicious_name_regex_is_not_substring_real_trap,
        test_low_flagged_accounts_do_not_render_as_clear,
        test_staff_join_audit_has_dm_safety_context_and_quick_mod,
    ):
        test()
        print(f"PASS {test.__name__}")
