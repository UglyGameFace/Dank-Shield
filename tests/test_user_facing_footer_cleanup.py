from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_member_setup_explains_eligibility_access_and_visibility_chain() -> None:
    source = _source("stoney_verify/commands_ext/public_member_setup.py")

    assert 'name="Member Access role (automatic)"' in source
    assert 'name="Eligibility prerequisite (optional)"' in source
    assert 'name="How Strict Gate works"' in source
    assert 'label="Member Access Role"' in source
    assert 'label="Eligibility Prerequisite"' in source
    assert "**1. Eligibility:**" in source
    assert "**2. Setup:**" in source
    assert "**3. Access:**" in source
    assert "**4. Visibility:**" in source


def test_cleaned_production_footers_do_not_restore_known_debug_copy() -> None:
    files = (
        "stoney_verify/welcome_card_studio_ui.py",
        "stoney_verify/exit_card_studio_ui.py",
        "stoney_verify/commands_ext/public_setup_assistant.py",
        "stoney_verify/commands_ext/public_setup_solid.py",
        "stoney_verify/commands_ext/public_setup_recommend.py",
        "stoney_verify/commands_ext/public_setup_cleanup.py",
        "stoney_verify/commands_ext/public_modlog_group.py",
        "stoney_verify/commands_ext/public_protection_center.py",
        "stoney_verify/commands_ext/public_diagnostics_group.py",
        "stoney_verify/commands_ext/public_setup_group.py",
        "stoney_verify/commands_ext/public_setup_picker.py",
        "stoney_verify/commands_ext/public_member_lifecycle_logs.py",
        "stoney_verify/commands_ext/public_member_update_modlog.py",
        "stoney_verify/commands_ext/public_mod_ban_toggle_patch.py",
        "stoney_verify/startup_guards/resource_modlog_coverage.py",
        "stoney_verify/startup_guards/full_setup_health_autofix.py",
        "stoney_verify/tickets_new/channel_panel_repair.py",
    )
    forbidden = (
        "canonical live runtime",
        "dank_shield:welcome_card_runtime:v1",
        "dank_shield:exit_card_runtime:v1",
        "Uses existing modlog_channel_id",
        "overlapping config bucket",
        "config source:",
        "source: /mod_ban_toggle",
        "setup assistant",
        "setup check groups existing health evidence",
    )

    combined = "\n".join(_source(path) for path in files)
    for phrase in forbidden:
        assert phrase not in combined


def test_live_profile_footer_hides_ids_but_keeps_legacy_recovery() -> None:
    source = _source("stoney_verify/profile_card_runtime_core.py")

    assert "return LIVE_CARD_FOOTER_PREFIX" in source
    assert "_LEGACY_LIVE_CARD_FOOTER_RE" in source
    assert "owned_metadata" in source
    assert "user:{int(user_id)}" not in source
    assert "trigger:{int(trigger_message_id)}" not in source


def test_basic_verify_uses_human_footer_and_keeps_legacy_detection() -> None:
    modes = _source("stoney_verify/setup_engine/verification_modes.py")
    runtime = _source("stoney_verify/verification_new/basic_verify.py")

    assert 'BASIC_VERIFY_FOOTER = "Dank Shield Basic Verify"' in modes
    assert 'LEGACY_BASIC_VERIFY_FOOTERS = ("dank_shield:basic_verify:v1",)' in modes
    assert "LEGACY_BASIC_VERIFY_FOOTERS" in runtime


def test_spam_guard_incident_and_panel_footers_are_human_readable() -> None:
    source = _source("stoney_verify/spam_guard.py")

    assert 'return "SpamGuard quarantine • use Restore Member to reverse this action"' in source
    assert "get_quarantine_case_by_modlog_message(" in source
    assert 'SPAM_PANEL_PUBLIC_PREFIX = "Spam Guard •"' in source
    assert "SPAM_PANEL_PUBLIC_PREFIX in footer" in source
    # Old encoded values remain only as backward-compatibility parsers.
    assert "INCIDENT_FOOTER_RE" in source
    assert 'SPAM_PANEL_FOOTER_PREFIX = "stoney_verify:spam_guard_panel:"' in source


def test_ticket_runtime_markers_are_human_with_legacy_aliases() -> None:
    source = _source("stoney_verify/transcripts.py")

    assert '_CLOSE_PROMPT_MARKER = "Ticket close confirmation"' in source
    assert '_STAFF_CLOSED_MARKER = "Ticket closed by staff"' in source
    assert '_STAFF_REVIEW_PANEL_MARKER = "Staff ticket review"' in source
    assert '_TRANSCRIPT_POSTED_MARKER = "Transcript posted"' in source
    assert '_OPEN_CONTROLS_MARKER = "Ticket controls"' in source
    assert "_LEGACY_MARKERS" in source
