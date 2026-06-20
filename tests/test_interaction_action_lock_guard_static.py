from pathlib import Path


SOURCE = Path("stoney_verify/startup_guards/interaction_action_lock_guard.py").read_text()
LOADER = Path("stoney_verify/startup_guards/__init__.py").read_text()


def test_action_lock_guard_loaded_after_elite_logger():
    elite = '"stoney_verify.startup_guards.global_interaction_trace_guard"'
    lock = '"stoney_verify.startup_guards.interaction_action_lock_guard"'

    assert elite in LOADER
    assert lock in LOADER
    assert LOADER.find(elite) < LOADER.find(lock)


def test_action_lock_default_mode_is_observe():
    assert 'DANK_SHIELD_INTERACTION_ACTION_LOCK_MODE", "observe"' in SOURCE
    assert 'return "observe"' in SOURCE


def test_action_lock_wraps_view_scheduled_task_only():
    assert "discord.ui.View" in SOURCE
    assert "_scheduled_task" in SOURCE
    assert "_dank_shield_action_lock_wrapped" in SOURCE
    assert "_patch_view_scheduled_task" in SOURCE


def test_action_lock_detects_duplicate_in_flight():
    required = [
        "_ACTIVE_LOCKS",
        "_try_acquire",
        "duplicate_in_flight",
        "_DUPLICATE_COUNTS",
        "_release",
        "This action is already running",
    ]

    for phrase in required:
        assert phrase in SOURCE


def test_action_lock_detects_post_action_cooldown_duplicates():
    required = [
        "DANK_SHIELD_INTERACTION_ACTION_COOLDOWN_SECONDS",
        "_RECENT_RELEASES",
        "_COOLDOWN_COUNTS",
        "_cooldown_seconds",
        "duplicate_cooldown",
        "This action was just used",
    ]

    for phrase in required:
        assert phrase in SOURCE


def test_action_lock_is_not_spam_guard_or_punishment():
    forbidden = [
        "timeout(",
        ".timeout",
        "ban(",
        ".ban",
        "kick(",
        ".kick",
        "delete_messages",
        "purge(",
        "quarantine",
    ]

    lowered = SOURCE.lower()
    for phrase in forbidden:
        assert phrase not in lowered


def test_action_lock_fail_open_behavior_is_visible():
    assert "guard_exception_fail_open" in SOURCE
    assert "intentionally call original" in SOURCE



def test_action_lock_block_mode_is_targeted_not_global():
    required = [
        "DANK_SHIELD_INTERACTION_ACTION_LOCK_BLOCK_TARGETS",
        "_block_targets",
        "_component_targeted_for_block",
        "fnmatchcase",
        "duplicate_blocked",
        "duplicate_allowed_not_targeted",
        "Empty list means observe/log all duplicates but block none.",
    ]

    for phrase in required:
        assert phrase in SOURCE

    assert 'os.getenv("DANK_SHIELD_INTERACTION_ACTION_LOCK_BLOCK_TARGETS", "")' in SOURCE

