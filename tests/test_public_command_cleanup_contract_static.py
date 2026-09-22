from __future__ import annotations

from pathlib import Path

from stoney_verify.command_surface_contract import PUBLIC_DANK_CHILDREN

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = (ROOT / "stoney_verify/command_runtime.py").read_text(encoding="utf-8")
COMMANDS_EXT = (ROOT / "stoney_verify/commands_ext/__init__.py").read_text(encoding="utf-8")
LEGACY_CLEANUP = ROOT / "stoney_verify/startup_guards/slash_command_cleanup.py"
LEGACY_EPOCH = ROOT / "stoney_verify/startup_guards/ticket_panel_command_epoch_guard.py"


def test_retired_global_command_cleanup_guards_stay_absent() -> None:
    assert not LEGACY_CLEANUP.exists()
    assert not LEGACY_EPOCH.exists()


def test_native_command_tree_owns_sync_policy() -> None:
    assert "class DankCommandTree(app_commands.CommandTree)" in RUNTIME
    assert "DANK_SKIP_UNCHANGED_GLOBAL_SYNC" in RUNTIME
    assert "DANK_FORCE_COMMAND_SYNC_ON_BOOT" in RUNTIME
    assert "DANK_COMMAND_SYNC_STATE_FILE" in RUNTIME
    assert "configured_guild_cleanup_ids" in RUNTIME
    assert "should_skip_unchanged_global_sync" in RUNTIME
    assert "remember_global_sync" in RUNTIME


def test_public_command_contract_is_canonical_and_runtime_pruning_is_disabled_by_default() -> None:
    assert PUBLIC_DANK_CHILDREN == frozenset({"home", "purge", "setup", "upload"})
    assert "PUBLIC_DANK_CHILDREN" in RUNTIME
    assert 'DANK_DISABLE_RUNTIME_COMMAND_PRUNE", True' in COMMANDS_EXT
    assert "def _runtime_command_prune_disabled()" in COMMANDS_EXT
