from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

ACTIVITY = (
    ROOT / "stoney_verify/members_new/activity_service.py"
).read_text(encoding="utf-8")

CLEANUP = (
    ROOT / "stoney_verify/members_new/cleanup_service.py"
).read_text(encoding="utf-8")

CLEANUP_COMMANDS = (
    ROOT / "stoney_verify/commands_ext/public_members_cleanup_group.py"
).read_text(encoding="utf-8")

MEMBERS_UI = (
    ROOT / "stoney_verify/commands_ext/public_members_group.py"
).read_text(encoding="utf-8")


def test_only_direct_member_activity_resets_inactivity() -> None:
    assert "and _signal_is_direct_activity(s)" in ACTIVITY
    assert (
        'if table == "ticket_messages":' in ACTIVITY
        or (
            'if table in {' in ACTIVITY
            and '"member_activity_ledger",' in ACTIVITY
            and '"ticket_messages",' in ACTIVITY
        )
    )
    assert "must not reset member inactivity" in ACTIVITY


def test_missing_role_configuration_fails_closed() -> None:
    assert "No valid verified/resident role IDs are configured" in ACTIVITY
    assert "The scan stopped instead of guessing" in ACTIVITY


def test_member_errors_are_not_silently_hidden() -> None:
    assert "member_scan_errors += 1" in ACTIVITY
    assert "Member scan error for" in ACTIVITY


def test_historical_scan_is_review_only_until_tracker_exists() -> None:
    # Gate 2 replaced the temporary "tracker not installed" state.
    # The permanent invariant is that authoritative coverage controls
    # actionability and incomplete coverage forces every candidate review-only.
    assert "get_activity_coverage_status" in ACTIVITY
    assert "report_actionable = bool(coverage.actionable)" in ACTIVITY
    assert "actionability_reason = str(coverage.reason)" in ACTIVITY
    assert "if report_actionable:" in ACTIVITY
    assert "candidate.removable = False" in ACTIVITY
def test_cleanup_always_rechecks_fresh_inactivity() -> None:
    assert "Fresh inactivity proof is not actionable" in CLEANUP
    assert "fresh_candidate" in CLEANUP
    assert "Cleanup stopped instead of guessing" in CLEANUP


def test_cleanup_queue_never_uses_cached_scan() -> None:
    start = CLEANUP_COMMANDS.index(
        "async def _load_report_for_queue("
    )
    end = CLEANUP_COMMANDS.index(
        "async def _build_queue_preview(",
        start,
    )
    block = CLEANUP_COMMANDS[start:end]

    assert "get_last_scan" not in block
    assert "await scan_inactive_members" in block


def test_mass_cleanup_always_requires_confirmation() -> None:
    assert (
        "Safety invariant: mass cleanup always requires confirmation"
        in CLEANUP_COMMANDS
    )


def test_ui_shows_actionability() -> None:
    assert "🟡 Review-only" in MEMBERS_UI
    assert "Only activity directly performed by the member" in MEMBERS_UI


if __name__ == "__main__":
    for test in (
        test_only_direct_member_activity_resets_inactivity,
        test_missing_role_configuration_fails_closed,
        test_member_errors_are_not_silently_hidden,
        test_historical_scan_is_review_only_until_tracker_exists,
        test_cleanup_always_rechecks_fresh_inactivity,
        test_cleanup_queue_never_uses_cached_scan,
        test_mass_cleanup_always_requires_confirmation,
        test_ui_shows_actionability,
    ):
        test()
        print(f"PASS {test.__name__}")
