from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from stoney_verify.startup_guards import owner_emergency_close_bridge as close_bridge


ROOT = Path(__file__).resolve().parents[1]


def test_owner_emergency_override_ui_is_loaded_and_fail_closed() -> None:
    guard = (
        ROOT / "stoney_verify/startup_guards/owner_emergency_override_guard.py"
    ).read_text(encoding="utf-8")
    close_bridge_source = (
        ROOT / "stoney_verify/startup_guards/owner_emergency_close_bridge.py"
    ).read_text(encoding="utf-8")
    audit_gate = (
        ROOT / "stoney_verify/startup_guards/owner_emergency_audit_gate.py"
    ).read_text(encoding="utf-8")
    action_guard = (
        ROOT / "stoney_verify/startup_guards/ticket_action_lock_guard.py"
    ).read_text(encoding="utf-8")
    service = (
        ROOT / "stoney_verify/tickets_new/owner_emergency_override.py"
    ).read_text(encoding="utf-8")

    assert "is_actual_guild_owner" in guard
    assert 'upper() != "OVERRIDE"' in guard
    assert "Emergency Override is restricted to the actual Discord server owner" in guard
    assert "panel.TicketChannelActionsView" in guard
    assert "transcripts.TicketOpenActionsView" in guard
    assert "transcripts.StaffClosedTicketView" in guard
    assert "_refresh_existing_control_messages" in guard
    assert "owner_emergency_override_guard" in action_guard
    assert "owner_emergency_close_bridge" in action_guard
    assert "owner_emergency_audit_gate" in action_guard

    assert "_confirmed_close_matches" in close_bridge_source
    assert "confirmed_ui_context" in close_bridge_source
    assert "canonical_event_attribution" in close_bridge_source
    assert "database_owner_attribution" in close_bridge_source
    assert "owner_emergency_authorizer" in close_bridge_source
    assert "owner_attributed_close_logger" in close_bridge_source

    assert "audit_unavailable" in audit_gate
    assert "mutation_started" in audit_gate
    assert "ticket_owner_emergency_override_authorized" in audit_gate
    assert "ticket_owner_emergency_override_failed" in audit_gate
    assert "if not result.ok" in audit_gate
    assert "override_reason" in audit_gate
    assert "override_timestamp" in audit_gate

    assert "owner_emergency_delete_prepare" in service
    assert "owner_emergency_delete" in service
    assert "ticket_has_transcript" in service
    assert "previous_claimed_by" in service
    assert "WeakValueDictionary" in service
    assert "_log_override" not in service
    assert "_log_assignment_event" in service
    assert "_log_delete_event" in service
    assert "repo_transfer" in service
    assert "repo_unclaim" in service
    assert "await channel.delete" in service


def test_emergency_close_capability_is_bound_to_exact_channel_and_owner() -> None:
    channel = SimpleNamespace(id=55)
    owner = SimpleNamespace(id=999)

    assert close_bridge._confirmed_close_matches(channel, owner) is False

    token = close_bridge._CONFIRMED_CLOSE.set((55, 999, "Server Owner"))
    try:
        assert close_bridge._confirmed_close_matches(channel, owner) is True
        assert close_bridge._confirmed_close_matches(SimpleNamespace(id=56), owner) is False
        assert close_bridge._confirmed_close_matches(channel, SimpleNamespace(id=998)) is False
    finally:
        close_bridge._CONFIRMED_CLOSE.reset(token)

    assert close_bridge._confirmed_close_matches(channel, owner) is False


def test_normal_owner_authority_remains_distinct_from_emergency_override() -> None:
    policy = (
        ROOT / "stoney_verify/tickets_new/claim_policy.py"
    ).read_text(encoding="utf-8")

    assert 'clean_action.startswith("owner_emergency_")' in policy
    assert 'clean_action == "owner_emergency_transfer"' in policy
    assert 'clean_action == "owner_emergency_unclaim"' in policy
    assert 'clean_action == "owner_emergency_close"' in policy
    assert 'clean_action == "owner_emergency_delete"' in policy
    assert 'return decision(\n            True,\n            "guild_owner_allowed"' in policy
    assert "without claiming or replacing the recorded claimant" in policy
    assert "owner_emergency_close_allowed" in policy
