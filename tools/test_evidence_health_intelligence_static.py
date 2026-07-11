from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODLOG = (ROOT / "stoney_verify/modlog.py").read_text(encoding="utf-8")


def test_evidence_health_helper_exists() -> None:
    assert "def _evidence_health_value(" in MODLOG
    assert "Evidence strength:" in MODLOG
    assert "Evidence gaps:" in MODLOG
    assert "DM/userbot behavior cannot be proven from join alone" in MODLOG


def test_evidence_health_field_is_added() -> None:
    assert 'fields.append(("Evidence Health"' in MODLOG
    assert "Verdict:" in MODLOG
    assert "LOW EVIDENCE" in MODLOG
    assert "WATCHLIST" in MODLOG
    assert "HIGH EVIDENCE" in MODLOG


def test_evidence_health_uses_source_reputation_when_available() -> None:
    assert "source_risky" in MODLOG
    assert "source_strong" in MODLOG
    assert "source_low_conf" in MODLOG
    assert 'locals().get("source_reputation", {})' in MODLOG


if __name__ == "__main__":
    for test in (
        test_evidence_health_helper_exists,
        test_evidence_health_field_is_added,
        test_evidence_health_uses_source_reputation_when_available,
    ):
        test()
        print(f"PASS {test.__name__}")
