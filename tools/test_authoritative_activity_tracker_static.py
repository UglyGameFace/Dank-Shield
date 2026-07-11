from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# This test exercises pure coverage calculations only. Do not import the live
# Supabase client or bot database configuration.
import types

globals_stub = types.ModuleType("stoney_verify.globals")
globals_stub.get_supabase = lambda: None
sys.modules["stoney_verify.globals"] = globals_stub

from stoney_verify.members_new.activity_tracker import (
    evaluate_coverage_state,
)

TRACKER = (
    ROOT / "stoney_verify/members_new/activity_tracker.py"
).read_text(encoding="utf-8")

ACTIVITY = (
    ROOT / "stoney_verify/members_new/activity_service.py"
).read_text(encoding="utf-8")

CLEANUP = (
    ROOT / "stoney_verify/members_new/cleanup_service.py"
).read_text(encoding="utf-8")

CLEANUP_COMMANDS = (
    ROOT / "stoney_verify/commands_ext/public_members_cleanup_group.py"
).read_text(encoding="utf-8")

APP = (
    ROOT / "stoney_verify/app.py"
).read_text(encoding="utf-8")

MIGRATION = (
    ROOT
    / "supabase/migrations/20260711_member_activity_truth_ledger.sql"
).read_text(encoding="utf-8")


def test_tracker_records_only_direct_member_actions() -> None:
    assert 'activity_type="message"' in TRACKER
    assert 'activity_type="reaction"' in TRACKER
    assert 'activity_type="interaction"' in TRACKER
    assert '"ticket_message"' in TRACKER

    assert "on_voice_state_update" not in TRACKER
    assert "presence" in TRACKER.lower()


def test_native_app_installs_tracker() -> None:
    assert "install_activity_tracker" in APP
    assert "_install_activity_tracker(bot)" in APP


def test_schema_has_atomic_activity_and_continuity_functions() -> None:
    assert "create table if not exists public.member_activity_ledger" in MIGRATION
    assert "create table if not exists public.member_activity_tracker_state" in MIGRATION
    assert "create or replace function public.record_member_activity" in MIGRATION
    assert "create or replace function public.start_member_activity_tracker" in MIGRATION
    assert "create or replace function public.heartbeat_member_activity_tracker" in MIGRATION
    assert "create or replace function public.fail_member_activity_tracker" in MIGRATION


def test_scan_uses_authoritative_coverage() -> None:
    assert "get_activity_coverage_status" in ACTIVITY
    assert "authoritative activity ledger" in ACTIVITY
    assert "coverage.actionable" in ACTIVITY
    assert "coverage.continuous_since" in ACTIVITY
    assert "Exact verification timing is required" in ACTIVITY


def test_cleanup_preserves_scan_threshold() -> None:
    assert "inactive_days: int = 90" in CLEANUP
    assert "inactive_days=int(item.inactive_days)" in CLEANUP_COMMANDS
    assert "inactive_days=int(report.options.inactive_days)" in CLEANUP_COMMANDS


def test_coverage_never_activates_early() -> None:
    now = datetime(2026, 7, 11, tzinfo=timezone.utc)
    process_id = "process-a"

    row = {
        "guild_id": "1",
        "process_id": process_id,
        "continuous_since": (
            now - timedelta(days=89, hours=23)
        ).isoformat(),
        "last_heartbeat_at": (
            now - timedelta(seconds=30)
        ).isoformat(),
        "event_writes_failed": 0,
    }

    status = evaluate_coverage_state(
        row,
        guild_id=1,
        now=now,
        required_days=90,
        expected_process_id=process_id,
    )

    assert status.actionable is False
    assert status.observed_days == 89


def test_coverage_activates_after_full_threshold() -> None:
    now = datetime(2026, 7, 11, tzinfo=timezone.utc)
    process_id = "process-a"

    row = {
        "guild_id": "1",
        "process_id": process_id,
        "continuous_since": (
            now - timedelta(days=91)
        ).isoformat(),
        "last_heartbeat_at": (
            now - timedelta(seconds=30)
        ).isoformat(),
        "event_writes_failed": 0,
    }

    status = evaluate_coverage_state(
        row,
        guild_id=1,
        now=now,
        required_days=90,
        expected_process_id=process_id,
    )

    assert status.actionable is True
    assert status.observed_days == 91


def test_restart_or_stale_heartbeat_fails_closed() -> None:
    now = datetime(2026, 7, 11, tzinfo=timezone.utc)

    base = {
        "guild_id": "1",
        "process_id": "old-process",
        "continuous_since": (
            now - timedelta(days=120)
        ).isoformat(),
        "last_heartbeat_at": (
            now - timedelta(seconds=30)
        ).isoformat(),
    }

    restarted = evaluate_coverage_state(
        base,
        guild_id=1,
        now=now,
        required_days=90,
        expected_process_id="new-process",
    )

    assert restarted.actionable is False

    stale = dict(base)
    stale["process_id"] = "new-process"
    stale["last_heartbeat_at"] = (
        now - timedelta(minutes=10)
    ).isoformat()

    stale_status = evaluate_coverage_state(
        stale,
        guild_id=1,
        now=now,
        required_days=90,
        expected_process_id="new-process",
    )

    assert stale_status.actionable is False


if __name__ == "__main__":
    for test in (
        test_tracker_records_only_direct_member_actions,
        test_native_app_installs_tracker,
        test_schema_has_atomic_activity_and_continuity_functions,
        test_scan_uses_authoritative_coverage,
        test_cleanup_preserves_scan_threshold,
        test_coverage_never_activates_early,
        test_coverage_activates_after_full_threshold,
        test_restart_or_stale_heartbeat_fails_closed,
    ):
        test()
        print(f"PASS {test.__name__}")
