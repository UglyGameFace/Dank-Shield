from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_owner_emergency_override_ui_is_loaded_and_fail_closed() -> None:
    guard = (
        ROOT / "stoney_verify/startup_guards/owner_emergency_override_guard.py"
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

    assert "owner_emergency_delete_prepare" in service
    assert "owner_emergency_delete" in service
    assert "ticket_has_transcript" in service
    assert "previous_claimed_by" in service
    assert "override_reason" in service
    assert "override_timestamp" in service
    assert "repo_transfer" in service
    assert "repo_unclaim" in service
    assert "await channel.delete" in service


def test_normal_actions_are_not_relabelled_as_owner_overrides() -> None:
    policy = (
        ROOT / "stoney_verify/tickets_new/claim_policy.py"
    ).read_text(encoding="utf-8")

    assert 'clean_action.startswith("owner_emergency_")' in policy
    assert 'clean_action == "owner_emergency_transfer"' in policy
    assert 'clean_action == "owner_emergency_unclaim"' in policy
    assert 'clean_action == "owner_emergency_delete"' in policy
    assert 'clean_action == "close" and resolved_guild_owner_id' in policy
    assert "Normal transfer" in policy
