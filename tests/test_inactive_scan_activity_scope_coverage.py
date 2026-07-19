from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from stoney_verify.members_new.activity_service import InactiveScanOptions, InactiveScanReport


SOURCE = Path("stoney_verify/members_new/activity_service.py").read_text(encoding="utf-8")
QUEUE_SOURCE = Path("stoney_verify/commands_ext/public_members_cleanup_group.py").read_text(encoding="utf-8")


def _report(*, scope_percent: int, scope_complete: bool, source_read: int = 3, source_attempted: int = 3) -> InactiveScanReport:
    return InactiveScanReport(
        guild_id=123,
        scanned_at=datetime.now(timezone.utc),
        options=InactiveScanOptions(),
        total_members_seen=10,
        candidates=[],
        protected=[],
        cannot_remove=[],
        data_warnings=[],
        data_sources_read=source_read,
        data_sources_attempted=source_attempted,
        activity_scope_total_channels=4,
        activity_scope_accessible_channels=round((scope_percent / 100) * 4),
        activity_scope_coverage_percent=scope_percent,
        activity_scope_complete=scope_complete,
    )


def test_inaccessible_channels_prevent_data_coverage_from_claiming_100_percent() -> None:
    report = _report(scope_percent=50, scope_complete=False)

    assert report.data_coverage_percent == 50
    assert report.data_confidence_label == "Incomplete channel scope"


def test_complete_channel_scope_preserves_full_data_source_coverage() -> None:
    report = _report(scope_percent=100, scope_complete=True)

    assert report.data_coverage_percent == 100
    assert report.data_confidence_label == "Good"


def test_partial_data_sources_and_partial_channel_scope_use_the_more_conservative_percentage() -> None:
    report = _report(scope_percent=75, scope_complete=False, source_read=1, source_attempted=2)

    assert report.data_coverage_percent == 50
    assert report.data_confidence_label == "Incomplete channel scope"


def test_inactive_scan_actionability_requires_complete_channel_scope() -> None:
    assert "scope_report = audit_activity_scope(guild)" in SOURCE
    assert "report_actionable = bool(coverage.actionable and scope_report.complete)" in SOURCE
    assert "candidate.removable = False" in SOURCE
    assert "activity_scope_problems=scope_problem_lines" in SOURCE


def test_cleanup_queue_displays_the_scope_aware_report_coverage() -> None:
    assert "report.data_confidence_label" in QUEUE_SOURCE
    assert "report.data_coverage_percent" in QUEUE_SOURCE
