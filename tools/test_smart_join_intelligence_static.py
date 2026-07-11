from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RAID = (ROOT / "stoney_verify/raidguard.py").read_text(encoding="utf-8")
MODLOG = (ROOT / "stoney_verify/modlog.py").read_text(encoding="utf-8")


def test_official_bot_and_human_risk_are_separate() -> None:
    assert "Official Bot: Yes" in RAID
    assert "Official Bot: No" in RAID
    assert "Alt/Raid Risk:" in RAID
    assert "DM Raider Risk:" in RAID
    assert "DM Raider Report Risk" not in RAID


def test_modlog_has_smart_join_intelligence() -> None:
    assert "def _smart_join_intelligence_value(" in MODLOG
    assert "Smart Join Intelligence" in MODLOG
    assert "Human automation/userbot risk:" in MODLOG
    assert "Invite/source confidence:" in MODLOG
    assert "Context gap:" in MODLOG
    assert "Recommended action:" in MODLOG


def test_join_source_truth_is_visible() -> None:
    assert 'fields.append(("Join Source"' in MODLOG
    assert "Entry method:" in MODLOG
    assert "Confidence:" in MODLOG
    assert "Why:" in MODLOG


if __name__ == "__main__":
    for test in (
        test_official_bot_and_human_risk_are_separate,
        test_modlog_has_smart_join_intelligence,
        test_join_source_truth_is_visible,
    ):
        test()
        print(f"PASS {test.__name__}")
