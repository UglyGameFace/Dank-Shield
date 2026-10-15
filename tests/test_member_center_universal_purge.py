"""Member Center history, all-member purge and no-repeat scan regressions."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from stoney_verify.commands_ext import member_command_center as center
from stoney_verify.commands_ext import public_members_cleanup_group as group
from stoney_verify.members_new import cleanup_service as cleanup
from stoney_verify.members_new.activity_service import InactiveScanOptions

ROOT = Path(__file__).resolve().parents[1]


def test_departed_history_renders_without_live_discord_user() -> None:
    snapshot = {
        "member": {"username": "FormerMember", "in_guild": False},
        "join_summary": {
            "times_joined": 2,
            "times_left": 2,
            "last_seen_at": "2026-10-05T02:00:00Z",
            "left_at": "2026-10-06T02:00:00Z",
        },
        "event_summary": {
            "total": 4, "moderation_count": 1, "latest_event_type": "kick",
            "type_counts": {"kick": 1, "message": 3},
        },
        "ticket_summary": {"total": 2},
        "name_history": {"all_names": ["FormerMember"]},
        "recent_events": [{"event_type": "kick"}],
    }
    embed = center._historical_member_embed(123456789012345678, snapshot, [
        {"metadata": {"verdict_label": "Monitor"}}
    ])
    text = str(embed.description) + " ".join(str(field.value) for field in embed.fields)
    assert "FormerMember" in text
    assert "123456789012345678" in text
    assert "kick" in text
    assert "Last seen" in text
    assert "Monitor" in text


def test_history_by_id_does_not_require_fetching_departed_user() -> None:
    src = (ROOT / "stoney_verify/commands_ext/member_command_center.py").read_text()
    assert "get_member_context_snapshot(" in src
    assert "get_member_review_history," in src
    assert "fetch_user(user_id)" not in src
    assert "from .public_member_review_feedback import review_history" not in src
    assert "await _show_stored_member_history(interaction, self.member_id)" in src


def test_scope_is_not_limited_to_verified_or_resident() -> None:
    source = (ROOT / "stoney_verify/commands_ext/public_members_cleanup_group.py").read_text()
    scanner = (ROOT / "stoney_verify/members_new/activity_service.py").read_text()
    service = (ROOT / "stoney_verify/members_new/cleanup_service.py").read_text()
    assert 'verified_resident_focus=False' in source
    assert 'verified_resident_focus=False' in service
    assert 'if not bool(getattr(candidate, "verified_or_resident", False)):' not in source
    assert 'if candidate.verified_or_resident and not exact_verification:' in scanner
    assert "has_direct_proof = any(" in scanner
    assert "_RECENT_EVIDENCE_PER_CHANNEL_LIMIT = 100" in scanner
    assert "coverage.continuous_since" in scanner
    assert "if len(queued) >= 20:" in source
    assert "require_confirmation = True" in source


def test_nonverified_candidate_can_be_eligible_if_proven() -> None:
    candidate = SimpleNamespace(
        verified_or_resident=False,
        inactivity_days=120,
        removable=True, protected=False, cannot_remove=False,
        confidence="High", status="Review candidate",
    )
    ok, reason = group._candidate_is_purge_all_eligible(
        candidate, inactive_days=90, include_low_confidence=False
    )
    assert ok, reason
    candidate.inactivity_days = 3
    ok, reason = group._candidate_is_purge_all_eligible(
        candidate, inactive_days=90, include_low_confidence=False
    )
    assert not ok and "newer" in reason


def test_unverified_with_unknown_activity_not_purge_safe() -> None:
    scanner = (ROOT / "stoney_verify/members_new/activity_service.py").read_text()
    assert "No direct activity timestamp from the verified tracking" in scanner
    assert "candidate.removable = False" in scanner
    assert 'and _safe_dt(signal.timestamp) >= coverage.continuous_since' in scanner


def test_action_time_scans_once_per_batch(monkeypatch) -> None:
    counter = {"scans": 0, "removals": 0}
    options = InactiveScanOptions(
        inactive_days=90, grace_days=14, verified_resident_focus=False
    )
    report = SimpleNamespace(
        actionable=True, options=options, guild_id=111,
        scanned_at=datetime.now(timezone.utc),
    )

    async def scan(_guild, _opts):
        counter["scans"] += 1
        return report

    async def execute(_guild, request):
        assert request.authoritative_scan_report is report
        assert request.inactive_days == 90
        assert request.grace_days == 14
        counter["removals"] += 1
        return cleanup.MemberCleanupResult(True, "Member removed", request.target_user_id)

    async def zero_sleep(_delay):
        return None

    monkeypatch.setattr(group, "scan_inactive_members", scan)
    monkeypatch.setattr(group, "execute_member_cleanup", execute)
    monkeypatch.setattr(group.asyncio, "sleep", zero_sleep)
    items = [
        group.QueuePreviewItem(
            candidate=SimpleNamespace(user_id=222 + i),
            validation=SimpleNamespace(ok=True),
            inactive_days=90, grace_days=14,
        ) for i in range(3)
    ]
    interaction = SimpleNamespace(
        guild=SimpleNamespace(id=111),
        user=SimpleNamespace(id=123),
    )
    removed, blocked, failed, result_rows = asyncio.run(
        group._process_queue_items(interaction, items=items, reason="Confirmed cleanup")
    )
    assert counter == {"scans": 1, "removals": 3}
    assert len(removed) == 3
    assert len(result_rows) == 3
    assert not blocked and not failed


def test_authoritative_scan_is_short_lived_and_scope_bound(monkeypatch) -> None:
    actor = SimpleNamespace(
        id=120,
        top_role=10,
        guild_permissions=SimpleNamespace(administrator=True),
    )
    target = SimpleNamespace(
        id=220, name="RegularMember", display_name="RegularMember", bot=False,
        top_role=1,
    )
    bot = SimpleNamespace(
        id=300,
        top_role=20,
        guild_permissions=SimpleNamespace(kick_members=True),
    )
    guild = SimpleNamespace(
        id=888, owner_id=500, me=bot,
        get_member=lambda uid: {120: actor, 220: target}.get(uid),
    )
    actor.guild = guild

    async def roles(_guild_id):
        return set()

    monkeypatch.setattr(cleanup, "_load_cleanup_protected_role_ids", roles)
    monkeypatch.setattr(cleanup, "_is_staff_like", lambda *_args: False)
    monkeypatch.setattr(cleanup, "is_scan_user_locked", None)

    candidate = SimpleNamespace(
        user_id=220, removable=True, inactivity_days=120,
    )
    report = SimpleNamespace(
        guild_id=888,
        scanned_at=datetime.now(timezone.utc),
        options=InactiveScanOptions(inactive_days=90, grace_days=14, verified_resident_focus=False),
        actionable=True,
        candidates=[candidate],
    )
    request = cleanup.MemberCleanupRequest(
        guild_id=888, target_user_id=220, actor_user_id=120,
        inactive_days=90, grace_days=14, authoritative_scan_report=report,
    )
    allowed = asyncio.run(cleanup.validate_member_cleanup(guild, request))
    assert allowed.ok, allowed.reasons
    report.scanned_at = datetime.now(timezone.utc) - timedelta(minutes=2)
    blocked = asyncio.run(cleanup.validate_member_cleanup(guild, request))
    assert not blocked.ok
    assert "expired" in blocked.reason_text.lower()
