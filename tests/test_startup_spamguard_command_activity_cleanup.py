from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPAM = (ROOT / "stoney_verify/spam_guard.py").read_text(encoding="utf-8")
SERVICE = (ROOT / "stoney_verify/startup_guards/setup_service_modes.py").read_text(encoding="utf-8")
COMMANDS = (ROOT / "stoney_verify/commands_ext/__init__.py").read_text(encoding="utf-8")
SLASH = (ROOT / "stoney_verify/startup_guards/slash_command_cleanup.py").read_text(encoding="utf-8")
ACTIVITY = (ROOT / "stoney_verify/members_new/activity_reconciliation.py").read_text(encoding="utf-8")
DOC = (ROOT / "docs/public-production-env.md").read_text(encoding="utf-8")


def _function_source(text: str, name: str) -> str:
    tree = ast.parse(text)
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return ast.get_source_segment(text, node) or ""
    raise AssertionError(f"function {name} not found")


def _literal_set(text: str, name: str) -> set[str]:
    tree = ast.parse(text)
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if any(isinstance(target, ast.Name) and target.id == name for target in node.targets):
            value = node.value
            if isinstance(value, (ast.Set, ast.Tuple, ast.List)):
                return {
                    str(item.value)
                    for item in value.elts
                    if isinstance(item, ast.Constant) and isinstance(item.value, str)
                }
    raise AssertionError(f"literal set {name} not found")


def test_spam_guard_defaults_on_and_missing_rows_are_persisted() -> None:
    defaults = _function_source(SPAM, "_default_settings")
    loader = _function_source(SPAM, "get_spam_settings")
    assert '"enabled": True' in defaults
    assert "row_created_default_on" in loader
    assert "_upsert_settings_sync" in loader
    assert 'source = "db" if row_found else ("db-created" if persisted else "db-empty")' in loader
    assert '@bot.listen("on_guild_join")' in SPAM
    assert "_spam_guard_initialize_new_guild" in SPAM


def test_setup_spam_guard_fallback_matches_runtime_default() -> None:
    defaults = _function_source(SERVICE, "_default_spam_settings")
    normalize = _function_source(SERVICE, "_normalize_spam_settings")
    actual = _function_source(SERVICE, "_load_spam_actual_state")
    assert '"enabled": True' in defaults
    assert 'data.get("spam_blocker_enabled")), True)' in normalize
    assert 'settings.get("spam_blocker_enabled")), True)' in actual


def test_public_command_surface_is_explicitly_nine_and_prune_logs_once() -> None:
    expected = {
        "dank", "mod", "ticket", "tickets", "ticket-intake",
        "ticket-category", "ticket-panel", "verify", "View Dank Profile",
    }
    assert _literal_set(COMMANDS, "_EXPECTED_PUBLIC_TOP_LEVEL_COMMANDS") == expected
    assert "_validate_public_top_level_surface(tree, profile)" in COMMANDS
    assert "_log_runtime_skip_once" in COMMANDS
    assert '_log_runtime_skip_once("dank_children"' in COMMANDS
    assert COMMANDS.count("/dank child prune skipped; stable command surface active") == 1


def test_cleanup_allowlists_match_real_public_dank_surface() -> None:
    expected = {
        "setup", "status", "diagnostics", "protection", "help", "commands",
        "cleanup", "members", "member-logs", "profile", "roles", "design",
    }
    assert _literal_set(COMMANDS, "_ALLOWED_DANK_CHILDREN") == expected
    assert _literal_set(SLASH, "ALLOWED_DANK_CHILDREN") == expected
    assert 'COMMAND_CLEANUP_EPOCH = "2026-07-18-public-surface-v3"' in SLASH


def test_activity_permission_failure_is_actionable_and_bounded() -> None:
    scope = _function_source(ACTIVITY, "audit_guild_activity_scope")
    assert "inactivity cleanup stays review-only" in scope
    assert "View Channel + Read Message History" in scope
    assert "Manage Threads" in scope
    assert "unique[:5]" in scope
    assert "+{extra} more channel/thread permission issue(s)" in scope


def test_production_docs_match_current_public_runtime() -> None:
    assert "final_global=9 final_guild=0 profile=public" in DOC
    assert "View Dank Profile" in DOC
    assert "Spam Guard runtime protection defaults to **On**" in DOC
    assert "Set Up This Step" in DOC
