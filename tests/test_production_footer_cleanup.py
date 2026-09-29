from __future__ import annotations

from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = ROOT / "stoney_verify"


def _source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_production_footer_calls_do_not_expose_raw_guild_debug_metadata() -> None:
    offenders: list[str] = []
    pattern = re.compile(
        r"set_footer\s*\([\s\S]{0,180}?Guild\s*\{[^}]+\}",
        re.IGNORECASE,
    )

    for path in SOURCE_ROOT.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if pattern.search(text):
            offenders.append(str(path.relative_to(ROOT)))

    assert offenders == []


def test_known_debug_footer_phrases_are_not_used_by_live_production_ui() -> None:
    forbidden = {
        "stoney_verify/welcome_card_studio_ui.py": (
            "canonical live runtime",
            "Preview fallback • dank_shield:welcome_card_runtime:v1",
        ),
        "stoney_verify/exit_card_studio_ui.py": (
            "dank_shield:exit_card_runtime:v1",
        ),
        "stoney_verify/commands_ext/public_diagnostics_group.py": (
            "diagnostics are per-process, per-guild",
        ),
        "stoney_verify/commands_ext/public_modlog_group.py": (
            "Uses existing modlog_channel_id.",
        ),
        "stoney_verify/commands_ext/public_protection_center.py": (
            "overlapping config bucket",
        ),
        "stoney_verify/commands_ext/public_setup_group.py": (
            "config source:",
        ),
    }

    found: list[str] = []
    for path, phrases in forbidden.items():
        text = _source(path)
        for phrase in phrases:
            if phrase in text:
                found.append(f"{path}: {phrase}")

    assert found == []


def test_runtime_owned_footers_are_human_readable_with_legacy_detection_only() -> None:
    basic_modes = _source("stoney_verify/setup_engine/verification_modes.py")
    basic_runtime = _source("stoney_verify/verification_new/basic_verify.py")
    spam = _source("stoney_verify/spam_guard.py")
    profile = _source("stoney_verify/profile_card_runtime_core.py")
    tickets = _source("stoney_verify/transcripts.py")
    transcript_service = _source("stoney_verify/tickets_new/transcript_service.py")

    assert 'BASIC_VERIFY_FOOTER = "Dank Shield Basic Verify"' in basic_modes
    assert 'LEGACY_BASIC_VERIFY_FOOTERS = ("dank_shield:basic_verify:v1",)' in basic_modes
    assert "LEGACY_BASIC_VERIFY_FOOTERS" in basic_runtime

    assert 'SPAM_PANEL_PUBLIC_PREFIX = "Spam Guard •"' in spam
    assert 'return "SpamGuard quarantine • use Restore Member to reverse this action"' in spam
    restore = spam[spam.index("class SpamIncidentRestoreButton") :]
    assert restore.index("get_quarantine_case_by_modlog_message(") < restore.index(
        "_parse_incident_footer("
    )

    assert "return LIVE_CARD_FOOTER_PREFIX" in profile
    assert "_LEGACY_LIVE_CARD_FOOTER_RE" in profile
    assert "is_live_card_message(stored_message)" in profile

    assert '_CLOSE_PROMPT_MARKER = "Ticket close confirmation"' in tickets
    assert '_TRANSCRIPT_POSTED_MARKER = "Transcript posted"' in tickets
    assert "_LEGACY_MARKERS" in tickets
    assert '_TRANSCRIPT_MARKER = "Transcript posted"' in transcript_service
