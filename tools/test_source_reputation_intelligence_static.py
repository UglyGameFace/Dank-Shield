from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODLOG = (ROOT / "stoney_verify/modlog.py").read_text(encoding="utf-8")


def test_source_reputation_db_helper_exists() -> None:
    assert "def _sb_select_source_reputation_sync(" in MODLOG
    assert "member_joins" in MODLOG
    assert "source_field" in MODLOG
    assert "risky_count" in MODLOG
    assert "strong_or_confirmed_count" in MODLOG


def test_source_reputation_field_is_added() -> None:
    assert "def _source_reputation_value(" in MODLOG
    assert 'fields.append(("Source Reputation"' in MODLOG
    assert "Source key:" in MODLOG
    assert "Risky from same source:" in MODLOG


def test_smart_action_uses_source_pattern() -> None:
    assert "source_reputation=source_reputation" in MODLOG
    assert "Source pattern:" in MODLOG
    assert "same source has risky join history" in MODLOG
    assert "source_low_conf >= 3" in MODLOG


if __name__ == "__main__":
    for test in (
        test_source_reputation_db_helper_exists,
        test_source_reputation_field_is_added,
        test_smart_action_uses_source_pattern,
    ):
        test()
        print(f"PASS {test.__name__}")
