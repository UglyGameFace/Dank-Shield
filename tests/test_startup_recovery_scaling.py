from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EVENTS = (ROOT / "stoney_verify" / "events.py").read_text(encoding="utf-8")
APP = (ROOT / "stoney_verify" / "app.py").read_text(encoding="utf-8")
ACTIVITY = (ROOT / "stoney_verify" / "members_new" / "activity_tracker.py").read_text(encoding="utf-8")
ACTIVITY_RECONCILE = (ROOT / "stoney_verify" / "members_new" / "activity_reconciliation.py").read_text(encoding="utf-8")
MEMBERSHIP = (ROOT / "stoney_verify" / "members_new" / "membership_authority.py").read_text(encoding="utf-8")
KICK_TIMERS = (ROOT / "stoney_verify" / "commands_ext" / "kick_timers.py").read_text(encoding="utf-8")
INVITE = (ROOT / "stoney_verify" / "invite_reconciliation_runtime.py").read_text(encoding="utf-8")
DISCORD_API_SAFETY = (ROOT / "stoney_verify" / "startup_guards" / "discord_api_safety.py").read_text(encoding="utf-8")
ENV_EXAMPLE = (ROOT / ".env.example").read_text(encoding="utf-8")
POLICY = (ROOT / "stoney_verify" / "invite_policy_engine.py").read_text(encoding="utf-8")
SURFACE = (ROOT / "stoney_verify" / "invite_policy_message_surface_runtime.py").read_text(encoding="utf-8")
JOIN_CONTEXT = (ROOT / "stoney_verify" / "members_new" / "join_context_service.py").read_text(encoding="utf-8")
COORDINATOR = (ROOT / "stoney_verify" / "startup_recovery_coordinator.py").read_text(encoding="utf-8")
PANEL_REPAIR = (ROOT / "stoney_verify" / "tickets_new" / "channel_panel_repair.py").read_text(encoding="utf-8")
PANEL_BOOTSTRAP = (ROOT / "stoney_verify" / "tickets_new" / "panel_bootstrap.py").read_text(encoding="utf-8")
DURABLE_INVITE_STATS = (ROOT / "stoney_verify" / "durable_invite_stats.py").read_text(encoding="utf-8")
ANTINUKE_INCIDENT = (ROOT / "stoney_verify" / "anti_nuke_incident_runtime.py").read_text(encoding="utf-8")


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

    assert "_initial_member_sync_sweep" not in EVENTS
    assert "new_run_full_member_sync_for_guild" not in EVENTS
    assert "new_run_departed_reconciliation_for_guild" not in EVENTS
    assert "_initial_member_sync_started" not in block


def test_events_has_no_dead_all_guild_invite_warm_helper() -> None:
    assert "_warm_all_guild_invite_caches" not in EVENTS
    assert "_invite_cache_warm_started" not in EVENTS


def test_new_guild_bootstrap_is_event_scoped_not_ready_scoped() -> None:
    assert '@bot.listen("on_guild_join")' in APP
    assert "on_guild_join_member_bootstrap" in APP
    assert "_run_full_member_sync_for_guild(guild)" in APP
    assert "warm_invite_cache_for_guild(guild)" in APP

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
    assert "_persisted_last_heartbeat_at(gid)" in block
    assert "reason=no_durable_restart_checkpoint" in block


def test_activity_and_invite_recovery_share_bounded_coordinator_without_parallel_activity_history() -> None:
    # The shared coordinator bounds unrelated startup work, but Discord history
    # reconciliation must remain single-flight. PR #280 removed this narrower
    # lock and allowed two REST-heavy guild scans to overlap.
    assert "_STARTUP_RECONCILE_LOCK = asyncio.Lock()" in ACTIVITY
    assert "async with _STARTUP_RECONCILE_LOCK:" in ACTIVITY
    assert "startup_recovery_slot(" in ACTIVITY
    assert "startup_recovery_slot(" in INVITE
    assert ACTIVITY.index("async with _STARTUP_RECONCILE_LOCK:") < ACTIVITY.index(
        '"activity_restart_reconcile"'
    )

    assert "DANK_STARTUP_RECOVERY_MAX_CONCURRENT" in COORDINATOR
    assert "_MAX_CONCURRENT" in COORDINATOR
    assert "_GUILD_SLOTS" in COORDINATOR
    assert "_GUILD_SLOTS.pop(gid, None)" in COORDINATOR


def test_bulk_discord_recovery_paths_share_process_wide_rest_budget() -> None:
    assert "reserve_recovery_discord_rest_requests(" in ACTIVITY_RECONCILE
    assert "recovery_request_weight(history_limit)" in ACTIVITY_RECONCILE
    assert "archived-public" in ACTIVITY_RECONCILE
    assert "archived-private" in ACTIVITY_RECONCILE

    assert "reserve_recovery_discord_rest_requests(" in INVITE
    assert 'startswith("auto-reconcile:")' in INVITE

    assert "reserve_recovery_discord_rest_requests(" in MEMBERSHIP
    assert "page_size=1000" in MEMBERSHIP

    assert "DANK_RECOVERY_DISCORD_REST_BUDGET_PER_30S" in DISCORD_API_SAFETY
    assert "_RECOVERY_REST_WINDOW_SECONDS = 30.0" in DISCORD_API_SAFETY
    assert "DANK_RECOVERY_DISCORD_REST_BUDGET_PER_30S=100" in ENV_EXAMPLE
    assert "DANK_ACTIVITY_RECONCILE_TIMEOUT_SECONDS=180" in ENV_EXAMPLE


def test_member_wait_timer_startup_reuses_canonical_membership_authority() -> None:
    block = _block(
        KICK_TIMERS,
        "async def _resume_member_wait_timers_from_live_state(",
        "async def member_wait_timer_resume_all()",
    )
    assert "collect_membership_snapshot(guild)" in block
    assert "guild.fetch_members(limit=None)" not in block


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
    assert (
        "previous_heartbeat is not None\n"
        "            and previous_process\n"
        "            and previous_process != _PROCESS_ID"
        not in ACTIVITY
    )
    assert "Legacy tracker rows may predate process_id" in ACTIVITY
    assert "_STARTUP_RECOVERY_HEARTBEATS.setdefault(" in ACTIVITY
    assert "if stored_process == _PROCESS_ID:" in ACTIVITY


def test_invite_surface_never_reads_deprecated_message_interaction() -> None:
    assert 'getattr(message, "interaction_metadata", None)' in SURFACE
    assert 'getattr(message, "interaction", None)' not in SURFACE
    assert '("interaction_metadata", "interaction")' not in SURFACE


def test_restart_does_not_eagerly_warm_every_guild_invite_cache() -> None:
    startup = _block(
        EVENTS,
        "async def _run_startup_once_flags() -> None:",
        "@bot.event\nasync def on_ready()",
    )
    assert "_warm_all_guild_invite_caches()" not in startup
    assert "_invite_cache_warm_started" not in startup

    assert "def _can_fetch_guild_invites" in JOIN_CONTEXT
    assert 'getattr(permissions, "manage_guild", False)' in JOIN_CONTEXT
    assert "def _guild_has_vanity_url" in JOIN_CONTEXT
    assert '"VANITY_URL"' in JOIN_CONTEXT
    assert "if _guild_has_vanity_url(guild):" in JOIN_CONTEXT
    assert "if not baseline_ready:" in JOIN_CONTEXT
    assert "No invite is claimed for this join" in JOIN_CONTEXT


def test_public_startup_scope_has_no_silent_guild_count_cutoff() -> None:
    assert "DANK_STARTUP_MAX_GUILDS" not in APP
    assert "[:max_guilds]" not in APP
    assert "for index, guild in enumerate(guilds):" in APP


def test_ticket_history_backfill_is_opt_in() -> None:
    block = _block(
        APP,
        "async def _maybe_run_ticket_sync_once() -> None:",
        "async def _sync_beta_guild_commands_if_requested",
    )

    assert 'DANK_STARTUP_TICKET_BACKFILL' in block
    assert 'default=False' in block
    assert "set DANK_STARTUP_TICKET_BACKFILL=true only for explicit repair runs" in block


def test_public_startup_config_scope_uses_batched_lookup() -> None:
    assert "async def _configured_runtime_guild_ids(" in APP
    assert "DANK_STARTUP_CONFIG_BATCH_SIZE" in APP
    assert '.select("guild_id")' in APP
    assert '.in_("guild_id",' in APP
    assert "asyncio.to_thread(_read)" in APP


def test_ticket_panel_history_repair_is_explicit_and_coordinated() -> None:
    assert 'DANK_STARTUP_TICKET_PANEL_REPAIR' in PANEL_REPAIR
    assert '_env_true("DANK_STARTUP_TICKET_PANEL_REPAIR", False)' in PANEL_REPAIR
    assert 'startup_recovery_slot(' in PANEL_REPAIR
    assert '"ticket_panel_history_repair"' in PANEL_REPAIR


def test_startup_fanout_uses_fixed_worker_pools() -> None:
    assert "queue: asyncio.Queue[Any] = asyncio.Queue()" in DURABLE_INVITE_STATS
    assert 'name=f"durable-invite-stats-startup-{index}"' in DURABLE_INVITE_STATS
    assert "reconcile_one(guild)" not in DURABLE_INVITE_STATS

    assert "queue: asyncio.Queue[discord.Guild] = asyncio.Queue()" in PANEL_BOOTSTRAP
    assert 'name=f"panel-bootstrap-guild-worker-{index}"' in PANEL_BOOTSTRAP
    assert "_run_one(guild)" not in PANEL_BOOTSTRAP

    assert "queue: asyncio.Queue[discord.Guild] = asyncio.Queue()" in ANTINUKE_INCIDENT
    assert 'name=f"antinuke-security-prewarm-{index}"' in ANTINUKE_INCIDENT
    assert "warm(guild)" not in ANTINUKE_INCIDENT
