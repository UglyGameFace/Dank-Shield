from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RAID = (ROOT / "stoney_verify/raidguard.py").read_text(encoding="utf-8")
MODLOG = (ROOT / "stoney_verify/modlog.py").read_text(encoding="utf-8")


def test_raidguard_separates_official_bot_from_human_risk() -> None:
    assert "Official Bot: Yes" in RAID
    assert "Official Bot: No" in RAID
    assert "Alt/Raid Risk:" in RAID
    assert "DM Raider Risk:" in RAID
    assert "BOT ACCOUNT • excluded from raid/alt scoring" not in RAID


def test_modlog_risk_context_is_not_vague_clear_only() -> None:
    assert "Official Bot: No" in MODLOG
    assert "Official Bot: Yes" in MODLOG
    assert "Alt/Raid Risk:" in MODLOG
    assert "DM Raider Risk: no DM report evidence attached to this join" in MODLOG
    assert "BOT ACCOUNT • Excluded from alt-risk scoring" not in MODLOG


def test_modlog_shows_join_source_truth_reason() -> None:
    assert 'fields.append(("Join Source"' in MODLOG
    assert "entry_quality_reason" in MODLOG
    assert "Entry method:" in MODLOG
    assert "Confidence:" in MODLOG
    assert "Why:" in MODLOG


if __name__ == "__main__":
    for test in (
        test_raidguard_separates_official_bot_from_human_risk,
        test_modlog_risk_context_is_not_vague_clear_only,
        test_modlog_shows_join_source_truth_reason,
    ):
        test()
        print(f"PASS {test.__name__}")
