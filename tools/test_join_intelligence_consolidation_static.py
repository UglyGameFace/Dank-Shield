from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODLOG = (ROOT / "stoney_verify/modlog.py").read_text(encoding="utf-8")


def context_block() -> str:
    start = MODLOG.index("async def _build_member_context_fields(")
    end = MODLOG.index(
        "# ==========================================================\n# Public logging helpers",
        start,
    )
    return MODLOG[start:end]


def test_only_canonical_join_fields_are_emitted() -> None:
    block = context_block()
    assert '"Assessment"' in block
    assert '"Relevant Context"' in block
    assert '"Identity Links"' in block

    for old in (
        '"Join Intelligence"',
        '"Evidence & Source"',
        '"Risk Context"',
        '"Evidence Health"',
        '"Containment Posture"',
        '"Smart Join Intelligence"',
        '"Join Source"',
        '"Source Reputation"',
        '"Alt Summary"',
    ):
        assert old not in block


def test_role_state_uses_per_guild_ids_not_name_guessing() -> None:
    assert "from .guild_config import get_guild_config" in MODLOG
    assert "unverified_role_id" in MODLOG
    assert "verified_role_id" in MODLOG
    assert "resident_role_id" in MODLOG
    assert "staff_role_id" in MODLOG
    assert "def _member_has_named_role(" not in MODLOG


def test_old_display_helpers_are_removed() -> None:
    for old in (
        "def _containment_posture_value(",
        "def _evidence_health_value(",
        "def _smart_join_intelligence_value(",
        "def _risk_summary_header(",
        "def _source_reputation_value(",
        "build_alt_detection_summary",
    ):
        assert old not in MODLOG


def test_canonical_summary_is_honest_and_actionable() -> None:
    block = context_block()
    assert "Status:" in block
    assert "Alt identity:" in block
    assert "Spam behavior:" in block
    assert "Profile context:" in block
    assert "Recommended action:" in block
    assert "Context — not identity proof:" in block
    assert "Source confidence:" in block
    assert "Source history:" in block

    for stale in (
        "Access state:",
        "ROLE CONFIG MISSING",
        "Same-source history: not enough matching history yet",
        "DM/userbot scope:",
        "do not treat CLEAR as proof of safety",
        "burst joins=",
    ):
        assert stale not in block


if __name__ == "__main__":
    for test in (
        test_only_canonical_join_fields_are_emitted,
        test_role_state_uses_per_guild_ids_not_name_guessing,
        test_old_display_helpers_are_removed,
        test_canonical_summary_is_honest_and_actionable,
    ):
        test()
        print(f"PASS {test.__name__}")
