from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENGINE = (ROOT / "stoney_verify/member_risk_engine.py").read_text(encoding="utf-8")
MODLOG = (ROOT / "stoney_verify/modlog.py").read_text(encoding="utf-8")
EVENTS = (ROOT / "stoney_verify/events.py").read_text(encoding="utf-8")


def _block(source: str, start: str, end: str) -> str:
    start_index = source.index(start)
    end_index = source.index(end, start_index)
    return source[start_index:end_index]


def test_weak_profile_context_is_not_alt_identity() -> None:
    assert "def _derive_alt_evidence" in ENGINE
    assert 'return "clear", 0' in ENGINE
    assert '"evidence_tier": alt_tier' in ENGINE
    assert '"new_account_context": is_new_account' in ENGINE
    assert 'review_verdict = "NEW ACCOUNT — VERIFY NORMALLY"' in ENGINE


def test_member_updates_do_not_repeat_join_dossier() -> None:
    block = _block(
        MODLOG,
        "async def maybe_log_member_update_diff(",
        "def _voice_state_change_lines(",
    )
    assert "_build_member_context_fields" not in block
    assert "_is_expected_recent_unverified_assignment" in block
    assert "event_key=_member_update_event_key" in block


def test_risk_cards_hide_setup_and_unknown_filler() -> None:
    block = _block(
        MODLOG,
        "async def _build_member_context_fields(",
        "# ==========================================================\n# Public logging helpers",
    )
    for stale in (
        "ROLE CONFIG MISSING",
        "Same-source history: not enough matching history yet",
        "DM/userbot scope",
        "Official bot: **{'Yes' if is_bot else 'No'}**",
        "burst joins=",
    ):
        assert stale not in block
    assert '"Assessment"' in block
    assert '"Relevant Context"' in block


def test_join_card_hides_unresolved_source_noise() -> None:
    source_block = _block(
        EVENTS,
        "def _join_context_modlog_value(",
        "# ============================================================\n# VC session helpers",
    )
    join_block = _block(
        EVENTS,
        "async def on_member_join(member: discord.Member):",
        "async def on_member_remove(member: discord.Member):",
    )
    assert "Source could not be confirmed." not in join_block
    assert 'name="Entry Details"' in join_block
    assert "if source_text:" in join_block
    assert 'return "\\n".join(lines)[:1024]' in source_block


if __name__ == "__main__":
    for test in (
        test_weak_profile_context_is_not_alt_identity,
        test_member_updates_do_not_repeat_join_dossier,
        test_risk_cards_hide_setup_and_unknown_filler,
        test_join_card_hides_unresolved_source_noise,
    ):
        test()
        print(f"PASS {test.__name__}")
