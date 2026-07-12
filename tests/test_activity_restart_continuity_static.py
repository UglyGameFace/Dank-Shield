from pathlib import Path


TRACKER = Path(
    "stoney_verify/members_new/activity_tracker.py"
).read_text(encoding="utf-8")

RECONCILE = Path(
    "stoney_verify/members_new/"
    "activity_reconciliation.py"
).read_text(encoding="utf-8")

ACTIVITY = Path(
    "stoney_verify/members_new/activity_service.py"
).read_text(encoding="utf-8")

MIGRATION = Path(
    "supabase/migrations/"
    "20260712_member_activity_restart_continuity.sql"
).read_text(encoding="utf-8")


def test_restart_reads_previous_state_before_resetting():
    start = TRACKER.index(
        "async def _start_guild_tracking("
    )
    end = TRACKER.index(
        "async def _heartbeat_loop(",
        start,
    )
    block = TRACKER[start:end]

    assert "_select_tracker_state_sync" in block
    assert "reconcile_restart_gap(" in block
    assert "_resume_tracker_sync" in block
    assert "_start_tracker_sync" in block

    assert (
        block.index("_select_tracker_state_sync")
        < block.index("reconcile_restart_gap(")
        < block.index("_resume_tracker_sync")
    )


def test_reconciliation_is_bounded_and_fail_closed():
    assert "max_reconcile_gap_seconds" in TRACKER
    assert "DANK_ACTIVITY_RECONCILE_MAX_GAP_SECONDS" in RECONCILE
    assert "DANK_ACTIVITY_RECONCILE_MAX_MESSAGES" in RECONCILE
    assert "asyncio.gather(" not in RECONCILE
    assert "limit=None" not in RECONCILE
    assert "per_channel_limit + 1" in RECONCILE
    assert "thread_limit + 1" in RECONCILE
    assert "restart_reconciliation_failed" in TRACKER


def test_history_is_replayed_between_exact_cutover_times():
    assert "after=after" in RECONCILE
    assert "before=before" in RECONCILE
    assert "oldest_first=True" in RECONCILE
    assert "archived_threads(" in RECONCILE


def test_unreadable_channels_or_private_threads_block_coverage():
    assert "audit_guild_activity_scope" in RECONCILE
    assert "read_message_history" in RECONCILE
    assert "manage_threads" in RECONCILE
    assert "_LOCAL_SCOPE_ERRORS" in TRACKER
    assert "scope_became_incomplete" in TRACKER
    assert "scope_permissions_restored" in TRACKER


def test_reactions_cannot_authorize_cleanup():
    start = ACTIVITY.index(
        '"member_activity_ledger",'
    )
    end = ACTIVITY.index(
        '"ticket_messages",',
        start,
    )
    block = ACTIVITY[start:end]

    assert '"last_message_at"' in block
    assert '"last_interaction_at"' in block
    assert '"last_ticket_message_at"' in block
    assert '"last_activity_at"' not in block
    assert '"last_reaction_at"' not in block
    assert "Reactions are supplemental" in block


def test_resume_rpc_preserves_continuous_since():
    assert (
        "create or replace function "
        "public.resume_member_activity_tracker"
        in MIGRATION
    )

    update_start = MIGRATION.index(
        "update public.member_activity_tracker_state"
    )
    update_end = MIGRATION.index(
        "get diagnostics changed_rows",
        update_start,
    )
    update_block = MIGRATION[
        update_start:update_end
    ]

    assert "process_id = p_new_process_id" in update_block
    assert "last_heartbeat_at = p_resumed_at" in update_block
    assert "continuous_since =" not in update_block
    assert "return changed_rows = 1" in MIGRATION


def test_rpc_uses_compare_and_swap_state_guard():
    assert "process_id = p_previous_process_id" in MIGRATION
    assert (
        "last_heartbeat_at = p_previous_heartbeat_at"
        in MIGRATION
    )
    assert (
        "coalesce(event_writes_failed, 0) = 0"
        in MIGRATION
    )
    assert "coalesce(last_error, '') = ''" in MIGRATION
