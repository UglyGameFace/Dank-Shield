from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BROWSER = ROOT / "stoney_verify" / "commands_ext" / "public_member_role_browser.py"
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
    for path in (BROWSER, COMMON, REVIEW_PANEL, ACTIONS, BULK, ROSTER, REVIEW, CENTER):
        ast.parse(_source(path))


def test_browse_command_attaches_to_existing_members_group() -> None:
    source = _source(BROWSER)
    review = _source(REVIEW)
    assert "from .public_members_group import members_group" in source
    assert '@members_group.command(\n            name="browse"' in source
    assert "async def browse_members" in source
    assert "Optional[discord.Role]" in source
    assert "register_public_member_role_browser_commands(bot, tree)" in review


def test_browser_has_role_picker_pagination_search_and_sort() -> None:
    source = _source(ROSTER) + "\n" + _source(BROWSER)
    assert "class BrowserRoleSelect(discord.ui.RoleSelect)" in source
    assert "_BROWSER_PAGE_SIZE = 20" in source
    assert "class MemberRosterSelect(discord.ui.Select)" in source
    assert "class BrowserSortSelect(discord.ui.Select)" in source
    assert "class MemberSearchModal" in source
    assert 'label="Previous"' in source
    assert 'label="Next"' in source
    assert 'label="Refresh"' in source


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
    assert "class MemberDestructiveActionModal" in source
    assert "Confirmation did not match" in source
    assert "class MemberTimeoutModal" in source
    assert "record_member_action" in source
    assert source.count("if not await require_review(interaction):") >= 3


def test_verify_reuses_owned_basic_verification_service_without_bypassing_protected_modes() -> None:
    source = _source(COMMON) + "\n" + _source(ACTIONS)
    assert "from stoney_verify.verification_new.basic_verify import apply_basic_verification" in source
    assert "from stoney_verify.setup_engine.verification_modes import effective_verification_mode" in source
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
    assert 'action_lock(interaction.guild.id, member.id, "bulk_dm")' in source
    assert 'action_lock(interaction.guild.id, target.id, f"bulk_{self.parent_view.action}")' in source


def test_setup_center_links_to_role_browser() -> None:
    source = _source(CENTER)
    assert 'label="Browse by Role"' in source
    assert "public_member_role_browser as browser" in source
    assert "await browser._open_member_browser(interaction)" in source


def test_review_feedback_safety_contract_remains_non_enforcing() -> None:
    source = _source(REVIEW)
    start = source.index('if "review" not in existing:')
    end = source.index('if "history" not in existing:', start)
    block = source[start:end]
    assert "build_member_review_view" in block
    assert "_build_member_context_fields" in block
    assert "previous_feedback" in block
    assert "source_key" in block
    for forbidden in (".ban(", ".kick(", ".timeout(", ".add_roles(", ".remove_roles("):
        assert forbidden not in source


def test_component_rows_stay_inside_discord_limits() -> None:
    source = "\n".join(_source(path) for path in (ROSTER, BULK, ACTIONS))
    tree = ast.parse(source)
    rows: list[int] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.keyword) and node.arg == "row" and isinstance(node.value, ast.Constant):
            if isinstance(node.value.value, int):
                rows.append(node.value.value)
    assert rows
    assert min(rows) >= 0
    assert max(rows) <= 4
