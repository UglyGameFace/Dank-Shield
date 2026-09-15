from __future__ import annotations

import subprocess
import sys


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
