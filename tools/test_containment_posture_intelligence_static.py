from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODLOG = (ROOT / "stoney_verify/modlog.py").read_text(encoding="utf-8")


def test_containment_helper_exists() -> None:
    assert "def _containment_posture_value(" in MODLOG
    assert "def _member_has_named_role(" in MODLOG
    assert "UNVERIFIED / CONTAINED" in MODLOG
    assert "VERIFIED ACCESS" in MODLOG
    assert "UNKNOWN ROLE STATE" in MODLOG


def test_containment_field_added_to_member_context() -> None:
    assert 'fields.append(("Containment Posture"' in MODLOG
    assert "Hold reasons:" in MODLOG
    assert "Recommended action:" in MODLOG


def test_containment_uses_source_and_risk_context() -> None:
    assert "same source has risky join history" in MODLOG
    assert "invite/source unresolved or low-confidence" in MODLOG
    assert "Keep contained until verification" in MODLOG
    assert 'locals().get("source_reputation", {})' in MODLOG


if __name__ == "__main__":
    for test in (
        test_containment_helper_exists,
        test_containment_field_added_to_member_context,
        test_containment_uses_source_and_risk_context,
    ):
        test()
        print(f"PASS {test.__name__}")
