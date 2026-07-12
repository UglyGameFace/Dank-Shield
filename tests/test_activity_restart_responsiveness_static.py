from pathlib import Path


TRACKER = Path(
    "stoney_verify/members_new/activity_tracker.py"
).read_text(encoding="utf-8")

RECONCILE = Path(
    "stoney_verify/members_new/activity_reconciliation.py"
).read_text(encoding="utf-8")

COMMAND = Path(
    "stoney_verify/commands_ext/public_members_group.py"
).read_text(encoding="utf-8")


def _on_ready_block() -> str:
    start = TRACKER.index("async def _on_ready(")
    end = TRACKER.index(
        "async def _on_guild_join(",
        start,
    )
    return TRACKER[start:end]


def test_on_ready_schedules_but_never_awaits_reconciliation():
    block = _on_ready_block()

    assert "_schedule_guild_tracking(" in block
    assert "await _start_guild_tracking(" not in block
    assert "reconcile_restart_gap(" not in block


def test_reconciliation_runs_in_bounded_background_task():
    assert "async def _run_scheduled_guild_tracking(" in TRACKER
    assert "asyncio.wait_for(" in TRACKER
    assert "reconcile_timeout_seconds()" in TRACKER
    assert "_STARTUP_RECONCILE_LOCK" in TRACKER
    assert "_STARTUP_TASKS" in TRACKER


def test_timeout_resets_fail_closed():
    assert "_force_new_window_after_startup_failure(" in TRACKER
    assert "restart reconciliation exceeded" in TRACKER
    assert "_start_tracker_sync" in TRACKER


def test_history_reconciliation_has_no_task_fanout():
    assert "asyncio.gather(" not in RECONCILE
    assert "limit=None" not in RECONCILE
    assert "for channel in channels:" in RECONCILE
    assert "per_channel_limit + 1" in RECONCILE
    assert "thread_limit + 1" in RECONCILE


def test_coverage_defers_before_bounded_lookup():
    start = COMMAND.index(
        "async def members_coverage("
    )
    end = COMMAND.index(
        '@members_group.command(name="scan"',
        start,
    )
    block = COMMAND[start:end]

    assert "interaction.response.defer(" in block
    assert "asyncio.wait_for(" in block
    assert "timeout=8.0" in block
    assert (
        block.index("interaction.response.defer(")
        < block.index("asyncio.wait_for(")
    )


def test_coverage_timeout_remains_review_only():
    assert "Coverage lookup timed out." in COMMAND
    assert "actionable=False" in COMMAND
    assert "storage_ready=False" in COMMAND



def test_all_discord_iterators_are_bounded():
    assert "DANK_ACTIVITY_RECONCILE_MAX_CHANNELS" in RECONCILE
    assert (
        "DANK_ACTIVITY_RECONCILE_MAX_THREADS_PER_PARENT"
        in RECONCILE
    )
    assert (
        "DANK_ACTIVITY_RECONCILE_MAX_MESSAGES_PER_CHANNEL"
        in RECONCILE
    )
    assert "DANK_ACTIVITY_RECONCILE_MAX_MESSAGES" in RECONCILE
    assert "_bounded_async_items(" in RECONCILE
    assert "exceeded the safe limit" in RECONCILE



def test_restart_status_copy_matches_safe_reconciliation():
    assert "after every bot restart" not in TRACKER
    assert "Restarts, stale heartbeats" not in COMMAND
    assert "Restart reconciliation has not " in TRACKER
    assert "completed or could not be verified" in TRACKER
    assert "Safe restarts preserve coverage" in COMMAND
    assert "Unverified gaps" in COMMAND
