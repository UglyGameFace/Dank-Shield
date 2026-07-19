from pathlib import Path

SOURCE = Path("stoney_verify/commands_ext/__init__.py").read_text(encoding="utf-8")


def test_prune_skip_logs_are_once_per_startup():
    assert "_STALE_PRUNE_SKIP_LOGGED = False" in SOURCE
    assert "_CHILD_PRUNE_SKIP_LOGGED = False" in SOURCE
    assert "if not _STALE_PRUNE_SKIP_LOGGED:" in SOURCE
    assert "if not _CHILD_PRUNE_SKIP_LOGGED:" in SOURCE


def test_module_loop_still_keeps_runtime_pruning_disabled():
    assert 'DANK_DISABLE_RUNTIME_COMMAND_PRUNE", True' in SOURCE
    assert "after_module_registration" in SOURCE
