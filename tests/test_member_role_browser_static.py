from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BROWSER = ROOT / "stoney_verify" / "commands_ext" / "public_member_role_browser.py"
CENTER_UI = ROOT / "stoney_verify" / "commands_ext" / "member_command_center.py"
COMMAND_HUB = ROOT / "stoney_verify" / "commands_ext" / "public_command_hub.py"
COMMANDS = ROOT / "stoney_verify" / "commands.py"
COMMON = ROOT / "stoney_verify" / "commands_ext" / "member_role_browser_common.py"
REVIEW_PANEL = ROOT / "stoney_verify" / "commands_ext" / "member_role_browser_review.py"
ACTIONS = ROOT / "stoney_verify" / "commands_ext" / "member_role_browser_actions.py"
BULK = ROOT / "stoney_verify" / "commands_ext" / "member_role_browser_bulk.py"
ROSTER = ROOT / "stoney_verify" / "commands_ext" / "member_role_browser_roster.py"
REVIEW = ROOT / "stoney_verify" / "commands_ext" / "public_member_review_feedback.py"
CENTER = ROOT / "stoney_verify" / "members_cleanup_center_services.py"


def _source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_sources_parse() -> None:
    for path in (
        BROWSER,
        CENTER_UI,
        COMMAND_HUB,
        COMMANDS,
        COMMON,
        REVIEW_PANEL,
        ACTIONS,
        BULK,
        ROSTER,
        REVIEW,
        CENTER,
    ):
        ast.parse(_source(path))


def test_one_members_command_replaces_visible_subcommand_group() -> None:
    source = _source(BROWSER)
    review = _source(REVIEW)
    assert "from .public_setup_group import dank_group" in source
    assert 'dank_group.get_command("members")' in source
    assert "isinstance(existing, app_commands.Group)" in source
    assert 'dank_group.remove_command("members")' in source
    assert '@dank_group.command(\n            name="members"' in source
    assert "open_member_command_center" in source
    assert 'name="browse"' not in source
    assert "register_public_member_role_browser_commands(bot, tree)" in review


def test_members_entry_survives_final_dank_compaction() -> None:
    browser = _source(BROWSER)
    hub = _source(COMMAND_HUB)
    commands = _source(COMMANDS)
    assert "def restore_member_command_after_compaction" in browser
    assert 'for name in ("setup", "status", "diagnostics", "members")' in hub
    assert 'await _invoke_saved("members", interaction)' in hub
    assert "from .public_members_group import members_scan" not in hub
    assert commands.count("restore_member_command_after_compaction(") >= 2
    assert "compact_public_dank_surface(bot, bot.tree)\n    restore_member_command_after_compaction(bot, bot.tree)" in commands


def test_member_center_has_button_driven_categories() -> None:
    source = _source(CENTER_UI)
    assert "class MemberCommandCenterView(OwnedView)" in source
    assert 'label="Live Members"' in source
    assert 'label="Activity & Cleanup"' in source
    assert 'label="Intelligence"' in source
    assert 'label="Operations & Safety"' in source
    assert "class LiveMembersMenuView" in source
    assert "class ActivityCleanupMenuView" in source
    assert "class IntelligenceMenuView" in source
    assert "class OperationsSafetyMenuView" in source


def test_browser_has_role_picker_pagination_search_and_sort() -> None:
    source = _source(ROSTER) + "\n" + _source(CENTER_UI)
    assert "class CenterRoleSelect(discord.ui.RoleSelect)" in source
    assert "_BROWSER_PAGE_SIZE = 20" in source
    assert "class MemberRosterSelect(discord.ui.Select)" in source
    assert "class BrowserSortSelect(discord.ui.Select)" in source
    assert "class BrowserFilterSelect(discord.ui.Select)" in source
    assert "class CenterQuickRoleButton(discord.ui.Button)" in source
    assert "class DirectMemberSelect(discord.ui.UserSelect)" in source
    assert "class MemberSearchModal" in source
    assert 'label="Previous"' in source
    assert 'label="Next"' in source
    assert 'label="Refresh"' in source
    assert 'label="Command Center"' in source


def test_internal_member_tools_are_reused_behind_ui() -> None:
    source = _source(CENTER_UI)
    assert "async def _invoke_command" in source
    assert "from .public_members_group import _run_activity_scan" in source
    assert "from .public_members_group import members_locked" in source
    assert "from .public_members_group import members_notices" in source
    assert "from .public_members_group import members_coverage" in source
    assert "from .public_members_cleanup_group import members_cleanup_user" in source
    assert "from .public_members_cleanup_group import members_cleanup_queue" in source
    assert "from .public_members_cleanup_group import members_purge_all" in source
    assert "from .public_members_cleanup_group import members_cleanup_settings" in source
    assert "from .public_member_review_feedback import review_history" in source
    assert "open_review_panel" in source


def test_browser_modules_are_wired_without_private_cross_module_calls() -> None:
    common = _source(COMMON)
    roster = _source(ROSTER)
    bulk = _source(BULK)
    assert "async def ensure_member_cache" in common
    assert "ensure_member_cache(interaction.guild)" in roster
    assert "from .member_role_browser_bulk import BulkSelectView" in roster
    assert "MemberActionView, member_detail_embed" in roster
    assert "self.browser.render_embed()" in bulk
    assert "_role_browser_embed" not in bulk


def test_member_actions_are_guarded_and_confirmed() -> None:
    source = _source(COMMON) + "\n" + _source(ACTIONS)
    assert "def action_blockers(" in source
    assert "def role_action_blockers(" in source
    assert "target.top_role >= actor.top_role" in source
    assert "target.top_role >= me.top_role" in source
    assert "async def load_protected_role_ids" in source
    assert (
        "Configured staff/control roles require the server owner or an Administrator."
        in source
    )
    assert "class MemberDestructiveActionModal" in source
    assert "Confirmation did not match" in source
    assert "class MemberTimeoutModal" in source
    assert "record_member_action" in source
    assert source.count("if not await require_review(interaction):") >= 3


def test_verify_reuses_owned_basic_verification_service_without_bypassing_protected_modes() -> None:
    source = _source(COMMON) + "\n" + _source(ACTIONS)
    assert "stoney_verify.verification_new.basic_verify" in source
    assert "apply_basic_verification" in source
    assert "stoney_verify.setup_engine.verification_modes" in source
    assert "effective_verification_mode" in source
    assert 'if mode != "basic_button":' in source
    assert "protected ID/ticket verification flow" in source
    assert "unverified_role not in target.roles" in source
    assert "ok, message = await apply_basic_verification(target)" in source


def test_bulk_tools_exclude_mass_punishment() -> None:
    source = _source(BULK)
    bulk_start = source.index("class BulkActionView")
    bulk_end = source.index("class BulkReminderModal")
    bulk = source[bulk_start:bulk_end]
    assert 'label="Send Reminder"' in bulk
    assert 'label="Add Role"' in bulk
    assert 'label="Remove Role"' in bulk
    assert 'label="Kick"' not in bulk
    assert 'label="Ban"' not in bulk
    assert 'label="Timeout"' not in bulk
    assert "if not await require_review(interaction):" in source
    assert '"bulk_dm"' in source
    assert "member.id" in source
    assert 'f"bulk_{self.parent_view.action}"' in source
    assert "async with action_lock(" in source
    assert "protected_role_ids = await load_protected_role_ids" in source
    assert "blockers = await role_action_blockers(" in source


def test_setup_center_links_to_role_browser_without_command_slot() -> None:
    source = _source(CENTER)
    browser = _source(BROWSER)
    assert 'label="Browse by Role"' in source
    assert "public_member_role_browser as browser" in source
    assert "await browser._open_member_browser(interaction)" in source
    assert "without exposing another slash subcommand" in browser


def test_review_feedback_safety_contract_remains_non_enforcing() -> None:
    source = _source(REVIEW)
    start = source.index('if "review" not in existing:')
    end = source.index('if "history" not in existing:', start)
    block = source[start:end]
    assert "build_member_review_view" in block
    assert "_build_member_context_fields" in block
    assert "previous_feedback" in block
    assert "source_key" in block
    for forbidden in (
        ".ban(",
        ".kick(",
        ".timeout(",
        ".add_roles(",
        ".remove_roles(",
    ):
        assert forbidden not in source


def test_component_rows_stay_inside_discord_limits() -> None:
    source = "\n".join(
        _source(path)
        for path in (CENTER_UI, ROSTER, BULK, ACTIONS)
    )
    tree = ast.parse(source)
    rows: list[int] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.keyword)
            and node.arg == "row"
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, int)
        ):
            rows.append(node.value.value)
    assert rows
    assert min(rows) >= 0
    assert max(rows) <= 4
