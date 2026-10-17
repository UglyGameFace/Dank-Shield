"""Historical member-scan parity gates.

Keep current native implementations instead of restoring the stale phase2/phase3
branch snapshots. These tests deliberately validate behavior through source
contracts without importing the Discord bot or starting services.
"""
from pathlib import Path
import ast

ROOT = Path(__file__).resolve().parents[1]
SERVICE = ROOT / "stoney_verify/members_new/activity_service.py"
UI = ROOT / "stoney_verify/commands_ext/public_members_group.py"


def test_recent_discord_evidence_is_integrated_into_native_scan() -> None:
    source = SERVICE.read_text(encoding="utf-8")
    tree = ast.parse(source)
    names = {node.name for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
    assert "_load_recent_discord_evidence" in names
    assert "scan_inactive_members" in names
    assert "recent_signals, recent_warnings, recent_read, recent_attempted" in source
    assert "activity_signals.setdefault(int(uid), []).extend(signals)" in source
    assert "if recent_attempted > 0:" in source
    assert "if recent_read > 0:" in source


def test_recent_evidence_is_bounded_and_distinguishes_log_evidence() -> None:
    source = SERVICE.read_text(encoding="utf-8")
    assert "_RECENT_EVIDENCE_LOOKBACK_DAYS = 180" in source
    assert "_RECENT_EVIDENCE_MAX_CHANNELS = 75" in source
    assert "_RECENT_EVIDENCE_PER_CHANNEL_LIMIT = 100" in source
    assert "_RECENT_EVIDENCE_CHANNEL_CONCURRENCY = 4" in source
    assert "Active/recent evidence" in source


def test_mobile_review_shows_found_users_and_review_controls() -> None:
    source = UI.read_text(encoding="utf-8")
    tree = ast.parse(source)
    names = {node.name for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
    assert "_build_report_embed" in names
    assert "_build_page_users_text" in names
    assert "_page_bounds" in names
    assert 'name=f"Users Found — Page {page + 1}/{pages}"' in source
    assert "Select a user for details, lock/skip after review" in source
    assert "No users found" in source


def test_historical_recent_evidence_module_not_required_at_runtime() -> None:
    # The newer evidence implementation lives in activity_service.py.
    assert not (ROOT / "stoney_verify/members_new/recent_evidence_service.py").exists()
