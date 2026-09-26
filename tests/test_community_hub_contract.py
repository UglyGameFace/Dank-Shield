from __future__ import annotations

import ast
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
UI = ROOT / "stoney_verify" / "commands_ext" / "public_community_hub.py"
RUNTIME = ROOT / "stoney_verify" / "community_hub_runtime.py"
SERVICE = ROOT / "stoney_verify" / "community_hub_service.py"
GLOBALS = ROOT / "stoney_verify" / "globals.py"
QUEUE = ROOT / "stoney_verify" / "operation_queue.py"
PERMISSIONS = ROOT / "stoney_verify" / "permission_repair_core.py"
MIGRATION = ROOT / "supabase" / "migrations" / "20260925193000_community_hub.sql"
MATCHMAKING_MIGRATION = ROOT / "supabase" / "migrations" / "20260926044000_community_hub_matchmaking_loop.sql"


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_community_hub_python_sources_parse() -> None:
    for path in (UI, RUNTIME, SERVICE, GLOBALS, QUEUE, PERMISSIONS):
        ast.parse(_text(path), filename=str(path))


def test_public_ui_uses_clear_language_not_lfg_jargon() -> None:
    source = _text(UI)
    assert "Find Players" in source
    assert "Start Gaming Session" in source
    assert "Quick Match" in source
    assert re.search(r"\bLFG\b", source, re.IGNORECASE) is None


def test_interactions_use_shared_fail_closed_acknowledgement() -> None:
    source = _text(UI)
    assert "from ..interaction_guard import safe_defer_interaction" in source
    assert 'action_name="community_hub_component"' in source
    assert 'action_name="community_hub_modal_or_private_action"' in source
    assert 'raise RuntimeError("Community Hub interaction acknowledgement failed")' in source


def test_persistent_session_view_is_registered_and_static() -> None:
    ui = _text(UI)
    runtime = _text(RUNTIME)
    assert "class CommunitySessionPublicView(discord.ui.View):" in ui
    assert "super().__init__(timeout=None)" in ui
    assert "self.bot.add_view(CommunitySessionPublicView())" in runtime


def test_component_ids_follow_discord_length_and_uniqueness_contract() -> None:
    source = _text(UI)
    ids = re.findall(r'custom_id\s*=\s*["\']([^"\']+)["\']', source)
    assert ids
    assert all(1 <= len(value) <= 100 for value in ids)
    duplicates = sorted({value for value in ids if ids.count(value) > 1})
    assert not duplicates, f"duplicate Community Hub custom_ids: {duplicates}"


def test_decorated_button_rows_do_not_exceed_five_buttons() -> None:
    tree = ast.parse(_text(UI))
    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue
        counts: dict[int, int] = {}
        for item in node.body:
            if not isinstance(item, (ast.AsyncFunctionDef, ast.FunctionDef)):
                continue
            for decorator in item.decorator_list:
                if not isinstance(decorator, ast.Call):
                    continue
                func = decorator.func
                if not (
                    isinstance(func, ast.Attribute)
                    and func.attr == "button"
                ):
                    continue
                row = 0
                for keyword in decorator.keywords:
                    if keyword.arg == "row" and isinstance(keyword.value, ast.Constant):
                        row = int(keyword.value.value)
                counts[row] = counts.get(row, 0) + 1
        assert all(count <= 5 for count in counts.values()), (
            f"{node.name} exceeds Discord's five-button Action Row limit: {counts}"
        )


def test_close_ui_is_not_session_leave_or_end() -> None:
    tree = ast.parse(_text(UI))
    close_bodies: list[str] = []
    source = _text(UI)
    lines = source.splitlines()
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "close":
            start = node.lineno - 1
            end = getattr(node, "end_lineno", node.lineno)
            close_bodies.append("\n".join(lines[start:end]))
    assert close_bodies
    for body in close_bodies:
        assert "begin_end" not in body
        assert "leave_session" not in body
        assert "delete(" not in body


def test_play_again_creates_a_new_session_instead_of_resurrecting_old_state() -> None:
    service = _text(SERVICE)
    migration = _text(MIGRATION)
    assert "community_hub_restart_session" in service
    assert "parent_session_id" in migration
    assert "only an ended session can be played again" in migration
    assert "insert into public.dank_community_sessions" in migration


def test_managed_resource_cleanup_requires_durable_identity() -> None:
    runtime = _text(RUNTIME)
    migration = _text(MIGRATION)
    assert "ownership_token text not null unique" in migration
    assert "if discord_id <= 0:" in runtime
    assert "Cleanup skipped because durable Discord resource identity is missing." in runtime
    assert "No matching bot-authored Discord audit-log entry was found; resource preserved." in runtime


def test_non_idempotent_discord_creates_and_notifications_are_not_app_retried() -> None:
    runtime = _text(RUNTIME)
    assert "panel_message = await channel.send(" in runtime
    assert "thread = await panel_message.create_thread(" in runtime
    assert "voice = await guild.create_voice_channel(" in runtime
    assert "await user.send(" in runtime
    assert "lambda: channel.send(" not in runtime
    assert "lambda: panel_message.create_thread(" not in runtime
    assert "lambda: guild.create_voice_channel(" not in runtime


def test_thread_from_message_permission_check_matches_discord_route() -> None:
    runtime = _text(RUNTIME)
    permissions = _text(PERMISSIONS)
    region = runtime[runtime.index("def _can_create_public_thread"):runtime.index("class CommunityHubRuntime")]
    assert 'getattr(perms, "send_messages", False)' in region
    assert "create_public_threads" not in region
    assert '"manage_threads"' in permissions


def test_presence_is_privileged_optional_enrichment() -> None:
    globals_source = _text(GLOBALS)
    ui = _text(UI)
    migration = _text(MIGRATION)
    assert 'intents.presences = _env_bool("DANK_ENABLE_PRESENCE_INTENT", False)' in globals_source
    assert "presence_analytics_enabled boolean not null default false" in migration
    assert "Presence is privileged" in ui or "privileged Presence" in ui
    assert "Raw member presence never crosses server boundaries." in ui


def test_session_mutations_use_explicitly_scoped_operation_queue() -> None:
    queue = _text(QUEUE)
    ui = _text(UI)
    assert '"community_session_mutation"' in queue
    assert 'concurrency_class="community_session_mutation"' in ui
    assert "reject_if_busy=True" in ui


def test_notification_delivery_has_server_and_user_safety_limits() -> None:
    migration = _text(MIGRATION)
    runtime = _text(RUNTIME)
    for needle in (
        "notifications_enabled boolean not null default true",
        "max_notifications_per_hour integer not null default 5",
        "notification_cooldown_seconds integer not null default 300",
        "max_notification_targets_per_dispatch integer not null default 50",
        "community_hub_reserve_notification",
    ):
        assert needle in migration
    assert "_notification_semaphore = asyncio.Semaphore(2)" in runtime
    assert "targets[:limit]" in runtime


def test_migration_is_service_role_only_and_rls_protected() -> None:
    source = _text(MIGRATION)
    tables = (
        "dank_community_hub_settings",
        "dank_community_sessions",
        "dank_community_session_members",
        "dank_community_managed_resources",
        "dank_community_session_events",
        "dank_community_metrics_hourly",
        "dank_community_game_metrics_hourly",
        "dank_community_notification_prefs",
        "dank_community_notification_state",
        "dank_community_availability",
        "dank_community_user_blocks",
        "dank_community_reports",
        "dank_community_events",
        "dank_community_event_attendees",
        "dank_community_partner_links",
    )
    for table in tables:
        assert f"create table if not exists public.{table}" in source
        assert f"alter table public.{table} enable row level security" in source
        assert f"revoke all on table public.{table} from public, anon, authenticated" in source
        assert f"grant select, insert, update, delete on table public.{table} to service_role" in source


def test_migration_enforces_core_session_and_partner_invariants() -> None:
    source = _text(MIGRATION)
    assert "primary key (session_id, user_id)" in source
    assert "unique (guild_id, idempotency_key)" in source
    assert "check (guild_a_id < guild_b_id)" in source
    assert "member active-session limit reached" in source
    assert "server active-session limit reached" in source
    assert "minimum players to start has not been reached" in source
    assert "all active players must be ready before start" in source
    assert "session is locked" in source
    assert "host, co-host, or staff authority required" in source


def test_open_to_play_is_expiring_opt_in_not_presence_surveillance() -> None:
    migration = _text(MIGRATION)
    matchmaking = _text(MATCHMAKING_MIGRATION)
    service = _text(SERVICE)
    ui = _text(UI)
    assert "class OpenToPlayModal" in ui
    assert "class OpenToPlayView" in ui
    assert "def _availability_embed" in ui
    assert "create table if not exists public.dank_community_availability" in migration
    assert "expires_at timestamptz not null" in migration
    assert "add column if not exists auto_match boolean not null default false" in matchmaking
    assert "async def set_availability" in service
    assert "async def set_availability_auto_match" in service
    assert "async def delete_expired_availability" in service
    assert "Enable Quick Match" in ui
    assert "separate explicit opt-in" in ui


def test_quick_match_prefers_existing_groups_then_forms_only_opted_in_matches() -> None:
    matchmaking = _text(MATCHMAKING_MIGRATION)
    service = _text(SERVICE)
    runtime = _text(RUNTIME)
    ui = _text(UI)

    assert "p_idempotency_key text" in matchmaking
    assert "idempotency_key=p_idempotency_key" in matchmaking
    assert matchmaking.index("idempotency_key=p_idempotency_key") < matchmaking.index("Existing public groups remain the preferred path")
    assert "and a.auto_match=true" in matchmaking
    assert "match safety exclusion prevents this match" in matchmaking
    assert "for update of a skip locked" in matchmaking
    assert "session.quick_match_formed" in matchmaking
    assert "community_hub_normalize_formation" in matchmaking
    assert "grant execute on function public.community_hub_quick_match(text,text,text,text) to service_role" in matchmaking

    assert "idempotency_key: str" in service
    assert '"p_idempotency_key": key' in service
    assert "async def normalize_session_formation" in service
    assert "session = await hub.normalize_session_formation(session_id, guild_id)" in runtime
    assert 'bot.add_listener(runtime.on_member_remove, "on_member_remove")' in runtime

    assert "availability_summary=availability" in ui
    assert 'custom_id="dank:hub:find:quick:v1"' in ui
    assert 'custom_id="dank:hub:find:available:v1"' in ui
    assert "created_match" in ui
    assert "Quick Match formed a new" in ui


def test_match_safety_is_private_and_enforced_atomically_on_join() -> None:
    migration = _text(MIGRATION)
    service = _text(SERVICE)
    ui = _text(UI)
    assert "class MatchSafetyUserSelect" in ui
    assert "class MatchSafetyView" in ui
    assert "def _match_safety_embed" in ui
    assert "create table if not exists public.dank_community_user_blocks" in migration
    assert "match safety exclusion prevents this join" in migration
    assert "community_hub_set_match_block" in migration
    assert "async def set_match_block" in service
    assert "Match Safety" in ui
    assert "does not kick, ban, timeout" in ui


def test_session_reports_have_private_staff_review_surface() -> None:
    migration = _text(MIGRATION)
    service = _text(SERVICE)
    ui = _text(UI)
    assert "create table if not exists public.dank_community_reports" in migration
    assert "async def submit_report" in service
    assert "async def list_reports" in service
    assert "Report Problem" in ui
    assert "Safety Reports" in ui
    assert "do not automatically punish anyone" in ui


def test_create_and_replay_idempotency_precede_current_quota_checks() -> None:
    migration = _text(MIGRATION)
    create = migration[
        migration.index("create or replace function public.community_hub_create_session"):
        migration.index("create or replace function public.community_hub_join_session")
    ]
    replay = migration[
        migration.index("create or replace function public.community_hub_restart_session"):
        migration.index("create or replace function public.community_hub_set_match_block")
    ]
    assert create.index("idempotency_key = p_idempotency_key") < create.index("server active-session limit reached")
    assert replay.index("idempotency_key = p_idempotency_key") < replay.index("server active-session limit reached")


def test_ready_expiry_covers_initial_and_transferred_hosts() -> None:
    migration = _text(MIGRATION)
    assert "values (v_session.id,p_guild_id,p_host_id,'host',true,now())" in migration
    assert "set role = 'host', ready = true, ready_at = now()" in migration
    assert "when user_id = p_target_user_id then now() else ready_at end" in migration
    assert "community_hub_clear_stale_ready" in migration


def test_analytics_store_aggregate_presence_not_raw_presence_history() -> None:
    migration = _text(MIGRATION)
    service = _text(SERVICE)
    assert "online_presence_peak" in migration
    assert "gaming_presence_peak" in migration
    assert "active_players_peak" in migration
    assert "dank_community_presence_history" not in migration
    assert "subject_user_id" in migration  # operational audit only
    assert "Privacy-preserving aggregate Community Hub metrics" in migration
    assert "analytics_summary" in service
