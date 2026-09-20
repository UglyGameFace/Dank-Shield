from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EVENTS = (ROOT / "stoney_verify" / "events.py").read_text(encoding="utf-8")
APP = (ROOT / "stoney_verify" / "app.py").read_text(encoding="utf-8")
ACTIVITY = (ROOT / "stoney_verify" / "members_new" / "activity_tracker.py").read_text(encoding="utf-8")
INVITE = (ROOT / "stoney_verify" / "invite_reconciliation_runtime.py").read_text(encoding="utf-8")
POLICY = (ROOT / "stoney_verify" / "invite_policy_engine.py").read_text(encoding="utf-8")
SURFACE = (ROOT / "stoney_verify" / "invite_policy_message_surface_runtime.py").read_text(encoding="utf-8")
COORDINATOR = (ROOT / "stoney_verify" / "startup_recovery_coordinator.py").read_text(encoding="utf-8")


def _block(source: str, start_marker: str, end_marker: str) -> str:
    start = source.index(start_marker)
    end = source.index(end_marker, start)
    return source[start:end]


def test_events_startup_no_longer_owns_member_full_sync_or_departed_reconcile() -> None:
    block = _block(
        EVENTS,
        "async def _run_startup_once_flags() -> None:",
        "@bot.event\nasync def on_ready()",
    )

    assert "_initial_member_sync_sweep()" not in block
    assert "new_run_departed_reconciliation_for_guild" not in block
    assert "_initial_member_sync_started" not in block


def test_new_guild_bootstrap_is_event_scoped_not_ready_scoped() -> None:
    assert '@bot.listen("on_guild_join")' in APP
    assert "on_guild_join_member_bootstrap" in APP
    assert "_run_full_member_sync_for_guild(guild)" in APP

    ready = _block(
        APP,
        '@bot.listen("on_ready")',
        '@bot.listen("on_guild_join")',
    )
    assert "_run_full_member_sync_for_guild" not in ready


def test_startup_member_departure_recovery_uses_shared_guild_slot() -> None:
    block = _block(
        APP,
        "async def _maybe_run_departed_reconcile_once() -> None:",
        "async def _bootstrap_new_guild_members",
    )

    assert 'startup_recovery_slot(gid, "member_departure_reconcile")' in block
    assert "_run_departed_reconciliation_for_guild(guild)" in block


def test_activity_and_invite_recovery_share_bounded_coordinator() -> None:
    assert "_STARTUP_RECONCILE_LOCK" not in ACTIVITY
    assert "startup_recovery_slot(" in ACTIVITY
    assert "startup_recovery_slot(" in INVITE

    assert "DANK_STARTUP_RECOVERY_MAX_CONCURRENT" in COORDINATOR
    assert "_MAX_CONCURRENT" in COORDINATOR
    assert "_GUILD_SLOTS" in COORDINATOR
    assert "_GUILD_SLOTS.pop(gid, None)" in COORDINATOR


def test_invite_startup_recovery_uses_durable_gap_window() -> None:
    assert "persisted_last_heartbeat_at" in INVITE
    assert "_recovery_window(guild)" in INVITE
    assert 'scan_kwargs["after"] = after' in INVITE
    assert 'scan_kwargs["before"] = before' in INVITE
    assert "_channel_may_have_messages_after" in INVITE

    assert 'history_kwargs["after"] = after' in POLICY
    assert 'history_kwargs["before"] = before' in POLICY


def test_activity_pins_pre_restart_heartbeat_before_state_advances() -> None:
    assert "_STARTUP_RECOVERY_HEARTBEATS" in ACTIVITY
    assert "previous_process != _PROCESS_ID" in ACTIVITY
    assert "_STARTUP_RECOVERY_HEARTBEATS.setdefault(" in ACTIVITY
    assert "if stored_process == _PROCESS_ID:" in ACTIVITY


def test_invite_surface_never_reads_deprecated_message_interaction() -> None:
    assert 'getattr(message, "interaction_metadata", None)' in SURFACE
    assert 'getattr(message, "interaction", None)' not in SURFACE
    assert '("interaction_metadata", "interaction")' not in SURFACE
