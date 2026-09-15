from __future__ import annotations

import importlib.util
import subprocess
import sys


RETIRED_GUARD = "stoney_verify.startup_guards.interaction_action_lock_guard"


def test_retired_scheduler_guard_has_no_startup_or_package_owner() -> None:
    from stoney_verify.startup_diagnostics import EXPECTED_STARTUP_OWNER_MODULES
    from stoney_verify.startup_guards import LEGACY_DORMANT_STARTUP_GUARDS

    assert RETIRED_GUARD not in EXPECTED_STARTUP_OWNER_MODULES
    assert RETIRED_GUARD not in LEGACY_DORMANT_STARTUP_GUARDS
    assert importlib.util.find_spec(RETIRED_GUARD) is None


def test_native_interaction_owner_does_not_patch_discord_view_scheduler() -> None:
    code = """
import discord

before = discord.ui.View._scheduled_task
import stoney_verify.interaction_guard  # noqa: F401
after = discord.ui.View._scheduled_task

assert after is before
assert not hasattr(discord.ui.View, \"_dank_shield_action_lock_wrapped\")
assert not hasattr(discord.ui.View, \"_dank_shield_action_lock_original_scheduled_task\")
"""
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
